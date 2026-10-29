"""观测质量与未知原因记录。

空关键点不等于无人；显式给出未知原因，供上层规则区分"未发生"与"看不到"。
"""
from __future__ import annotations

from typing import Any

from .contracts import PersonObservation


def classify_person_quality(p: PersonObservation, *, min_conf: float) -> dict[str, Any]:
    """对单个 PersonObservation 输出质量标签。"""
    reasons: list[str] = []
    kps = p.keypoints
    n_valid = sum(1 for k in kps if k.conf is not None and k.conf >= min_conf)
    if not kps:
        reasons.append("no_keypoints")
    if n_valid < 5:
        reasons.append(f"low_valid_keypoints({n_valid})")
    if p.track_id is None:
        reasons.append("no_track_id")
    if p.bbox_conf < 0.35:
        reasons.append(f"low_bbox_conf({p.bbox_conf:.2f})")
    x1, y1, x2, y2 = p.bbox
    if (x2 - x1) < 16 or (y2 - y1) < 32:
        reasons.append("bbox_too_small")
    return {"valid_keypoints": n_valid, "reasons": reasons, "usable": not reasons}


def frame_quality(persons: list[PersonObservation], *, min_conf: float) -> dict[str, Any]:
    """聚合帧级质量指标。"""
    per_person = [classify_person_quality(p, min_conf=min_conf) for p in persons]
    usable = sum(1 for q in per_person if q["usable"])
    return {
        "person_count": len(persons),
        "usable_persons": usable,
        "with_track_id": sum(1 for p in persons if p.track_id is not None),
        "per_person": per_person,
    }
