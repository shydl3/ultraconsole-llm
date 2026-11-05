from __future__ import annotations

from ultra_rules.vlm.contracts import EventDefinition
from ultra_rules.vlm.prompting import PROMPT_VERSION, prompt


def candidate():
    return {"trackId": 1, "triggerKind": "line.cross", "triggerSec": 2.0,
            "direction": "positive_to_negative"}


def manifest():
    return {"window": {"startSec": 1.0, "endSec": 3.0}, "quality": {"uniqueFrameCount": 6},
            "observations": [{"mediaTimeSec": 1.0, "otherPeople": []},
                             {"mediaTimeSec": 2.0, "otherPeople": []}]}


def event(**kwargs):
    return EventDefinition(id="e", description="目标进入安检门", **kwargs)


def test_prompt_version_is_v6():
    assert PROMPT_VERSION == "target-event-described-json-v6"


def test_prompt_enforces_default_denial_and_conditions():
    text = prompt(candidate(), event(criteria=["跨过入口线", "门框红灯亮起"]), manifest())
    # Default-denial wording replaces the old "not seen != not happened" phrasing.
    assert "默认否认" in text
    # Structured per-condition output with three-state status and evidence time.
    assert "conditions" in text and "evidenceTimeSec" in text and "yes或no或unknown" in text
    # A yes verdict must not reuse the POI trigger time as its evidence.
    assert "triggerSec" in text
    # Appearance/clothing is explicitly banned; the legacy "可辨认特征" hook is gone.
    assert "衣着" in text
    assert "可辨认特征" not in text
    # Criteria are injected verbatim so the model can map conditions by index.
    assert "跨过入口线" in text and "门框红灯亮起" in text


def test_prompt_declares_field_order_and_three_few_shots():
    text = prompt(candidate(), event(criteria=["c0", "c1"]), manifest())
    # The five output fields are declared in the required order.
    assert text.index("videoDescription") < text.index("conditions") < text.index("decision")
    # Exactly three few-shot examples, one per decision label.
    assert text.count('"conditions":[') == 3
    assert "示例一(no_match)" in text and "示例二(match)" in text and "示例三(uncertain)" in text
    assert "示例四" not in text
    # Generation must stop right after the closing brace.
    assert "输出右花括号后立即结束" in text


def test_prompt_retry_demands_complete_conditions():
    text = prompt(candidate(), event(criteria=["c0"]), manifest(), retry=True)
    assert "conditions数组覆盖全部判定条件" in text


def test_prompt_includes_cue_note_when_provided():
    text = prompt(candidate(), event(), manifest(), cue_evidence={"state": "off", "onsetSec": None})
    assert "门框红灯状态=off" in text
