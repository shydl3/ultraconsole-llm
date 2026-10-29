from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from ultra_rules.vlm.contracts import RunRequest
from ultra_rules.vlm.reprocess import reprocess_run
from ultra_rules.vlm.results import POSTPROCESS_VERSION, aggregate_result, apply_answer, parse_answer, verified_decision
from ultra_rules.vlm.storage import iter_jsonl, read_json, write_json


def answer_body(decision="match", confidence=.7):
    value = {"match": "yes", "no_match": "no", "uncertain": "unknown"}[decision]
    return {"decision": decision, "confidence": confidence, "reason": f"model says {decision}",
            "evidence": [{"timeSec": 1.8, "observation": "alarm"}],
            "conditions": [{"index": i, "value": value} for i in range(4)]}


def quality(**values):
    return {"targetVisibleFraction": .4, "uniqueFrameCount": 20, "medianTargetHeightPx": 90.8,
            "preContextClipped": True, "postContextClipped": False, **values}


def segment(decision="match", index=0, confidence=.7):
    part = {"window": {"startSec": 0, "endSec": 7.8}, "quality": quality(),
            "sampleManifest": f"samples/{index}.json", "previewPath": f"previews/{index}.jpg"}
    apply_answer(part, parse_answer(json.dumps(answer_body(decision, confidence)), 0, 7.8, 4))
    return part


@pytest.mark.parametrize("decision", ["match", "no_match", "uncertain"])
def test_quality_cannot_change_model_decision_or_confidence(decision):
    part = segment(decision, confidence=.15)
    assert part["decision"] == decision
    assert part["modelDecisionConfidence"] == .15
    assert part["downgradeReasons"] == []
    assert part["qualityWarnings"] == ["insufficient_target_visibility"]
    answer = parse_answer(json.dumps(answer_body(decision)), 0, 7.8, 4)
    severe = quality(targetVisibleFraction=0, uniqueFrameCount=1, medianTargetHeightPx=0,
                     identityAmbiguous=True, timeUnreliable=True, evidenceTimeOutsideWindow=True)
    assert verified_decision(answer, severe)[0] == decision


@pytest.mark.parametrize("flag", ["preContextClipped", "postContextClipped", "contextTruncated", "observationContextMissing"])
def test_short_context_has_no_credibility_warning(flag):
    answer = parse_answer(json.dumps(answer_body()), 0, 7.8, 4)
    assert verified_decision(answer, quality(targetVisibleFraction=1, **{flag: True})) == ("match", [])


def test_out_of_window_citation_is_retained_raw_without_veto():
    part = segment()
    body = answer_body()
    body["evidence"][0]["timeSec"] = 99
    apply_answer(part, parse_answer(json.dumps(body), 0, 7.8, 4, check_timestamps=False))
    assert part["decision"] == "match"
    assert part["evidence"] == []
    assert part["modelEvidence"][0]["timeSec"] == 99
    assert "evidenceTimeOutsideWindow" in part["qualityWarnings"]


@pytest.mark.parametrize("decisions,expected,index", [
    (["no_match", "match"], "match", 1),
    (["match", "uncertain"], "match", 0),
    (["no_match", "uncertain"], "uncertain", 1),
    (["no_match", "no_match"], "no_match", 0),
    (["uncertain", "uncertain"], "uncertain", 0),
])
def test_segment_aggregation_keeps_matching_evidence_together(decisions, expected, index):
    output = {"segments": [segment(d, i, .7 + i * .1) for i, d in enumerate(decisions)]}
    aggregate_result(output)
    assert output["decision"] == expected
    assert output["selectedSegmentIndex"] == index
    assert output["previewPath"] == f"previews/{index}.jpg"
    assert output["reason"] == f"model says {expected}"
    assert output["modelDecisionConfidence"] == pytest.approx(.7 + index * .1)
    assert output["downgradeReasons"] == []


def historical_run(folder):
    write_json(folder / "status.json", {"state": "completed", "finishedAt": 123})
    write_json(folder / "request.json", {"request": {"events": [{"id": "e", "description": "异常安检未确认",
                                                                 "criteria": ["a", "b", "c", "d"]}]}})
    results = []
    # Reproduce the three observed coverage values and boundary flags from 未拦截2.
    for index, (fraction, pre, post, confidence) in enumerate([
        (.4, True, False, .7), (4 / 18, False, True, .7), (5 / 17, False, True, .8)
    ]):
        part = segment(confidence=confidence)
        part["quality"].update(targetVisibleFraction=fraction, preContextClipped=pre, postContextClipped=post)
        raw = folder / "raw" / f"{index}.txt"
        raw.parent.mkdir(exist_ok=True)
        raw.write_text(json.dumps(answer_body(confidence=confidence)), encoding="utf-8")
        part.update(rawOutputPath=f"raw/{index}.txt", decision="uncertain",
                    downgradeReasons=["insufficient_target_visibility", "incomplete_context"])
        result = {"resultId": str(index), "eventDefinitionId": "e", "inferenceStatus": "succeeded",
                  "decision": "uncertain", "segments": [part]}
        results.append(result)
    results.extend([{"inferenceStatus": "failed", "decision": "uncertain", "error": "invalid_output"},
                    {"inferenceStatus": "dry_run", "decision": "uncertain"}])
    (folder / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")
    write_json(folder / "summary.json", {"resultCount": 5, "failedRequests": 1, "decisions": {"uncertain": 4}})
    write_json(folder / "progress.json", {"decisions": {"uncertain": 4}, "completedRequests": 5})
    return results


def test_replay_recovers_positives_backs_up_and_updates_all_counts(tmp_path):
    before = historical_run(tmp_path)
    raw = (tmp_path / "raw/0.txt").read_bytes()
    report = reprocess_run(tmp_path)
    assert report["changedResults"] == 3
    after = list(iter_jsonl(tmp_path / "results.jsonl"))
    assert [r["decision"] for r in after[:3]] == ["match"] * 3
    assert [r["modelDecisionConfidence"] for r in after[:3]] == [.7, .7, .8]
    assert all(r["downgradeReasons"] == [] for r in after[:3])
    assert after[3:] == before[3:]
    counts = {"match": 3, "no_match": 0, "uncertain": 1}
    assert read_json(tmp_path / "summary.json")["decisions"] == counts
    assert read_json(tmp_path / "status.json")["summary"]["decisions"] == counts
    assert read_json(tmp_path / "status.json")["finishedAt"] == 123
    assert read_json(tmp_path / "progress.json")["decisions"] == counts
    assert list(iter_jsonl(Path(report["backup"]) / "results.jsonl")) == before
    assert (tmp_path / "raw/0.txt").read_bytes() == raw
    assert reprocess_run(tmp_path)["changedResults"] == 0


def test_replay_rejects_active_runs_and_preserves_files_on_bad_raw(tmp_path):
    historical_run(tmp_path)
    old = (tmp_path / "results.jsonl").read_bytes()
    write_json(tmp_path / "status.json", {"state": "understanding"})
    with pytest.raises(ValueError, match="terminal"):
        reprocess_run(tmp_path)
    write_json(tmp_path / "status.json", {"state": "completed"})
    (tmp_path / "raw/2.txt").write_text("invalid", encoding="utf-8")
    with pytest.raises(ValueError):
        reprocess_run(tmp_path)
    assert (tmp_path / "results.jsonl").read_bytes() == old
    assert not (tmp_path / "postprocess-backups").exists()


@pytest.mark.parametrize("invalid", [False, True])
def test_live_worker_uses_same_policy_and_keeps_invalid_output_failed(tmp_path, monkeypatch, invalid):
    from ultra_rules.vlm import qwen_worker

    cfg = RunRequest(videoId="v", pois=[{"id": "p", "kind": "line", "points": [[0, 0], [1, 1]]}],
                     events=[{"id": "e", "description": "test", "criteria": ["a", "b", "c", "d"],
                              "preSeconds": 4, "postSeconds": 20}])
    write_json(tmp_path / "request.json", {"request": cfg.model_dump(), "modelPath": "fake",
                                          "video": {"durationSec": 40, "sha256": "fake"}})
    candidate = {"candidateId": "c", "trackId": 2, "triggerSec": 1.8, "eventDefinitionIds": ["e"]}
    (tmp_path / "candidates.jsonl").write_text(json.dumps(candidate) + "\n", encoding="utf-8")
    answers = iter(["no_match", "match"])

    class FakeQwen:
        runtime = {}
        torch = SimpleNamespace(cuda=SimpleNamespace(empty_cache=lambda: None))

        def __init__(self, _path):
            pass

        def infer(self, *_args):
            return ("invalid" if invalid else json.dumps(answer_body(next(answers)))), {"generationTruncated": False}

    def build_sample(_dir, _config, _request, _candidate, start, end, _sample_id):
        return None, {}, {"window": {"startSec": start, "endSec": end}, "quality": deepcopy(quality())}

    monkeypatch.setattr(qwen_worker, "Qwen", FakeQwen)
    monkeypatch.setattr(qwen_worker, "build_sample", build_sample)
    monkeypatch.setattr(qwen_worker, "prompt", lambda *_args, **_kwargs: "test")
    qwen_worker.understand(tmp_path)
    result = list(iter_jsonl(tmp_path / "results.jsonl"))[0]
    if invalid:
        assert result["inferenceStatus"] == "failed"
        assert result["decision"] == "uncertain"
        assert read_json(tmp_path / "summary.json")["failedRequests"] == 1
    else:
        assert result["inferenceStatus"] == "succeeded"
        assert result["decision"] == "match"
        assert result["selectedSegmentIndex"] == 1
        assert result["postprocessVersion"] == POSTPROCESS_VERSION
        assert result["downgradeReasons"] == []
        assert read_json(tmp_path / "summary.json")["decisions"]["match"] == 1
