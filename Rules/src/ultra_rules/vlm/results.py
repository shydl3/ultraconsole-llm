from __future__ import annotations

import json

from pydantic import ValidationError

from .contracts import ConditionCheck, Evidence, ModelAnswer

POSTPROCESS_VERSION = "model-decision-v4"


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


def parse_answer(text: str, start: float, end: float, criteria_count: int, *, check_timestamps: bool = True):
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
    if "conditions" in data:
        checks, duplicates = {}, set()
        raw_checks = data["conditions"]
        if not isinstance(raw_checks, list):
            auxiliary_issue = True
        else:
            for item in raw_checks:
                try:
                    # Tolerate the observed legacy typo " index" without inferring an index.
                    if isinstance(item, dict) and " index" in item and "index" not in item:
                        item = {("index" if k == " index" else k): v for k, v in item.items()}
                        auxiliary_issue = True
                    check = ConditionCheck.model_validate(item)
                    if check.index >= criteria_count:
                        auxiliary_issue = True
                    elif check.index in checks:
                        duplicates.add(check.index)
                    else:
                        checks[check.index] = check
                except ValidationError:
                    auxiliary_issue = True
        auxiliary_issue |= bool(duplicates) or len(checks) != criteria_count
        answer.conditions = [checks[i] if i in checks and i not in duplicates else ConditionCheck(index=i, value="unknown")
                             for i in range(criteria_count)]
    if auxiliary_issue:
        answer._parse_warnings.append("auxiliary_output_incomplete")
    if check_timestamps and any(e.timeSec < start - 0.11 or e.timeSec > end + 0.11 for e in answer.evidence):
        raise ValueError("Evidence timestamp outside window")
    return answer


def verified_decision(answer: ModelAnswer, quality: dict):
    """Keep the model decision; quality findings are advisory, never vetoes.

    Requested context clipping is acquisition metadata, not a credibility signal.
    Business-specific uncertainty is evaluated by Qwen from the event definition.
    """
    reasons = []
    values = [c.value for c in answer.conditions]
    expected = "match" if all(v == "yes" for v in values) else "no_match" if "no" in values else "uncertain"
    if values and expected != answer.decision:
        reasons.append("inconsistent_conditions")
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


def apply_answer(part: dict, answer: ModelAnswer):
    """Shared by live inference and replay of saved model answers."""
    start, end = part["window"]["startSec"], part["window"]["endSec"]
    evidence = [e.model_dump() for e in answer.evidence if start - .11 <= e.timeSec <= end + .11]
    quality = part["quality"]
    quality["evidenceTimeOutsideWindow"] = len(evidence) != len(answer.evidence)
    decision, warnings = verified_decision(answer, quality)
    warnings = list(dict.fromkeys(warnings + answer._parse_warnings))
    part.update(decision=decision, modelDecision=answer.decision, modelDecisionConfidence=answer.confidence,
                reason=answer.reason, videoDescription=answer.videoDescription,
                evidence=evidence, modelEvidence=[e.model_dump() for e in answer.evidence],
                conditions=[c.model_dump() for c in answer.conditions], qualityWarnings=warnings,
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
    warnings = list(dict.fromkeys(w for s in segments for w in s.get("qualityWarnings", [])))
    if len(set(decisions)) > 1:
        warnings.append("different_segment_decisions")
    output.update(decision=decision, modelDecision=selected.get("modelDecision"), qualityWarnings=warnings,
                  downgradeReasons=[], selectedSegmentIndex=index, postprocessVersion=POSTPROCESS_VERSION)
