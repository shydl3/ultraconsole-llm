from dataclasses import replace
from types import SimpleNamespace

import app.main as main
import app.security as security
from fastapi.testclient import TestClient


class _FakeRuntime:
    """Stands in for UltralyticsRuntime so tests never touch real weights or CUDA."""

    def __init__(self, models):
        self._models = list(models)
        self.loaded = None

    def list_models(self):
        return list(self._models)

    def load(self, model_id, device):
        self.loaded = (model_id, device)
        return SimpleNamespace(id=model_id, filename=model_id, task_type="pose", names={"0": "person"})


def test_state_endpoint_reports_engine_and_no_model_when_autostart_disabled(monkeypatch):
    monkeypatch.setattr(security, "settings", replace(security.settings, api_token=""))
    with TestClient(main.app) as client:
        response = client.get("/api/state")
    assert response.status_code == 200
    body = response.json()
    assert body["engine"]["device"] in ("cpu", "cuda")
    assert body["engine"]["warming"] is False
    assert body["model"] is None


def test_autostart_prefers_cuda_and_loads_default_model(monkeypatch):
    fake = _FakeRuntime([("yolo11m-pose.pt", "/models/yolo11m-pose.pt")])
    monkeypatch.setattr(
        main, "settings", replace(main.settings, autostart=True, device="", default_model="yolo11m-pose.pt")
    )
    monkeypatch.setattr(main, "ensure_device_available", lambda device: None)
    monkeypatch.setattr(main, "runtime", fake)
    monkeypatch.setattr(main.state.engine, "device", "cpu")
    monkeypatch.setattr(main.state, "model_id", None)

    main._autostart()

    assert main.state.engine.device == "cuda"
    assert main.state.model_id == "yolo11m-pose.pt"
    assert fake.loaded == ("yolo11m-pose.pt", "cuda")


def test_autostart_falls_back_to_cpu_when_cuda_unavailable(monkeypatch):
    requested = []

    def fake_ensure(device):
        requested.append(device)
        if device == "cuda":
            raise RuntimeError("no gpu")

    fake = _FakeRuntime([("yolo11m-pose.pt", "/models/yolo11m-pose.pt")])
    monkeypatch.setattr(
        main, "settings", replace(main.settings, autostart=True, device="", default_model="does-not-exist.pt")
    )
    monkeypatch.setattr(main, "ensure_device_available", fake_ensure)
    monkeypatch.setattr(main, "runtime", fake)
    monkeypatch.setattr(main.state.engine, "device", "cuda")
    monkeypatch.setattr(main.state, "model_id", "stale.pt")

    main._autostart()

    assert requested[0] == "cuda"
    assert main.state.engine.device == "cpu"
    assert fake.loaded is None
    assert main.state.model_id == "stale.pt"
