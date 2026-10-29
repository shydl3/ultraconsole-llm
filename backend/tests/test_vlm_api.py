from __future__ import annotations

import json
import os
import subprocess
import sys
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
        (folder / "results.jsonl").write_text(json.dumps({"trackId": 7, "decision": "uncertain", "eventDefinitionId": "e"}) + "\n")
    monkeypatch.setattr(jobs, "launch", fake_launch)
    payload = {"videoId": vid, "pois": [{"id": "gate", "kind": "line", "points": [[.2, .2], [.8, .8]]}],
               "events": [{"id": "e", "description": "test"}], "modelPath": str(local_vlm_models[1])}
    response = client.post("/api/vlm/runs", json=payload)
    assert response.status_code == 200, response.text
    run = response.json()["runId"]
    assert client.get(f"/api/vlm/runs/{run}").json()["state"] == "completed"
    assert client.get(f"/api/vlm/runs/{run}/results?trackId=7").json()["total"] == 1
    assert client.get(f"/api/vlm/runs/{run}/results?trackId=8").json()["total"] == 0
    assert client.get(f"/api/vlm/videos/{vid}/source", headers={"range": "bytes=0-99"}).status_code == 206
    assert TestClient(main.app).get("/api/vlm/runs").status_code == 401
    assert client.get(f"/api/vlm/runs/{run}/artifacts/..%2frequest.json").status_code in (400, 404)
    assert client.post(f"/api/vlm/runs/{run}/cancel").json()["state"] == "completed"


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


def test_backend_imports_without_rules_pythonpath():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run([sys.executable, "-c", "from app.main import app; print(app.title)"],
                               cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
