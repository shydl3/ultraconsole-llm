from __future__ import annotations

import json

from pydantic import ValidationError

from .contracts import ConditionCheck, Evidence, ModelAnswer

POSTPROCESS_VERSION = "model-decision-v5"


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def json_body(text: str):
    body = text.strip()
    if body.startswith("```"):
        # Generation may stop at the JSON closing brace before a closing fence.
        body = body.partition("\n")[2].strip()
        if body.endswith("```"):
            body = body[:-3].strip()
    return body


def complete_json_answer(text: str):
    """Recognize a complete top-level object, not braces inside quoted strings."""
    try:
        return isinstance(json.loads(json_body(text)), dict)
    except (ValueError, TypeError):
        return False


def parse_answer(text: str, start: float, end: float, criteria_count: int, *, trigger: float | None = None,
                 check_timestamps: bool = True):
    data = json.loads(json_body(text), object_pairs_hook=_unique_keys)
    if not isinstance(data, dict):
        raise ValueError("Model answer must be a JSON object")
    # Validate the decision independently of optional legacy evidence/conditions.
    answer = ModelAnswer.model_validate({key: data[key] for key in ("decision", "confidence", "reason", "videoDescription") if key in data})
    auxiliary_issue = bool(set(data) - {"decision", "confidence", "reason", "videoDescription", "evidence", "conditions"})
    if "evidence" in data:
        raw_evidence = data["evidence"]
        if not isinstance(raw_evidence, list):
            auxiliary_issue = True
        else:
            for item in raw_evidence[:8]:
                try:
                    answer.evidence.append(Evidence.model_validate(item))
                except ValidationError:
                    auxiliary_issue = True
            auxiliary_issue |= len(raw_evidence) > 8
    # conditions drive the final decision by conjunction; only a complete, well-formed
    # set is kept. Anything else falls back to the model's own decision downstream and
    # is reported specifically as conditions_unavailable (not a generic auxiliary issue).
    answer.conditions, condition_warnings = _parse_conditions(data, criteria_count, start, end, trigger)
    if auxiliary_issue:
        answer._parse_warnings.append("auxiliary_output_incomplete")
    answer._parse_warnings.extend(w for w in condition_warnings if w not in answer._parse_warnings)
    if check_timestamps and any(e.timeSec < start - 0.11 or e.timeSec > end + 0.11 for e in answer.evidence):
        raise ValueError("Evidence timestamp outside window")
    return answer


def _parse_conditions(data: dict, criteria_count: int, start: float, end: float, trigger: float | None):
    """Return (conditions, warnings). Empty conditions signal the conjunction fallback."""
    raw = data.get("conditions")
    if not isinstance(raw, list):
        return [], ["conditions_unavailable"]
    checks, duplicates, malformed = {}, set(), False
    for item in raw:
        try:
            if isinstance(item, dict):
                # Tolerate the observed legacy typo " index" and rename the documented
                # "status" key to the ConditionCheck field "value" (extra keys are forbidden).
                if " index" in item and "index" not in item:
                    item = {("index" if k == " index" else k): v for k, v in item.items()}
                if "status" in item and "value" not in item:
                    item = {("value" if k == "status" else k): v for k, v in item.items()}
            check = ConditionCheck.model_validate(item)
            if check.index >= criteria_count:
                malformed = True
            elif check.index in checks:
                duplicates.add(check.index)
            else:
                checks[check.index] = check
        except ValidationError:
            malformed = True
    if duplicates or malformed or criteria_count == 0 or len(checks) != criteria_count:
        return [], ["conditions_unavailable"]
    return _validate_condition_evidence([checks[i] for i in range(criteria_count)], start, end, trigger)


def _validate_condition_evidence(conditions, start: float, end: float, trigger: float | None):
    """Downgrade a yes/no verdict to unknown when its evidence time is unusable."""
    warnings, result = [], []
    for c in conditions:
        value, t = c.value, c.evidenceTimeSec
        if value in ("yes", "no"):
            if t is None:
                value, warn = "unknown", "missing_evidence_time"
            elif t < start - 0.11 or t > end + 0.11:
                value, warn = "unknown", "evidence_time_outside_window"
            elif value == "yes" and trigger is not None and abs(t - trigger) < 0.15:
                value, warn = "unknown", "evidence_time_equals_trigger"
            else:
                warn = None
            if warn and warn not in warnings:
                warnings.append(warn)
        result.append(ConditionCheck(index=c.index, value=value, evidenceTimeSec=t))
    return result, warnings


def verified_decision(answer: ModelAnswer, quality: dict):
    """Advisory quality findings only; never vetoes the model decision.

    Condition-conjunction now owns the yes/no/unknown -> decision mapping, so this
    function no longer re-derives an expected decision from conditions. Requested
    context clipping is acquisition metadata, not a credibility signal.
    """
    reasons = []
    if quality["targetVisibleFraction"] < 0.6:
        reasons.append("insufficient_target_visibility")
    if quality["uniqueFrameCount"] < 4:
        reasons.append("insufficient_frames")
    if quality["medianTargetHeightPx"] < 24:
        reasons.append("target_too_small")
    for key in ("identityAmbiguous", "timeUnreliable", "evidenceTimeOutsideWindow"):
        if quality.get(key):
            reasons.append(key)
    return answer.decision, reasons


def decision_from_conditions(conditions, criteria_count: int):
    """Conjunction truth table: any no -> no_match; all yes -> match; else uncertain.

    Returns (None, ["conditions_unavailable"]) when conditions are absent or do not
    exactly cover the criteria, signalling the caller to fall back to the model decision.
    """
    if criteria_count <= 0 or len(conditions) != criteria_count:
        return None, ["conditions_unavailable"]
    values = [c.value for c in conditions]
    if "no" in values:
        return "no_match", []
    if all(v == "yes" for v in values):
        return "match", []
    return "uncertain", []


def apply_answer(part: dict, answer: ModelAnswer, *, sample_times: list[float] | None = None,
                 source_indices: list[int] | None = None, trigger: float | None = None):
    """Shared by live inference and replay of saved model answers.

    The final decision comes from the per-condition conjunction when the model
    returned a usable conditions set; otherwise it falls back to the model's own
    decision plus advisory quality findings (legacy v4 behaviour).
    """
    start, end = part["window"]["startSec"], part["window"]["endSec"]
    evidence = [e.model_dump() for e in answer.evidence if start - .11 <= e.timeSec <= end + .11]
    quality = part["quality"]
    quality["evidenceTimeOutsideWindow"] = len(evidence) != len(answer.evidence)
    conditions = [c.model_dump() for c in answer.conditions]
    # Map each verdict's evidence second back to the nearest sampled source frame.
    if conditions and sample_times and source_indices and len(sample_times) == len(source_indices):
        for c in conditions:
            t = c.get("evidenceTimeSec")
            c["frameIndex"] = None if t is None else source_indices[
                min(range(len(sample_times)), key=lambda i: abs(sample_times[i] - t))]
    adv_decision, adv_reasons = verified_decision(answer, quality)
    conj_decision, conj_warnings = decision_from_conditions(answer.conditions, len(answer.conditions))
    if conj_decision is not None:
        decision = conj_decision
        extra = ["condition_decision_mismatch"] if conj_decision != answer.decision else []
    else:
        decision, extra = adv_decision, []
    warnings = list(dict.fromkeys(adv_reasons + conj_warnings + extra + answer._parse_warnings))
    part.update(decision=decision, modelDecision=answer.decision, modelDecisionConfidence=answer.confidence,
                reason=answer.reason, videoDescription=answer.videoDescription,
                evidence=evidence, modelEvidence=[e.model_dump() for e in answer.evidence],
                conditions=conditions, qualityWarnings=warnings,
                downgradeReasons=[], postprocessVersion=POSTPROCESS_VERSION)


def aggregate_result(output: dict):
    """A positive segment remains positive; negatives require all segments negative.

    Keep the selected segment's explanation/confidence/preview together rather
    than averaging uncalibrated confidence or attaching the first segment's data.
    """
    segments = output["segments"]
    decisions = [s["decision"] for s in segments]
    decision = "match" if "match" in decisions else "uncertain" if "uncertain" in decisions else "no_match"
    index = decisions.index(decision)
    selected = segments[index]
    for key in ("window", "quality", "previewPath", "sampleManifest", "reason", "modelDecisionConfidence"):
        output[key] = selected[key]
    output["videoDescription"] = selected.get("videoDescription")
    output["conditions"] = selected.get("conditions", [])
    warnings = list(dict.fromkeys(w for s in segments for w in s.get("qualityWarnings", [])))
    if len(set(decisions)) > 1:
        warnings.append("different_segment_decisions")
    output.update(decision=decision, modelDecision=selected.get("modelDecision"), qualityWarnings=warnings,
                  downgradeReasons=[], selectedSegmentIndex=index, postprocessVersion=POSTPROCESS_VERSION)
