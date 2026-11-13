from __future__ import annotations

import time
from dataclasses import replace

import app.main as main
import app.playback as playback
import app.security as security
import app.vlm_api as api
import pytest
from fastapi.testclient import TestClient
from ultra_rules.vlm.storage import write_json


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(security, "settings", replace(security.settings, api_token="secret"))
    monkeypatch.setattr(playback, "_active", set())
    return TestClient(main.app, headers={"authorization": "Bearer secret"})


def _upload(folder, name="source.mp4", payload=b"\x00" * 4096):
    folder.mkdir(parents=True)
    (folder / name).write_bytes(payload)
    write_json(folder / "video.json", {"videoId": folder.name, "sourceName": name, "sizeBytes": len(payload)})


def test_playback_proxy_lifecycle(client, monkeypatch):
    folder = api.DATA_ROOT / "uploads" / "vid1"
    _upload(folder)

    assert client.get("/api/vlm/videos/vid1/playback").json()["state"] == "missing"
    assert client.get("/api/vlm/videos/vid1/playback/source").status_code == 409

    def fake_run(video_id, source, target, status_path):
        target.write_bytes(b"proxy")
        write_json(status_path, {"videoId": video_id, "state": "ready", "progress": 100, "bytes": 5,
                                 "version": playback.VERSION})
        with playback._lock:
            playback._active.discard(video_id)

    monkeypatch.setattr(playback, "_run", fake_run)
    started = client.post("/api/vlm/videos/vid1/playback")
    assert started.status_code == 202
    assert started.json()["state"] in {"running", "ready"}

    for _ in range(100):
        status = client.get("/api/vlm/videos/vid1/playback").json()
        if status["state"] == "ready":
            break
        time.sleep(0.02)
    assert status["state"] == "ready"
    assert status["progress"] == 100

    source = client.get("/api/vlm/videos/vid1/playback/source")
    assert source.status_code == 200
    assert source.content == b"proxy"
    assert source.headers["cache-control"] == "private, max-age=3600"
    # The proxy is served once and reused, never regenerated on demand.
    assert client.post("/api/vlm/videos/vid1/playback").json()["state"] == "ready"


def test_playback_proxy_is_capped_for_very_long_recordings(client, monkeypatch):
    folder = api.DATA_ROOT / "uploads" / "long"
    _upload(folder)
    write_json(folder / "video.json", {"videoId": "long", "sourceName": "source.mp4",
                                       "sizeBytes": 4096, "durationSec": 24 * 3600})
    response = client.post("/api/vlm/videos/long/playback")
    assert response.status_code == 409
    assert "VLM_PLAYBACK_MAX_DURATION_SEC" in response.text
    # Raising the cap (0 = unlimited) lets an operator build the proxy explicitly.
    monkeypatch.setattr(playback, "MAX_DURATION_SEC", 0)
    monkeypatch.setattr(playback, "_run", lambda video_id, *_: playback._active.discard(video_id))
    assert client.post("/api/vlm/videos/long/playback").status_code == 202


def test_playback_reports_interrupted_workers(client):
    folder = api.DATA_ROOT / "uploads" / "vid2"
    _upload(folder)
    write_json(folder / "playback.json", {"videoId": "vid2", "state": "running", "progress": 40})
    status = client.get("/api/vlm/videos/vid2/playback").json()
    assert status["state"] == "failed"
    assert "interrupted" in (status["error"] or "").lower()


def test_playback_rejects_unknown_and_invalid_ids(client):
    assert client.get("/api/vlm/videos/does-not-exist/playback").status_code == 404
    assert client.post("/api/vlm/videos/bad.id/playback").status_code == 400
    assert client.get("/api/vlm/videos/vid1/playback").status_code == 404


def test_stale_proxy_version_is_rebuilt(client):
    folder = api.DATA_ROOT / "uploads" / "vid3"
    _upload(folder)
    (folder / "playback.mp4").write_bytes(b"old")
    write_json(folder / "playback.json", {"videoId": "vid3", "state": "ready", "progress": 100, "version": 0})
    assert client.get("/api/vlm/videos/vid3/playback").json()["state"] == "missing"
