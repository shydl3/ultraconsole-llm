"""离线规则接口（/api/rules/*）测试：运行列表/详情/事件过滤、路径校验与鉴权。"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import app.main as main
import app.rules_api as rules_api
import app.security as security
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def fake_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """构造一个最小可运行的 run 目录，并把模块级 RUNS_ROOT 指向它。"""
    runs = tmp_path / "runs"
    run_id = "test-run"
    d = runs / run_id
    (d / "frames").mkdir(parents=True)

    run = {
        "runId": run_id,
        "startedAt": "2026-01-01T00:00:00Z",
        "finishedAt": "2026-01-01T00:01:00Z",
        "modelId": "yolo11m-pose",
        "device": "cpu",
        "configHash": "abc123",
        "totals": {"byRule": {"passage.checked": 1}},
        "videos": [
            {"videoId": "v1", "cameraProfileId": "d2", "durationSec": 10.0, "eventCount": 2, "sampledFrames": 30},
        ],
        "events": [],
    }
    events = [
        {"eventId": "e1", "ruleId": "passage.checked", "videoId": "v1", "startSec": 1.0, "endSec": 3.0, "review": None},
        {"eventId": "e2", "ruleId": "access.crossing", "videoId": "v1", "startSec": 4.0, "endSec": 5.0, "review": None},
        {"eventId": "e3", "ruleId": "passage.checked", "videoId": "v2", "startSec": 0.5, "endSec": 2.0, "review": None},
    ]
    (d / "run.json").write_text(json.dumps(run), encoding="utf-8")
    (d / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (d / "frames" / "v1_trigger.jpg").write_bytes(b"\xff\xd8\xffjpeg")

    monkeypatch.setattr(rules_api, "RUNS_ROOT", runs)
    return run_id


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(security, "settings", replace(security.settings, api_token="test-secret"))
    c = TestClient(main.app)
    c.headers.update({"authorization": "Bearer test-secret"})
    return c


def test_list_runs_summarizes_counts(fake_run: str, monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client(monkeypatch)
    r = c.get("/api/rules/runs")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["runId"] == fake_run
    assert data[0]["videoCount"] == 1
    assert data[0]["eventCount"] == 0  # run.json 内 events 为空


def test_get_run_and_videos(fake_run: str, monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client(monkeypatch)
    assert c.get(f"/api/rules/runs/{fake_run}").status_code == 200
    vids = c.get(f"/api/rules/runs/{fake_run}/videos").json()
    assert vids[0]["videoId"] == "v1"


def test_events_filter_by_video_and_rule(fake_run: str, monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client(monkeypatch)
    all_ev = c.get(f"/api/rules/runs/{fake_run}/events").json()
    assert len(all_ev) == 3
    v1 = c.get(f"/api/rules/runs/{fake_run}/events", params={"videoId": "v1"}).json()
    assert {e["eventId"] for e in v1} == {"e1", "e2"}
    checked = c.get(f"/api/rules/runs/{fake_run}/events", params={"ruleId": "passage.checked"}).json()
    assert {e["eventId"] for e in checked} == {"e1", "e3"}


def test_frame_served_and_path_escape_blocked(fake_run: str, monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client(monkeypatch)
    ok = c.get(f"/api/rules/runs/{fake_run}/frames/frames/v1_trigger.jpg")
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/jpeg"
    # 目录穿越必须被拒绝
    bad = c.get(f"/api/rules/runs/{fake_run}/frames/..%2f..%2frun.json")
    assert bad.status_code in (400, 404)


def test_invalid_run_id_rejected(fake_run: str, monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client(monkeypatch)
    assert c.get("/api/rules/runs/..%2f..%2fetc").status_code in (400, 404)
    assert c.get("/api/rules/runs/does-not-exist").status_code == 404


def test_rules_requires_auth(fake_run: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "settings", replace(security.settings, api_token="test-secret"))
    anon = TestClient(main.app)
    assert anon.get("/api/rules/runs").status_code == 401
