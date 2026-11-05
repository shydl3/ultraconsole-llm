"""Understand-phase orchestration: logical segment bucketing and crash resume.

The real _process_candidate decodes video and runs the VLM, so these tests stub
it out to verify only the scheduling layer in understand(): candidates are
grouped by segment time-range, progress reports currentSegment/segmentCount, and
already-written resultIds are skipped on a re-run (idempotent resume).
"""
from __future__ import annotations

import ultra_rules.vlm.qwen_worker as worker
from ultra_rules.vlm.qwen_worker import understand
from ultra_rules.vlm.segmenting import write_manifest
from ultra_rules.vlm.storage import append_json, iter_jsonl, progress, read_json, write_json


def _request_config():
    return {
        "modelPath": "/unused/model",
        "video": {"durationSec": 40.0, "sha256": "abc"},
        "request": {
            "videoId": "v1",
            "pois": [{"id": "zone", "kind": "zone", "points": [[.2, .2], [.8, .2], [.8, .8], [.2, .8]]}],
            "events": [{"id": "e1", "description": "test"}],
            "stableSeconds": 0, "hysteresisPx": 0,
        },
    }


def _candidate(cid, trigger_sec):
    return {"candidateId": cid, "triggerSec": trigger_sec, "poiId": "zone",
            "triggerKind": "zone.enter", "trackSegmentId": f"{cid}-seg",
            "eventDefinitionIds": ["e1"]}


def _install_stub(monkeypatch, processed):
    def stub(run_dir, config, request, candidate, events, candidates, state):
        for event_id in candidate["eventDefinitionIds"]:
            result_id = f"{candidate['candidateId']}-{event_id}"
            if result_id in state["completed"]:
                continue
            processed.append((state["currentSegment"], candidate["candidateId"]))
            append_json(run_dir / "results.jsonl",
                        {"resultId": result_id, "candidateId": candidate["candidateId"], "decision": "match"})
            state["completed"].add(result_id)
            state["done"] += 1
            state["counts"]["match"] += 1
            progress(run_dir, completedRequests=state["done"], failedRequests=state["failed"],
                     decisions=state["counts"], currentSegment=state.get("currentSegment", 0))
    monkeypatch.setattr(worker, "_process_candidate", stub)


def _setup_run(run_dir):
    write_json(run_dir / "request.json", _request_config())
    for cand in (_candidate("c0", 5.0), _candidate("c1", 15.0),
                 _candidate("c2", 25.0), _candidate("c3", 35.0)):
        append_json(run_dir / "candidates.jsonl", cand)
    write_manifest(run_dir, [
        {"index": 0, "startSec": 0.0, "endSec": 20.0, "durationSec": 20.0},
        {"index": 1, "startSec": 20.0, "endSec": 40.0, "durationSec": 20.0},
    ], "abc", mode="logical")


def test_understand_buckets_candidates_by_segment(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    _setup_run(run_dir)
    processed: list = []
    _install_stub(monkeypatch, processed)

    understand(run_dir)

    # Segment 0 candidates run before segment 1, each tagged with its segment.
    assert processed == [(0, "c0"), (0, "c1"), (1, "c2"), (1, "c3")]
    rows = list(iter_jsonl(run_dir / "results.jsonl"))
    assert {r["resultId"] for r in rows} == {"c0-e1", "c1-e1", "c2-e1", "c3-e1"}
    summary = read_json(run_dir / "summary.json")
    assert summary["resultCount"] == 4 and summary["failedRequests"] == 0
    progress = read_json(run_dir / "progress.json")
    assert progress["segmentCount"] == 2
    assert progress["currentSegment"] == 1  # last scheduled segment
    assert progress["completedRequests"] == 4


def test_understand_resumes_skipping_completed_results(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    _setup_run(run_dir)
    # Simulate a crash after segment 0 finished: c0/c1 already written.
    append_json(run_dir / "results.jsonl", {"resultId": "c0-e1", "candidateId": "c0", "decision": "match"})
    append_json(run_dir / "results.jsonl", {"resultId": "c1-e1", "candidateId": "c1", "decision": "match"})
    processed: list = []
    _install_stub(monkeypatch, processed)

    understand(run_dir)

    # Only the unfinished segment-1 candidates are reprocessed.
    assert processed == [(1, "c2"), (1, "c3")]
    rows = list(iter_jsonl(run_dir / "results.jsonl"))
    result_ids = [r["resultId"] for r in rows]
    assert len(result_ids) == len(set(result_ids)) == 4  # no duplicates appended
    summary = read_json(run_dir / "summary.json")
    assert summary["resultCount"] == 4


def test_understand_without_segments_processes_all_in_one_bucket(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    write_json(run_dir / "request.json", _request_config())
    for cand in (_candidate("c0", 5.0), _candidate("c1", 15.0)):
        append_json(run_dir / "candidates.jsonl", cand)
    processed: list = []
    _install_stub(monkeypatch, processed)

    understand(run_dir)

    # Missing segments.json falls back to a single open-ended bucket.
    assert processed == [(0, "c0"), (0, "c1")]
    assert read_json(run_dir / "summary.json")["resultCount"] == 2
