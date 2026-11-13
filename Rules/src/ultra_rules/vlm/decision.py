"""Deterministic event verdicts for cue-bound rules.

Runs in the Qwen worker process but only reads precomputed cue timelines and
pose observations; it never decodes video or touches the model. When a rule
binds cues (event.cueIds), the decision is derived from measurable predicates
and the VLM is consulted only for uncertain cases.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from .contracts import EventDefinition, RunRequest
from .storage import read_json

RETREAT_SECONDS = 5.0


def retreat_candidate_ids(candidates: list[dict]) -> set[str]:
    """The first opposite line.cross within RETREAT_SECONDS is a retreat, not a new entry."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for c in candidates:
        if c.get("triggerKind") == "line.cross":
            groups[(c.get("trackSegmentId"), c.get("poiId"))].append(c)
    skipped: set[str] = set()
    for group in groups.values():
        group.sort(key=lambda c: c["triggerSec"])
        pending = None
        for c in group:
            if pending is None:
                pending = c
                continue
            if c.get("direction") != pending.get("direction") and c["triggerSec"] - pending["triggerSec"] <= RETREAT_SECONDS:
                skipped.add(c["candidateId"])
                pending = None
            else:
                pending = c
    return skipped


def candidate_groups(candidates: list[dict]) -> dict[tuple, list[dict]]:
    """Index candidates by track segment and POI so retreat lookups stay O(1).

    Long recordings produce tens of thousands of candidates; scanning the whole
    list per verdict would make cue-bound analysis quadratic.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for c in candidates:
        groups[(c.get("trackSegmentId"), c.get("poiId"))].append(c)
    return groups


def load_cue_timelines(run_dir: Path) -> dict:
    path = run_dir / "cues.json"
    if not path.exists():
        return {}
    return read_json(path).get("cues", {})


def red_state(timelines: dict, cue_ids: list[str], entry_t: float, pre: float, post: float):
    """Return (state, onset) where state is 'on'/'off'/'unknown'."""
    if not cue_ids:
        return "off", None
    intervals: list[list[float]] = []
    for cid in cue_ids:
        if cid not in timelines:
            return "unknown", None
        intervals.extend(timelines[cid].get("intervals", []))
    window_lo = entry_t - pre
    window_hi = entry_t + post
    hits = [iv for iv in intervals if iv[1] >= window_lo and iv[0] <= window_hi]
    if not hits:
        return "off", None
    best = min(hits, key=lambda iv: abs(iv[0] - entry_t))
    return "on", best[0]


def _retreated(candidate: dict, grouped: dict[tuple, list[dict]], start: float, end: float) -> bool:
    seg, poi = candidate["trackSegmentId"], candidate["poiId"]
    for other in grouped.get((seg, poi), []):
        if other["candidateId"] == candidate["candidateId"]:
            continue
        if other["trackSegmentId"] != seg or other["poiId"] != poi:
            continue
        if not (start <= other["triggerSec"] <= end):
            continue
        if candidate["triggerKind"] == "line.cross":
            if other["triggerKind"] == "line.cross" and other.get("direction") and other["direction"] != candidate.get("direction"):
                return True
        elif candidate["triggerKind"] == "zone.enter":
            if other["triggerKind"] == "zone.leave":
                return True
    return False


def _tracked_through(run_dir: Path, candidate: dict, start: float, end: float, max_gap: float) -> bool:
    db = sqlite3.connect(run_dir / "observations.sqlite")
    try:
        rows = db.execute(
            "SELECT time, body FROM observations WHERE time >= ? AND time <= ? ORDER BY time",
            (start - max_gap, end + max_gap)).fetchall()
    finally:
        db.close()
    seg = candidate["trackSegmentId"]
    times = [t for t, body in rows if any(p.get("trackSegmentId") == seg for p in json.loads(body).get("persons", []))]
    if not times:
        return False
    if times[0] > start + max_gap or times[-1] < end - max_gap:
        return False
    return all(b - a <= max_gap for a, b in zip(times, times[1:], strict=False))


def evaluate(candidate: dict, event: EventDefinition, request: RunRequest, run_dir: Path,
             candidates: list[dict] | None = None, *, grouped: dict[tuple, list[dict]] | None = None,
             timelines: dict | None = None):
    if not event.cueIds:
        return None
    # The understanding phase passes preloaded timelines/grouping so a multi-hour
    # run does not re-read cues.json or rescan candidates for every verdict.
    timelines = load_cue_timelines(run_dir) if timelines is None else timelines
    grouped = candidate_groups(candidates or []) if grouped is None else grouped
    entry_t = candidate["triggerSec"]
    state, onset = red_state(timelines, event.cueIds, entry_t, event.preSeconds, event.postSeconds)
    cue_evidence = {"cueIds": event.cueIds, "state": state, "onsetSec": onset}
    if state == "off":
        return {"decision": "no_match", "reason": "检测窗口内门框红灯未亮起",
                "cueEvidence": cue_evidence, "reviewRequired": False}
    if state == "unknown":
        return {"decision": "uncertain", "reason": "红灯检测不确定，转 VLM 复核",
                "cueEvidence": cue_evidence, "reviewRequired": True}
    window_end = onset + RETREAT_SECONDS
    if _retreated(candidate, grouped, entry_t, window_end):
        return {"decision": "no_match", "reason": "红灯亮起后 5 秒内目标退回原区域",
                "cueEvidence": cue_evidence, "reviewRequired": False}
    if not _tracked_through(run_dir, candidate, entry_t, window_end, request.maxGapSeconds):
        return {"decision": "uncertain", "reason": "5 秒窗口内目标跟踪缺失，无法确认是否退回",
                "cueEvidence": cue_evidence, "reviewRequired": True}
    return {"decision": "match", "reason": "门框红灯亮起且 5 秒内未退回",
            "cueEvidence": cue_evidence, "reviewRequired": False}
