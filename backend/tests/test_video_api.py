from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import replace
from fractions import Fraction

import app.main as main
import app.security as security
import app.video_api as api
import av
import numpy as np
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "ROOT", tmp_path / "previews")
    monkeypatch.setattr(api, "CACHE_ROOT", tmp_path / "saved-previews")
    monkeypatch.setattr(api, "jobs", {})
    monkeypatch.setattr(security, "settings", replace(security.settings, api_token="secret"))
    with TestClient(main.app, headers={"authorization": "Bearer secret"}) as client:
        yield client
        for job in list(api.jobs.values()):
            job["cancel"].set()
        deadline = time.monotonic() + 5
        while any(j["state"] in {"uploading", "converting"} for j in api.jobs.values()) and time.monotonic() < deadline:
            time.sleep(.01)


def wait_for_preview(client, identifier):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = client.get(f"/api/video/previews/{identifier}").json()
        if result["state"] in {"ready", "failed"}:
            return result
        time.sleep(.01)
    pytest.fail("Conversion did not finish")


def make_surveillance_video(path):
    # Deliberately misnamed MPEG-TS/HEVC with a nonzero, variable timestamp timeline.
    with av.open(str(path), "w", format="mpegts") as output:
        stream = output.add_stream("libx265", rate=25)
        stream.width, stream.height = 320, 192
        stream.pix_fmt = "yuv420p"
        stream.codec_context.time_base = Fraction(1, 90000)
        stream.options = {"x265-params": "log-level=error:pools=1:frame-threads=1"}
        for i in range(30):
            frame = av.VideoFrame.from_ndarray(np.full((192, 320, 3), i * 5, dtype=np.uint8), format="rgb24")
            frame.pts = 900000 + i * 3600 + (1800 if i >= 15 else 0)
            frame.time_base = Fraction(1, 90000)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


@pytest.mark.skipif(os.getenv("ULTRA_TEST_GPU_VIDEO") != "1", reason="Set ULTRA_TEST_GPU_VIDEO=1 on NVIDIA hardware")
def test_hevc_gpu_timeline_auth_range_and_cleanup(client, tmp_path):
    source = tmp_path / "surveillance.mp4"
    make_surveillance_video(source)
    assert TestClient(main.app).post("/api/video/previews").status_code == 401
    response = client.post("/api/video/previews", files={"file": (source.name, source.read_bytes(), "video/mp4")})
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    assert wait_for_preview(client, identifier) == {"state": "ready", "progress": 100, "error": None}
    job = api.jobs[identifier]
    assert not (job["folder"] / "source").exists()
    with av.open(str(job["preview"])) as video:
        assert video.streams.video[0].codec_context.name == "h264"
        assert video.streams.video[0].codec_context.pix_fmt == "yuv420p"
        times = [frame.time for frame in video.decode(video=0)]
    assert len(times) == 30
    assert times[0] == 0
    assert times[15] - times[14] == pytest.approx(.06)
    assert times[-1] == pytest.approx(1.18)
    response = client.get(f"/api/video/previews/{identifier}/source", headers={"range": "bytes=0-99"})
    assert response.status_code == 206
    assert response.headers["content-type"] == "video/mp4"
    assert len(response.content) == 100
    assert client.delete(f"/api/video/previews/{identifier}").status_code == 200
    assert not job["folder"].exists()
    assert job["preview"].exists()
    assert client.get(f"/api/video/previews/{identifier}").status_code == 404


def test_saved_preview_reused_after_release_restart_and_expiry(client, monkeypatch):
    conversions = []

    def fake_transcode(source, target, cancel, progress):
        conversions.append(source.read_bytes())
        target.write_bytes(b"finished-preview")

    monkeypatch.setattr(api, "transcode", fake_transcode)

    def upload(name, content):
        response = client.post("/api/video/previews", files={"file": (name, content)})
        assert response.status_code == 202
        identifier = response.json()["id"]
        assert wait_for_preview(client, identifier)["state"] == "ready"
        return identifier

    first = upload("监控.mp4", b"original")
    saved = api.jobs[first]["preview"]
    assert saved.parent == api.CACHE_ROOT
    assert saved.stem == hashlib.sha256(b"original").hexdigest()
    metadata = json.loads(saved.with_suffix(".json").read_text())
    assert metadata["originalFilename"] == "监控.mp4"
    assert client.delete(f"/api/video/previews/{first}").status_code == 200
    assert saved.read_bytes() == b"finished-preview"
    # A fresh in-memory registry simulates restarting the backend.
    monkeypatch.setattr(api, "jobs", {})
    second = upload("renamed.mkv", b"original")
    assert conversions == [b"original"]
    assert api.jobs[second]["preview"] == saved
    assert client.get(f"/api/video/previews/{second}/source").content == b"finished-preview"
    api.jobs[second]["finished"] = time.time() - 90000
    third = upload("监控.mp4", b"different")
    assert second not in api.jobs
    assert saved.exists()
    assert api.jobs[third]["preview"] != saved
    assert conversions == [b"original", b"different"]
    # Removing an archive manually must not leave a permanently broken cache hit.
    saved.unlink()
    upload("监控.mp4", b"original")
    assert conversions == [b"original", b"different", b"original"]
    assert list(api.ROOT.iterdir()) == []
    assert list(api.CACHE_ROOT.glob("*.tmp")) == []


def test_invalid_and_oversize_video(client, monkeypatch):
    response = client.post("/api/video/previews", files={"file": ("bad.mp4", b"invalid")})
    identifier = response.json()["id"]
    assert wait_for_preview(client, identifier)["state"] == "failed"
    assert not api.jobs[identifier]["folder"].exists()
    monkeypatch.setattr(api, "MAX_BYTES", 4)
    assert client.post("/api/video/previews", files={"file": ("large.mp4", b"12345")}).status_code == 413
    assert list(api.ROOT.iterdir()) == []
    assert not api.CACHE_ROOT.exists()


def test_cancel_and_concurrency(client, monkeypatch):
    started = threading.Event()

    def slow_transcode(source, target, cancel, progress):
        started.set()
        cancel.wait(5)
        raise InterruptedError()

    monkeypatch.setattr(api, "transcode", slow_transcode)
    response = client.post("/api/video/previews", files={"file": ("clip.mp4", b"video")})
    identifier = response.json()["id"]
    assert started.wait(2)
    assert client.get(f"/api/video/previews/{identifier}/source").status_code == 409
    assert client.post("/api/video/previews", files={"file": ("clip.mp4", b"video")}).status_code == 409
    assert client.delete(f"/api/video/previews/{identifier}").status_code == 200
    deadline = time.monotonic() + 5
    while identifier in api.jobs and time.monotonic() < deadline:
        time.sleep(.01)
    assert identifier not in api.jobs
    assert list(api.ROOT.iterdir()) == []


def test_worker_failure_is_reported_without_cpu_retry(client, monkeypatch, tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text("import os\nimport sys\nprint('NVENC unavailable', file=sys.stderr, flush=True)\nos._exit(17)\n")
    monkeypatch.setattr(api, "WORKER", worker)
    response = client.post("/api/video/previews", files={"file": ("clip.mp4", b"video")})
    identifier = response.json()["id"]
    status = wait_for_preview(client, identifier)
    assert status["state"] == "failed"
    assert "CPU fallback disabled" in status["error"]
    assert "exited (17)" in status["error"]
    assert "NVENC unavailable" in status["error"]
    assert not api.jobs[identifier]["folder"].exists()


def test_cancel_terminates_actual_worker(monkeypatch, tmp_path):
    worker = tmp_path / "worker.py"
    marker = tmp_path / "started"
    worker.write_text(
        "import os, sys, time\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "print(37, flush=True)\ntime.sleep(60)\n"
    )
    monkeypatch.setattr(api, "WORKER", worker)
    cancel = threading.Event()
    progress = []
    errors = []

    def run():
        try:
            api.transcode(marker, tmp_path / "preview.mp4", cancel, progress.append)
        except InterruptedError:
            errors.append("cancelled")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    try:
        while not progress and time.monotonic() < deadline:
            time.sleep(.01)
        assert progress == [37]
    finally:
        cancel.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == ["cancelled"]
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)


def test_unsupported_codec_fails_instead_of_software_decode(tmp_path):
    source = tmp_path / "lossless.mkv"
    with av.open(str(source), "w") as output:
        stream = output.add_stream("ffv1", rate=25)
        stream.width, stream.height = 320, 192
        stream.pix_fmt = "yuv420p"
        frame = av.VideoFrame.from_ndarray(np.zeros((192, 320, 3), dtype=np.uint8), format="rgb24")
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    with pytest.raises(RuntimeError, match="no NVIDIA decoder: ffv1"):
        api.transcode(source, tmp_path / "preview.mp4", threading.Event(), lambda value: None)
    assert not (tmp_path / "preview.mp4").exists()
