from dataclasses import replace

import app.main as main
import app.security as security
import pytest
from fastapi.testclient import TestClient


def test_api_token_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "settings", replace(security.settings, api_token="test-secret"))
    client = TestClient(main.app)

    assert client.get("/api/models").status_code == 401
    assert client.get("/api/models", headers={"authorization": "Bearer test-secret"}).status_code == 200
    assert client.get("/api/health").status_code == 200
