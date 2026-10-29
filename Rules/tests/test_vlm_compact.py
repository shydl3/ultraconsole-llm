from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest
from ultra_rules.vlm.contracts import EventDefinition, RunRequest
from ultra_rules.vlm.prompting import PROMPT_VERSION, prompt
from ultra_rules.vlm.qwen_worker import GENERATION_OPTIONS, JsonAnswerStop
from ultra_rules.vlm.results import apply_answer, complete_json_answer, parse_answer
from ultra_rules.vlm.storage import iter_jsonl, read_json, write_json


def core(decision="match"):
    return {"decision": decision, "confidence": .8, "reason": "目标经过门框，报警后继续通行。"}


@pytest.mark.parametrize("decision", ["match", "no_match", "uncertain"])
def test_compact_answer_keeps_model_decision_without_fabricating_evidence(decision):
    answer = parse_answer(json.dumps(core(decision)), 0, 7.8, 4)
    part = {"window": {"startSec": 0, "endSec": 7.8},
            "quality": {"targetVisibleFraction": 1, "uniqueFrameCount": 20, "medianTargetHeightPx": 80}}
    apply_answer(part, answer)
    assert part["decision"] == decision
    assert part["conditions"] == part["evidence"] == part["modelEvidence"] == []
    assert part["qualityWarnings"] == []


@pytest.mark.parametrize("conditions", [
    [{"index": 0, "value": "yes"}, {"index": 1, "value": "yes"}],
    [{"index": 0, "value": "yes"}, {"index": 0, "value": "no"}],
    [{" index": 0, "value": "yes"}, {"index": 4, "value": "yes"}, {"idx": 5, "value": "yes"}],
    None,
])
def test_legacy_optional_fields_cannot_discard_valid_decision(conditions):
    answer = parse_answer(json.dumps({**core(), "conditions": conditions, "evidence": [{"timeSec": "bad"}]}), 0, 8, 4)
    assert answer.decision == "match"
    assert len(answer.conditions) == 4
    assert answer.conditions[2].value == answer.conditions[3].value == "unknown"
    if conditions and len(conditions) == 2 and conditions[0]["index"] == conditions[1]["index"]:
        assert answer.conditions[0].value == "unknown"
    assert answer.evidence == []
    assert "auxiliary_output_incomplete" in answer._parse_warnings


@pytest.mark.parametrize("raw", [
    '{"decision":"match","decision":"no_match","reason":"x"}',
    '{"decision":"match","confidence":.8',
    '{"decision":"yes","reason":"x"}',
    '{"reason":"match"}',
    '[{"decision":"match","reason":"x"}]',
    '{"decision":"match","reason":"x"}{"decision":"no_match","reason":"y"}',
])
def test_ambiguous_or_incomplete_core_output_is_not_guessed(raw):
    with pytest.raises(ValueError):
        parse_answer(raw, 0, 8, 4)


def test_stop_handles_nested_json_quoted_braces_and_markdown_without_gpu():
    text = json.dumps({**core(), "reason": '引号与括号 " } 不应提前结束'})

    class Tokenizer:
        def decode(self, tokens, **_kwargs):
            return "".join(chr(int(t)) for t in tokens)

    stop = JsonAnswerStop(Tokenizer(), 2)
    for index in range(len(text)):
        assert not stop(np.array([[9, 9, *map(ord, text[:index])]]), None)
    assert stop(np.array([[9, 9, *map(ord, text)]]), None)
    for fenced in (f"```json\n{text}", f"```json\n{text}\n```"):
        assert complete_json_answer(fenced)
        assert parse_answer(fenced, 0, 8, 4).decision == "match"
    assert GENERATION_OPTIONS["no_repeat_ngram_size"] == 0
    assert GENERATION_OPTIONS["repetition_penalty"] == 1


def test_prompt_only_requests_compact_output_and_current_event():
    event = EventDefinition(id="e", description="是否存在异常安检未确认", criteria=["门框红灯", "目标继续通行"])
    candidate = {"trackId": 32, "triggerKind": "zone.enter", "triggerSec": 12.8, "direction": None}
    manifest = {"observations": [], "window": {"startSec": 8.8, "endSec": 15.72}, "quality": {}}
    question = prompt(candidate, event, manifest)
    assert "60个汉字" in question
    assert "不要输出conditions、evidence" in question
    assert "推挤" not in question and "强行通过" not in question
    assert '"conditions":' not in question and '"evidence":' not in question
    assert "门框红灯" in question and "目标继续通行" in question
    assert "30个汉字" in prompt(candidate, event, manifest, retry=True)
    assert PROMPT_VERSION == "target-event-compact-json-v3"


@pytest.mark.parametrize("mode", ["compact", "legacy", "retry", "truncated", "complete_without_eos"])
def test_worker_compact_flow_preserves_poi_seek_metadata(tmp_path, monkeypatch, mode):
    from ultra_rules.vlm import qwen_worker

    request = RunRequest(videoId="v", pois=[{"id": "p", "kind": "line", "points": [[0, 0], [1, 1]]}],
                         events=[{"id": "e", "description": "test", "criteria": ["a", "b", "c", "d"]}])
    write_json(tmp_path / "request.json", {"request": request.model_dump(), "modelPath": "fake",
                                          "video": {"durationSec": 16, "sha256": "fake"}})
    candidate = {"candidateId": "c", "trackId": 32, "trackSegmentId": "32-1", "poiId": "p",
                 "triggerKind": "line.cross", "direction": "negative_to_positive", "triggerSec": 12.8,
                 "confirmedSec": 13.0, "eventDefinitionIds": ["e"]}
    (tmp_path / "candidates.jsonl").write_text(json.dumps(candidate) + "\n", encoding="utf-8")
    calls = []

    class FakeQwen:
        runtime = {}
        torch = SimpleNamespace(cuda=SimpleNamespace(empty_cache=lambda: None))

        def __init__(self, _path):
            pass

        def infer(self, _frames, _metadata, question, max_tokens):
            calls.append(question)
            assert max_tokens == 512
            body = core()
            if mode == "legacy":
                body.update(conditions=[{"index": 0, "value": "yes"}], trackId=999, triggerSec=999)
            raw = json.dumps(body)
            if mode == "truncated" or (mode == "retry" and len(calls) == 1):
                raw = raw[:-2]
            return raw, {"generationTruncated": mode in ("truncated", "complete_without_eos")}

    def build_sample(_dir, _config, _request, _candidate, start, end, _sample_id):
        return None, {}, {"window": {"startSec": start, "endSec": end}, "observations": [],
                          "quality": {"targetVisibleFraction": .3, "uniqueFrameCount": 17, "medianTargetHeightPx": 90}}

    monkeypatch.setattr(qwen_worker, "Qwen", FakeQwen)
    monkeypatch.setattr(qwen_worker, "build_sample", build_sample)
    qwen_worker.understand(tmp_path)
    result = list(iter_jsonl(tmp_path / "results.jsonl"))[0]
    # Click-to-seek uses these authoritative POI fields, never generated evidence.
    for key in ("trackId", "trackSegmentId", "poiId", "triggerSec", "confirmedSec"):
        assert result[key] == candidate[key]
    assert len(calls) == (2 if mode in ("retry", "truncated") else 1)
    if mode in ("retry", "truncated"):
        assert "30个汉字" in calls[1]
        assert "parseError" in result["segments"][0]["attempts"][0]
    if mode == "truncated":
        assert result["inferenceStatus"] == "failed"
        assert "generation_truncated_or_timeout" in result["error"]
    else:
        assert result["inferenceStatus"] == "succeeded"
        assert result["decision"] == "match"
        assert result["segments"][0]["evidence"] == []
        assert read_json(tmp_path / "summary.json")["decisions"]["match"] == 1
