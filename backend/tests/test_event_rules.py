from __future__ import annotations

import json
from dataclasses import replace

import app.main as main
import app.security as security
import app.vlm_api as api
import app.vlm_jobs as jobs
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch, local_vlm_models):
    monkeypatch.setattr(api, 'DATA_ROOT', tmp_path / 'vlm')
    monkeypatch.setattr(security, 'settings', replace(security.settings, api_token='secret'))
    with TestClient(main.app, headers={'authorization': 'Bearer secret'}) as client:
        yield client


def rule(identifier='custom', **changes):
    return {'id': identifier, 'name': '测试规则', 'description': '是否强行通过', **changes}


def save(client, rules, revision=None, deletes=None):
    if revision is None:
        revision = client.get('/api/vlm/event-rules').json()['revision']
    return client.post('/api/vlm/event-rules/batch', json={'revision': revision, 'upserts': rules, 'deletes': deletes or []})


def test_persistence_batch_validation_and_conflict(client):
    original = client.get('/api/vlm/event-rules').json()
    assert original['rules']
    response = save(client, [rule('one'), rule('two', enabled=False)])
    assert response.status_code == 200, response.text
    saved = response.json()
    assert json.loads((api.DATA_ROOT / 'event-rules.json').read_text()) == saved
    # A mixed valid/invalid batch must write nothing.
    assert save(client, [rule('three'), rule('broken', description=' ')]).status_code == 422
    assert client.get('/api/vlm/event-rules').json() == saved
    assert save(client, [rule('one', description='changed')], revision=original['revision']).status_code == 409
    assert save(client, [rule('one'), rule('one')]).status_code == 422
    assert client.get('/api/vlm/event-rules').json() == saved
    updated = save(client, [rule('one', name='重命名')]).json()
    assert next(r for r in updated['rules'] if r['id'] == 'one')['version'] == 2
    assert save(client, [], deletes=['unknown']).status_code == 404
    assert client.get('/api/vlm/event-rules').json() == updated
    assert save(client, [rule('bad', poiIds=['old-poi'])]).status_code == 422
    deleted = save(client, [], deletes=[r['id'] for r in updated['rules']]).json()
    assert deleted['rules'] == []
    assert client.get('/api/vlm/event-rules').json()['rules'] == []  # Do not re-seed a deliberately empty library.


def start_payload(library, identifier='custom', **changes):
    item = next(r for r in library['rules'] if r['id'] == identifier)
    return {'videoId': 'clip', 'revision': library['revision'],
            'selections': [{'id': identifier, 'version': item['version'], 'poiIds': ['gate']}],
            'pois': [{'id': 'gate', 'kind': 'line', 'points': [[.1, .5], [.9, .5]]}], **changes}


def test_run_uses_backend_definition_and_keeps_snapshot_after_edit_delete(client, monkeypatch):
    library = save(client, [rule()]).json()
    folder = api.DATA_ROOT / 'uploads/clip'
    folder.mkdir(parents=True)
    jobs.write_json(folder / 'video.json', {'fileName': 'clip.mp4', 'sourceName': 'source.mp4', 'sha256': 'abc'})
    (folder / 'source.mp4').write_bytes(b'test')

    def launch(run_dir, python):
        # The snapshot must be on disk before the worker sees the run.
        assert (run_dir / 'rules-snapshot.json').exists()
        status = jobs.read_json(run_dir / 'status.json')
        jobs.write_json(run_dir / 'status.json', {**status, 'state': 'completed'})
    monkeypatch.setattr(jobs, 'launch', launch)
    response = client.post('/api/vlm/runs/from-rules', json=start_payload(library))
    assert response.status_code == 200, response.text
    rid = response.json()['runId']
    snapshot = client.get(f'/api/vlm/runs/{rid}/rules').json()
    assert snapshot['rules'][0]['version'] == 1
    assert snapshot['rules'][0]['event']['poiIds'] == ['gate']
    request = jobs.read_json(api.DATA_ROOT / f'runs/{rid}/request.json')
    assert request['request']['events'][0]['description'] == '是否强行通过'
    save(client, [rule(description='changed', name='new name')])
    save(client, [], deletes=['custom'])
    assert client.get(f'/api/vlm/runs/{rid}/rules').json() == snapshot
    assert client.get(f'/api/library/vlm/{rid}').json()['ruleSnapshot'] == snapshot
    # A stale selection is rejected rather than silently picking up a newer rule.
    assert client.post('/api/vlm/runs/from-rules', json=start_payload(library)).status_code == 409


def test_invalid_bindings_disabled_and_oversized_selection(client):
    library = save(client, [rule()]).json()
    payload = start_payload(library)
    payload['selections'][0]['poiIds'] = ['deleted-zone']
    assert client.post('/api/vlm/runs/from-rules', json=payload).status_code == 422
    payload = start_payload(library)
    payload['selections'] *= 31
    assert client.post('/api/vlm/runs/from-rules', json=payload).status_code == 422
    library = save(client, [rule(triggerKinds=['zone.enter'])]).json()
    assert client.post('/api/vlm/runs/from-rules', json=start_payload(library)).status_code == 422
    library = save(client, [rule(enabled=False)]).json()
    assert client.post('/api/vlm/runs/from-rules', json=start_payload(library)).status_code == 409


def test_old_snapshot_and_auth(client):
    folder = api.DATA_ROOT / 'runs/old'
    folder.mkdir(parents=True)
    jobs.write_json(folder / 'request.json', {'request': {'events': [{'id': 'old', 'description': 'old definition'}]}})
    value = client.get('/api/vlm/runs/old/rules').json()
    assert value['rules'][0]['version'] is None
    assert value['rules'][0]['event']['description'] == 'old definition'
    assert TestClient(main.app).get('/api/vlm/event-rules').status_code == 401
    assert TestClient(main.app).post('/api/vlm/event-rules/batch', json={'revision': 0}).status_code == 401
    assert client.get('/api/vlm/runs/..%2foutside/rules').status_code in (400, 404)


def test_corrupt_library_preserved(client):
    client.get('/api/vlm/event-rules')
    path = api.DATA_ROOT / 'event-rules.json'
    path.write_text('broken')
    assert client.get('/api/vlm/event-rules').status_code == 500
    assert path.read_text() == 'broken'


def test_parameter_defaults_and_custom_values_reach_worker_snapshot(client, monkeypatch):
    contract = client.get('/api/vlm/parameters').json()
    assert contract['defaults'] == jobs.AnalysisParameters().model_dump()
    assert contract['fields']['maxFrames']['multipleOf'] == 2
    assert contract['fields']['conf']['exclusiveMinimum'] == 0
    assert contract['fields']['conf']['exclusiveMaximum'] == 1
    library = save(client, [rule()]).json()
    folder = api.DATA_ROOT / 'uploads/clip'
    folder.mkdir(parents=True)
    jobs.write_json(folder / 'video.json', {'fileName': 'clip.mp4', 'sourceName': 'source.mp4', 'sha256': 'abc'})
    (folder / 'source.mp4').write_bytes(b'test')
    settings = {'modelPath': contract['models'][1]['path'], 'poseDevice': 'cpu', 'poseFps': 15, 'conf': .4, 'iou': .6, 'stableSeconds': .4,
                'hysteresisPx': 10, 'maxGapSeconds': 2, 'sampleFps': 4, 'maxFrames': 48,
                'maxPixels': 393216, 'maxNewTokens': 256, 'dryRun': False}

    def launch(run_dir, _python):
        # Check the exact persisted request consumed by the real worker.
        config = jobs.read_json(run_dir / 'request.json')
        request = config['request']
        assert config['modelPath'] == settings['modelPath']
        assert jobs.read_json(run_dir / 'manifest.json')['configuration']['modelPath'] == settings['modelPath']
        assert {key: request[key] for key in settings} == settings
        assert request['events'][0]['preSeconds'] == 4
        assert request['events'][0]['postSeconds'] == 6
        status = jobs.read_json(run_dir / 'status.json')
        jobs.write_json(run_dir / 'status.json', {**status, 'state': 'completed'})
    monkeypatch.setattr(jobs, 'launch', launch)
    response = client.post('/api/vlm/runs/from-rules', json=start_payload(library, **settings))
    assert response.status_code == 200, response.text
    rid = response.json()['runId']
    assert client.get(f'/api/vlm/runs/{rid}').json()['parameters'] == settings
    assert client.get('/api/vlm/parameters').json()['defaults'] == contract['defaults']


@pytest.mark.parametrize('key,value', [('sampleFps', 0), ('maxFrames', 5), ('maxFrames', 66),
                                     ('maxNewTokens', 127), ('maxPixels', 1), ('conf', 0), ('iou', 1),
                                     ('poseFps', 31), ('maxGapSeconds', .1), ('stableSeconds', -1)])
def test_invalid_parameters_rejected_before_launch(client, monkeypatch, key, value):
    library = save(client, [rule()]).json()
    def no_launch(*_args):
        pytest.fail('Invalid parameters must not launch a worker')
    monkeypatch.setattr(jobs, 'launch', no_launch)
    response = client.post('/api/vlm/runs/from-rules', json=start_payload(library, **{key: value}))
    assert response.status_code == 422
    assert not (api.DATA_ROOT / 'runs').exists()
