from __future__ import annotations

import json
import os
from dataclasses import replace

import app.library_api as library
import app.main as main
import app.rules_api as rules
import app.security as security
import app.video_api as video
import app.vlm_api as vlm
import pytest
from fastapi.testclient import TestClient


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(vlm, "DATA_ROOT", tmp_path / "vlm")
    monkeypatch.setattr(rules, "RUNS_ROOT", tmp_path / "rules")
    monkeypatch.setattr(video, "CACHE_ROOT", tmp_path / "videos")
    monkeypatch.setattr(video, "ROOT", tmp_path / "temporary")
    monkeypatch.setattr(video, "jobs", {})
    monkeypatch.setattr(security, "settings", replace(security.settings, api_token="secret"))
    write(vlm.DATA_ROOT / "runs/analysis/status.json", {
        "runId": "analysis", "state": "completed", "videoId": "source1", "fileName": "监控.mp4",
        "startedAt": 200, "summary": {"resultCount": 1},
    })
    (vlm.DATA_ROOT / "runs/analysis/results.jsonl").write_text(json.dumps({"trackId": 7, "reason": "result"}) + '\n')
    write(vlm.DATA_ROOT / "uploads/source1/video.json", {"sourceName": "source.mp4"})
    (vlm.DATA_ROOT / "uploads/source1/source.mp4").write_bytes(b"original")
    write(rules.RUNS_ROOT / "rule-run/run.json", {"finishedAt": 101, "startedAt": 100, "videos": [], "events": [{"eventId": "event1"}]})
    write(rules.RUNS_ROOT / "rule-run/events.json", [{"eventId": "event1", "ruleId": "crossing"}])
    video.CACHE_ROOT.mkdir(parents=True)
    (video.CACHE_ROOT / "legacy-preview.mp4").write_bytes(b"converted-video")
    write(video.CACHE_ROOT / "legacy-preview.json", {"originalFilename": "converted.mp4", "createdAt": 300, "sourceSha256": "original-hash"})
    with TestClient(main.app, headers={"authorization": "Bearer secret"}) as client:
        yield client


def test_all_categories_pagination_search_and_details(client):
    result = client.get('/api/library?limit=2').json()
    assert result['total'] == 3
    assert result['counts'] == {'vlm': 1, 'rules': 1, 'video': 1}
    assert [item['kind'] for item in result['items']] == ['video', 'vlm']
    assert client.get('/api/library?offset=2&limit=2').json()['items'][0]['kind'] == 'rules'
    assert client.get('/api/library', params={'q': '监控'}).json()['total'] == 1
    assert client.get('/api/library?kind=video').json()['total'] == 1
    semantic = client.get('/api/library/vlm/analysis').json()
    assert semantic['results'][0]['trackId'] == 7
    assert semantic['videos'][0]['url'] == '/api/vlm/videos/source1/source'
    assert client.get('/api/library/rules/rule-run').json()['results'][0]['eventId'] == 'event1'
    assert client.get('/api/library/video/legacy-preview/source', headers={'Range': 'bytes=0-3'}).content == b'conv'


def test_rename_preserves_ids_cache_key_and_worker_status(client):
    response = client.patch('/api/library/video/legacy-preview', json={'name': '自定义视频'})
    assert response.status_code == 200
    assert response.json()['name'] == '自定义视频'
    meta = json.loads((video.CACHE_ROOT / 'legacy-preview.json').read_text())
    assert meta['sourceSha256'] == 'original-hash'
    assert (video.CACHE_ROOT / 'legacy-preview.mp4').exists()
    assert client.get('/api/library', params={'q': '自定义视频'}).json()['total'] == 1
    response = client.patch('/api/library/vlm/analysis', json={'name': '安全检查记录'})
    assert response.status_code == 200
    assert json.loads((vlm.DATA_ROOT / 'runs/analysis/status.json').read_text())['fileName'] == '监控.mp4'
    assert client.get('/api/library/vlm/analysis').json()['item']['name'] == '安全检查记录'
    assert client.patch('/api/library/rules/rule-run', json={'name': '规则记录'}).status_code == 200
    assert client.patch('/api/library/video/legacy-preview', json={'name': '  '}).status_code == 422
    assert client.patch('/api/library/video/legacy-preview', json={'name': 'x' * 161}).status_code == 422
    download = client.get('/api/library/video/legacy-preview/source?download=true')
    assert download.status_code == 200
    assert 'attachment;' in download.headers['content-disposition']


def test_delete_preserves_shared_source_and_rejects_active(client):
    write(vlm.DATA_ROOT / 'runs/active/status.json', {'runId': 'active', 'state': 'tracking'})
    assert client.delete('/api/library/vlm/active').status_code == 409
    write(rules.RUNS_ROOT / 'active/run.json', {'startedAt': 100})
    assert client.delete('/api/library/rules/active').status_code == 409
    assert client.delete('/api/library/vlm/analysis').status_code == 200
    assert not (vlm.DATA_ROOT / 'runs/analysis').exists()
    assert (vlm.DATA_ROOT / 'uploads/source1/source.mp4').read_bytes() == b'original'
    source = vlm.DATA_ROOT / 'uploads/source1/source.mp4'
    (rules.RUNS_ROOT / 'rule-run/sources').mkdir()
    (rules.RUNS_ROOT / 'rule-run/sources/source.mp4').symlink_to(source)
    assert client.delete('/api/library/rules/rule-run').status_code == 200
    assert source.exists()


def test_delete_video_removes_metadata_alias_and_invalidates_session(client):
    path = video.CACHE_ROOT / 'legacy-preview.mp4'
    alias = video.ROOT / 'preview-legacy/preview.mp4'
    alias.parent.mkdir(parents=True)
    os.link(path, alias)
    video.jobs['session'] = {'preview': path, 'state': 'ready'}
    assert client.delete('/api/library/video/legacy-preview').status_code == 200
    assert not path.exists()
    assert not path.with_suffix('.json').exists()
    assert not alias.exists()
    assert video.jobs['session']['state'] == 'failed'
    assert client.get('/api/library/video/legacy-preview/source').status_code == 404
    assert client.get('/api/library?kind=video').json()['total'] == 0


def test_auth_and_path_guards(client, tmp_path):
    assert TestClient(main.app).get('/api/library').status_code == 401
    assert TestClient(main.app).delete('/api/library/video/legacy-preview').status_code == 401
    outside = tmp_path / 'outside.mp4'
    outside.write_bytes(b'do not delete')
    (video.CACHE_ROOT / 'escape.mp4').symlink_to(outside)
    assert client.delete('/api/library/video/escape').status_code == 400
    assert client.get('/api/library/video/escape/source').status_code == 400
    assert client.delete('/api/library/video/..%2Foutside').status_code in (400, 404)
    assert client.get('/api/library?kind=video').json()['total'] == 1
    assert outside.exists()
    assert library._root('video') == video.CACHE_ROOT
