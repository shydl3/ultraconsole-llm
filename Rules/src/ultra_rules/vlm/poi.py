from __future__ import annotations

import math
from dataclasses import dataclass

from ..geometry import point_in_polygon
from .contracts import RunRequest


def distance_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    u = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - a[0] - u * dx, p[1] - a[1] - u * dy)


def side(p, a, b):
    return ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) / math.dist(a, b)


def crossing_fraction(prev, cur, a, b):
    s1, s2 = side(prev, a, b), side(cur, a, b)
    if s1 * s2 >= 0:
        return None
    fraction = s1 / (s1 - s2)
    hit = [prev[i] + fraction * (cur[i] - prev[i]) for i in range(2)]
    return fraction if distance_segment(hit, a, b) < 1e-5 else None


@dataclass
class BoundaryState:
    stable: int | None = None
    point: tuple | None = None
    time: float = 0
    pending: int | None = None
    since: float = 0
    crossing: float | None = None
    observed: float = 0


class PoiTracker:
    def __init__(self, request: RunRequest):
        self.request = request
        self.tracks: dict[int, tuple[float, int, list, bool]] = {}
        self.states: dict[tuple, BoundaryState] = {}
        self.sequence = 0

    def update(self, frame: dict):
        t, w, h = frame["mediaTimeSec"], frame["width"], frame["height"]
        out = []
        for person in frame["persons"]:
            tid = person.get("trackId")
            if tid is None:
                continue
            box = person["bbox"]
            previous = self.tracks.get(tid)
            segment = previous[1] if previous else 1
            jump = False
            if previous:
                old = previous[2]
                jump = math.dist(((old[0] + old[2]) / 2, (old[1] + old[3]) / 2), ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)) > math.hypot(w, h) * 0.25
                if t - previous[0] > self.request.maxGapSeconds or jump:
                    segment += 1
                    for key in list(self.states):
                        if key[0] == tid:
                            del self.states[key]
            ambiguous = jump or bool(previous and segment == previous[1] and previous[3])
            self.tracks[tid] = (t, segment, box, ambiguous)
            person["trackSegmentId"] = f"{tid}-{segment}"
            person["identityAmbiguous"] = ambiguous
            for poi in self.request.pois:
                point = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2 if poi.anchor == "bbox_center" else box[3])
                points = [(x * w, y * h) for x, y in poi.points]
                margin = self.request.hysteresisPx * min(w / 1920, h / 1080)
                if poi.kind == "line":
                    signed = side(point, *points)
                    value = 1 if signed > margin else -1 if signed < -margin else None
                else:
                    distance = min(distance_segment(point, a, b) for a, b in zip(points, points[1:] + points[:1], strict=True))
                    value = int(point_in_polygon(point, points)) if distance > margin else None
                state = self.states.setdefault((tid, segment, poi.id), BoundaryState())
                if t - state.observed > max(0.3, 2.5 / self.request.poseFps):
                    state.pending = None
                state.observed = t
                if value is None:
                    state.pending = None
                    continue
                if value == state.stable:
                    state.point, state.time, state.pending = point, t, None
                    continue
                if value != state.pending:
                    state.pending, state.since = value, t
                    state.crossing = None
                    if poi.kind == "line" and state.point is not None:
                        fraction = crossing_fraction(state.point, point, *points)
                        if fraction is not None:
                            state.crossing = state.time + fraction * (t - state.time)
                if t - state.since + 1e-6 < self.request.stableSeconds:
                    continue
                kind, direction = None, None
                if poi.kind == "line" and state.stable is not None and state.crossing is not None:
                    kind = "line.cross"
                    direction = "negative_to_positive" if value == 1 else "positive_to_negative"
                elif poi.kind == "zone":
                    if state.stable is None:
                        kind = "initial_inside" if value else None
                    else:
                        kind = "zone.enter" if value else "zone.leave"
                if kind:
                    self.sequence += 1
                    trigger = state.crossing if kind == "line.cross" else state.since
                    out.append({"candidateId": f"c{self.sequence:06d}", "trackId": tid,
                                "trackSegmentId": person["trackSegmentId"], "poiId": poi.id,
                                "triggerKind": kind, "direction": direction, "triggerSec": trigger,
                                "confirmedSec": t, "identityAmbiguous": ambiguous})
                state.stable, state.point, state.time, state.pending = value, point, t, None
        return out
