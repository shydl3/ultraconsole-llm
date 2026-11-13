from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import app.main as main
import app.security as security
import app.vlm_api as api
import app.vlm_jobs as jobs
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from ultra_rules.vlm.runner import process_identity
from ultra_rules.vlm.storage import write_json


@pytest.fixture()
def client(tmp_path, monkeypatch, local_vlm_models):
    monkeypatch.setattr(api, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(security, "settings", replace(security.settings, api_token="secret"))
    return TestClient(main.app, headers={"authorization": "Bearer secret"})


def upload(client, tmp_path):
    path = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25, (96, 64))
    for _ in range(25):
        writer.write(np.zeros((64, 96, 3), np.uint8))
    writer.release()
    result = client.post("/api/vlm/videos", files={"file": ("clip.mp4", path.read_bytes(), "video/mp4")})
    assert result.status_code == 200, result.text
    return result.json()["videoId"]


def test_upload_start_results_auth_and_range(client, tmp_path, monkeypatch, local_vlm_models):
    vid = upload(client, tmp_path)
    def fake_launch(folder, python):
        assert jobs.read_json(folder / 'request.json')['modelPath'] == str(local_vlm_models[1])
        state = json.loads((folder / "status.json").read_text())
        write_json(folder / "status.json", {**state, "state": "completed"})
        (folder / "results.jsonl").write_text(json.dumps({"trackId": 7, "decision": "uncertain", "eventDefinitionId": "e",
                                                        "videoDescription": "目标走向门框，随后被旁人遮挡。", "reason": "后续动作看不清。"}) + "\n")
    monkeypatch.setattr(jobs, "launch", fake_launch)
    payload = {"videoId": vid, "pois": [{"id": "gate", "kind": "line", "points": [[.2, .2], [.8, .8]]}],
               "events": [{"id": "e", "description": "test"}], "modelPath": str(local_vlm_models[1])}
    response = client.post("/api/vlm/runs", json=payload)
    assert response.status_code == 200, response.text
    run = response.json()["runId"]
    assert client.get(f"/api/vlm/runs/{run}").json()["state"] == "completed"
    results = client.get(f"/api/vlm/runs/{run}/results?trackId=7").json()
    assert results["total"] == 1
    assert results["items"][0]["videoDescription"] == "目标走向门框，随后被旁人遮挡。"
    assert client.get(f"/api/vlm/runs/{run}/results?trackId=8").json()["total"] == 0
    assert client.get(f"/api/vlm/videos/{vid}/source", headers={"range": "bytes=0-99"}).status_code == 206
    assert TestClient(main.app).get("/api/vlm/runs").status_code == 401
    assert client.get(f"/api/vlm/runs/{run}/artifacts/..%2frequest.json").status_code in (400, 404)
    assert client.post(f"/api/vlm/runs/{run}/cancel").json()["state"] == "completed"


def test_results_facets_markers_and_filters(client):
    folder = api.DATA_ROOT / "runs/facets"
    folder.mkdir(parents=True)
    write_json(folder / "status.json", {"runId": "facets", "state": "completed"})
    rows = [
        {"resultId": "r1", "trackId": 3, "triggerSec": 12.0, "decision": "match", "inferenceStatus": "succeeded",
         "eventDefinitionId": "enter", "description": "行人进入安检区", "reason": "目标清晰"},
        {"resultId": "r2", "trackId": 3, "triggerSec": 4.0, "decision": "no_match", "inferenceStatus": "succeeded",
         "eventDefinitionId": "enter", "description": "书包跨线", "reason": "没有人物"},
        {"resultId": "r3", "trackId": 8, "triggerSec": 20.0, "decision": "uncertain", "inferenceStatus": "failed",
         "eventDefinitionId": "retreat", "description": "目标退回", "reason": "推理失败",
         "downgradeReasons": ["evidence_time_outside_window"]},
        # Older rows may omit decision fields entirely; they must not break facets.
        {"resultId": "r4", "trackId": 9, "triggerSec": 1.0, "inferenceStatus": "succeeded", "description": "历史结果"},
    ]
    (folder / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))

    page = client.get("/api/vlm/runs/facets/results?limit=2").json()
    assert page["total"] == 4
    assert [row["resultId"] for row in page["items"]] == ["r4", "r2"]  # chronological, paginated
    assert page["facets"]["decision"] == {"match": 1, "no_match": 1, "uncertain": 1}
    assert page["facets"]["inferenceStatus"] == {"succeeded": 3, "failed": 1}
    assert page["facets"]["eventDefinitionId"] == {"enter": 2, "retreat": 1}
    assert page["facets"]["trackId"] == {"3": 2, "8": 1, "9": 1}
    assert page["facets"]["flagged"] == 1
    assert page["facets"]["total"] == 4
    assert len(page["markers"]) == 4  # markers cover every hit, not just the page
    assert page["markers"][0]["resultId"] == "r4"
    assert client.get("/api/vlm/runs/facets/results?decision=match").json()["total"] == 1
    # The "all" chip reports what it would show after clearing the verdict filter.
    assert client.get("/api/vlm/runs/facets/results?decision=match").json()["facets"]["total"] == 4
    assert client.get("/api/vlm/runs/facets/results?inferenceStatus=failed").json()["total"] == 1
    assert client.get("/api/vlm/runs/facets/results?flagged=true").json()["total"] == 1
    assert client.get("/api/vlm/runs/facets/results?q=书包").json()["total"] == 1
    assert client.get("/api/vlm/runs/facets/results?q=3").json()["total"] == 2  # matches trackId
    filtered = client.get("/api/vlm/runs/facets/results?decision=no_match").json()
    # A rule facet ignores its own dimension so other rules stay selectable.
    assert filtered["facets"]["eventDefinitionId"] == {"enter": 1}
    paged = client.get("/api/vlm/runs/facets/results?eventId=enter&offset=1&limit=1").json()
    assert paged["total"] == 2
    assert [row["resultId"] for row in paged["items"]] == ["r1"]
    tracked = client.get("/api/vlm/runs/facets/results?sort=track&order=desc&limit=2").json()
    assert [row["resultId"] for row in tracked["items"]] == ["r4", "r3"]


def test_description_output_budget_is_exposed_and_validated(client):
    contract = client.get('/api/vlm/parameters').json()
    assert contract['defaults']['maxNewTokens'] == 1024
    assert contract['fields']['maxNewTokens']['maximum'] == 2048
    assert jobs.AnalysisParameters(maxNewTokens=512).maxNewTokens == 512  # Existing preferences remain valid.
    assert jobs.AnalysisParameters(maxNewTokens=2048).maxNewTokens == 2048
    with pytest.raises(ValueError):
        jobs.AnalysisParameters(maxNewTokens=2049)


def test_invalid_model_is_rejected_before_worker_or_run_creation(client, tmp_path, monkeypatch, local_vlm_models):
    vid = upload(client, tmp_path)
    def no_launch(*args):
        pytest.fail('Bad model must not start tracking')
    monkeypatch.setattr(jobs, 'launch', no_launch)
    path = local_vlm_models[1]
    # A sharded download with one missing shard is not ready for submission.
    (path / 'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'a': 'missing.safetensors'}}))
    option = next(model for model in client.get('/api/vlm/parameters').json()['models'] if model['path'] == str(path))
    assert not option['available']
    payload = {'videoId': vid, 'modelPath': str(path),
               'pois': [{'id': 'gate', 'kind': 'line', 'points': [[.2, .2], [.8, .8]]}],
               'events': [{'id': 'e', 'description': 'test'}]}
    response = client.post('/api/vlm/runs', json=payload)
    assert response.status_code == 422
    assert 'missing.safetensors' in response.json()['detail']
    assert not (api.DATA_ROOT / 'runs').exists()


def test_historical_model_uses_worker_configuration_even_after_default_changes(client, local_vlm_models, monkeypatch):
    folder = api.DATA_ROOT / 'runs/old-model'
    folder.mkdir(parents=True)
    write_json(folder / 'status.json', {'runId': 'old-model', 'state': 'completed'})
    write_json(folder / 'request.json', {'modelPath': '/historical/removed-model', 'request': {'sampleFps': 3}})
    monkeypatch.setenv('VLM_MODEL_PATH', str(local_vlm_models[1]))
    parameters = client.get('/api/vlm/runs/old-model').json()['parameters']
    assert parameters['modelPath'] == '/historical/removed-model'
    assert parameters['sampleFps'] == 3


def test_invalid_video_and_size_limit_cleanup(client, monkeypatch):
    assert client.post("/api/vlm/videos", files={"file": ("clip.mp4", b"broken")}).status_code == 400
    assert list((api.DATA_ROOT / "uploads").iterdir()) == []
    monkeypatch.setattr(api, "MAX_BYTES", 4)
    assert client.post("/api/vlm/videos", files={"file": ("clip.mp4", b"12345")}).status_code == 413
    assert list((api.DATA_ROOT / "uploads").iterdir()) == []


def clip_bytes(tmp_path):
    path = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25, (96, 64))
    for _ in range(25):
        writer.write(np.zeros((64, 96, 3), np.uint8))
    writer.release()
    return path.read_bytes()


def chunked_upload(client, blob, chunk_size):
    """Drive the init/parts/complete protocol and return the final response."""
    init = client.post("/api/vlm/videos/init", json={"filename": "clip.mp4", "size": len(blob)})
    assert init.status_code == 200, init.text
    upload_id = init.json()["uploadId"]
    for index, offset in enumerate(range(0, len(blob), chunk_size)):
        part = client.put(f"/api/vlm/videos/{upload_id}/parts/{index}",
                          content=blob[offset:offset + chunk_size])
        assert part.status_code == 200, part.text
    return upload_id, client.post(f"/api/vlm/videos/{upload_id}/complete")


def test_chunked_upload_assembles_and_matches_single_upload_sha(client, tmp_path):
    blob = clip_bytes(tmp_path)
    upload_id, done = chunked_upload(client, blob, chunk_size=max(1, len(blob) // 3 + 1))
    assert done.status_code == 200, done.text
    meta = done.json()
    import hashlib
    assert meta["sha256"] == hashlib.sha256(blob).hexdigest()
    assert meta["videoId"] == upload_id
    assert meta["sizeBytes"] == len(blob)
    # Parts are consumed and removed after assembly.
    assert not (api.DATA_ROOT / "uploads" / upload_id / "parts").exists()
    assert (api.DATA_ROOT / "uploads" / upload_id / "video.json").is_file()
    # The assembled file streams back with Range support like a single upload.
    assert client.get(f"/api/vlm/videos/{upload_id}/source",
                      headers={"range": "bytes=0-99"}).status_code == 206


def test_chunked_upload_rejects_missing_parts_and_cleans_up(client, tmp_path):
    blob = clip_bytes(tmp_path)
    chunk_size = max(1, len(blob) // 3 + 1)
    init = client.post("/api/vlm/videos/init", json={"filename": "clip.mp4", "size": len(blob)})
    upload_id = init.json()["uploadId"]
    # Upload part 0 and part 2 but skip part 1 -> non-contiguous.
    client.put(f"/api/vlm/videos/{upload_id}/parts/0", content=blob[0:chunk_size])
    client.put(f"/api/vlm/videos/{upload_id}/parts/2", content=blob[chunk_size * 2:chunk_size * 3])
    done = client.post(f"/api/vlm/videos/{upload_id}/complete")
    assert done.status_code == 400, done.text
    assert not (api.DATA_ROOT / "uploads" / upload_id).exists()


def test_chunked_upload_init_validates_extension_and_size(client):
    assert client.post("/api/vlm/videos/init", json={"filename": "clip.txt", "size": 10}).status_code == 400
    assert client.post("/api/vlm/videos/init", json={"filename": "clip.mp4", "size": 0}).status_code == 400


def test_chunked_upload_abort_removes_pending(client, tmp_path):
    blob = clip_bytes(tmp_path)
    init = client.post("/api/vlm/videos/init", json={"filename": "clip.mp4", "size": len(blob)})
    upload_id = init.json()["uploadId"]
    client.put(f"/api/vlm/videos/{upload_id}/parts/0", content=blob[:64])
    assert client.delete(f"/api/vlm/videos/{upload_id}").json()["ok"] is True
    assert not (api.DATA_ROOT / "uploads" / upload_id).exists()
    # Completing an aborted upload is unknown.
    assert client.post(f"/api/vlm/videos/{upload_id}/complete").status_code == 404


def test_chunked_upload_rejects_overlong_video(client, tmp_path, monkeypatch):
    blob = clip_bytes(tmp_path)
    monkeypatch.setattr(api, "MAX_DURATION_SEC", 0.001)
    _, done = chunked_upload(client, blob, chunk_size=max(1, len(blob) // 2 + 1))
    assert done.status_code == 400, done.text


def test_video_duration_limit_is_configurable(client, tmp_path, monkeypatch):
    blob = clip_bytes(tmp_path)
    monkeypatch.setattr(api, "MAX_DURATION_SEC", 0.001)
    _, rejected = chunked_upload(client, blob, chunk_size=max(1, len(blob) // 2 + 1))
    assert rejected.status_code == 400
    assert "VLM_MAX_DURATION_SEC" in rejected.text
    # 0 disables the check, so multi-hour recordings are accepted as uploaded.
    monkeypatch.setattr(api, "MAX_DURATION_SEC", 0)
    _, accepted = chunked_upload(client, blob, chunk_size=max(1, len(blob) // 2 + 1))
    assert accepted.status_code == 200, accepted.text


def test_crashed_worker_is_interrupted(client):
    folder = api.DATA_ROOT / "runs" / "orphan"
    folder.mkdir(parents=True)
    write_json(folder / "status.json", {"runId": "orphan", "state": "tracking"})
    write_json(folder / "owner.json", {"pid": os.getpid(), "identity": "wrong-start-time"})
    assert client.get("/api/vlm/runs/orphan").json()["state"] == "interrupted"


def test_cancel_live_worker_marks_request(client):
    folder = api.DATA_ROOT / "runs" / "live"
    folder.mkdir(parents=True)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        write_json(folder / "status.json", {"runId": "live", "state": "tracking"})
        write_json(folder / "owner.json", {"pid": child.pid, "identity": process_identity(child.pid)})
        assert client.post("/api/vlm/runs/live/cancel").status_code == 200
        child.wait(timeout=5)
        assert (folder / "cancel.requested").exists()
        assert client.get("/api/vlm/runs/live").json()["state"] == "cancelled"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_submitted_runs_queue_and_pump_starts_the_next(client, tmp_path, monkeypatch, local_vlm_models):
    vid = upload(client, tmp_path)
    launched = []

    def fake_launch(folder, python):
        launched.append(folder.name)
        if len(launched) == 1:
            state = jobs.read_json(folder / "status.json")
            jobs.write_json(folder / "status.json", {**state, "state": "tracking"})

    monkeypatch.setattr(jobs, "launch", fake_launch)
    payload = {"videoId": vid, "pois": [{"id": "gate", "kind": "line", "points": [[.2, .2], [.8, .8]]}],
               "events": [{"id": "e", "description": "test"}], "modelPath": str(local_vlm_models[1])}
    first = client.post("/api/vlm/runs", json=payload)
    second = client.post("/api/vlm/runs", json=payload)
    assert first.status_code == second.status_code == 200, second.text
    first_id, second_id = first.json()["runId"], second.json()["runId"]
    # The second submission is accepted and waits instead of failing with 409.
    assert launched == [first_id]
    assert jobs.read_json(api.DATA_ROOT / "runs" / second_id / "status.json")["state"] == "queued"
    state = jobs.read_json(api.DATA_ROOT / "runs" / first_id / "status.json")
    jobs.write_json(api.DATA_ROOT / "runs" / first_id / "status.json", {**state, "state": "completed"})
    api._pump()
    assert launched == [first_id, second_id]


def test_queued_run_waits_without_interrupt_and_lists_as_active(client):
    folder = api.DATA_ROOT / "runs/waiting"
    folder.mkdir(parents=True)
    write_json(folder / "status.json", {"runId": "waiting", "state": "queued", "startedAt": 1})
    old = time.time() - 600
    os.utime(folder / "status.json", (old, old))
    assert client.get("/api/vlm/runs/waiting").json()["state"] == "queued"
    assert [run["runId"] for run in client.get("/api/vlm/runs?active=true").json()] == ["waiting"]


def test_state_filter_returns_completed_records_before_limit(client):
    for index in range(3):
        folder = api.DATA_ROOT / f"runs/queued-{index}"
        folder.mkdir(parents=True)
        write_json(folder / "status.json", {"runId": f"queued-{index}", "state": "queued", "startedAt": index})
    done = api.DATA_ROOT / "runs/done"
    done.mkdir(parents=True)
    write_json(done / "status.json", {"runId": "done", "state": "completed", "startedAt": 10})
    old = time.time() - 60
    os.utime(done / "status.json", (old, old))
    # Newer unfinished runs must not push the completed record out of the limit.
    assert [run["runId"] for run in client.get("/api/vlm/runs?limit=1&state=completed").json()] == ["done"]
    assert client.get("/api/vlm/runs?state=completed").json()[0]["runId"] == "done"


def test_cancelled_queued_run_is_terminal_and_never_launched(client, monkeypatch):
    folder = api.DATA_ROOT / "runs/waiting-cancel"
    folder.mkdir(parents=True)
    write_json(folder / "status.json", {"runId": "waiting-cancel", "state": "queued", "startedAt": 1})
    launched = []
    monkeypatch.setattr(jobs, "launch", lambda *args: launched.append(args))
    assert client.post("/api/vlm/runs/waiting-cancel/cancel").json()["state"] == "cancelled"
    api._pump()
    assert launched == []
    assert client.get("/api/vlm/runs?active=true").json() == []


def test_backend_imports_without_rules_pythonpath():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run([sys.executable, "-c", "from app.main import app; print(app.title)"],
                               cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
