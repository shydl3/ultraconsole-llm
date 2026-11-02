from dataclasses import replace

import app.main as main
import app.security as security
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def test_removed_module_endpoints_are_unavailable(monkeypatch):
    monkeypatch.setattr(security, "settings", replace(security.settings, api_token=""))
    with TestClient(main.app) as client:
        for path in ("/api/infer/image", "/api/infer/frame", "/api/rules/runs", "/api/rules/runs/start"):
            for method in ("GET", "POST"):
                assert client.request(method, path).status_code == 404
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/ws/stream"):
                pass
        assert closed.value.code == 1000
        assert client.get("/api/library?kind=rules").status_code == 422
        assert client.get("/api/library/rules/old-run").status_code == 422
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/models").status_code == 200
