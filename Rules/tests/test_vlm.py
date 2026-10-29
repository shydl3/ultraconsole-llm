from __future__ import annotations

import json
from fractions import Fraction

import av
import numpy as np
import pytest
from pydantic import ValidationError
from ultra_rules.vlm.contracts import EventDefinition, RunRequest
from ultra_rules.vlm.media import decoded, probe, sampled
from ultra_rules.vlm.poi import PoiTracker
from ultra_rules.vlm.results import parse_answer, verified_decision
from ultra_rules.vlm.sampling import choose_rows, source_images, windows


def request(kind="line", **kwargs):
    points = [[0.5, 0.2], [0.5, 0.8]] if kind == "line" else [[0.4, 0.2], [0.8, 0.2], [0.8, 0.8], [0.4, 0.8]]
    return RunRequest(videoId="v1", pois=[{"id": "gate", "kind": kind, "points": points}],
                      events=[{"id": "e1", "description": "test"}], stableSeconds=0, hysteresisPx=0, **kwargs)


def frame(t, x, y=0.5, tid=1):
    return {"mediaTimeSec": t, "width": 1000, "height": 1000,
            "persons": [] if x is None else [{"trackId": tid, "bbox": [x * 1000 - 10, y * 1000 - 10, x * 1000 + 10, y * 1000 + 10]}]}


def test_line_finite_segment_both_directions_and_extension():
    tracker = PoiTracker(request())
    assert not tracker.update(frame(0, .4))
    ev = tracker.update(frame(.1, .6))
    assert len(ev) == 1 and ev[0]["direction"] == "positive_to_negative"
    assert ev[0]["triggerSec"] == pytest.approx(.05)
    assert len(tracker.update(frame(.2, .4))) == 1
    other = PoiTracker(request())
    other.update(frame(0, .4, .95))
    assert not other.update(frame(.1, .6, .95))


def test_dead_band_and_stability_do_not_duplicate():
    cfg = request()
    cfg.stableSeconds = .2
    cfg.hysteresisPx = 10
    tracker = PoiTracker(cfg)
    for t in (0, .1, .2):
        assert not tracker.update(frame(t, .4))
    assert not tracker.update(frame(.3, .499))
    assert not tracker.update(frame(.4, .501))
    assert not tracker.update(frame(.5, .6))
    assert not tracker.update(frame(.6, .6))
    assert len(tracker.update(frame(.7, .6))) == 1
    assert not tracker.update(frame(.8, .6))


def test_missing_observation_is_not_leave_and_long_gap_splits_identity():
    tracker = PoiTracker(request("zone"))
    assert tracker.update(frame(0, .5))[0]["triggerKind"] == "initial_inside"
    assert not tracker.update(frame(.1, None))
    assert not tracker.update(frame(.2, .5))
    assert tracker.update(frame(.3, .3))[0]["triggerKind"] == "zone.leave"
    event = tracker.update(frame(2, .5))[0]
    assert event["triggerKind"] == "initial_inside"
    assert event["trackSegmentId"] == "1-2"


def test_multiple_people_and_independent_runs():
    tracker = PoiTracker(request())
    tracker.update(frame(0, .4, tid=1))
    tracker.update(frame(0, .6, tid=2))
    assert tracker.update(frame(.1, .6, tid=1))[0]["trackId"] == 1
    assert tracker.update(frame(.1, .4, tid=2))[0]["trackId"] == 2
    assert not PoiTracker(request()).update(frame(.1, .6, tid=1))


def make_video(path, fps=25, vfr=False):
    with av.open(str(path), "w") as output:
        stream = output.add_stream("libx264", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 96, 64, "yuv420p"
        for i in range(fps * 2):
            f = av.VideoFrame.from_ndarray(np.full((64, 96, 3), i * 3 % 255, np.uint8), format="rgb24")
            f.pts = 250 + i * 2 + (i // 3 if vfr else 0)
            f.time_base = Fraction(1, fps * 2)
            for packet in stream.encode(f):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


@pytest.mark.parametrize("fps,vfr", [(25, False), (30, False), (25, True)])
def test_timestamps_sampling_and_exact_frame_retrieval(tmp_path, fps, vfr):
    path = tmp_path / "clip.mp4"
    make_video(path, fps, vfr)
    meta = probe(path)
    all_frames = list(decoded(path, meta))
    assert all_frames[0][1] == pytest.approx(0)
    assert meta["timeOriginSec"] > 0
    rows = []
    for index, t, time_source, f in sampled(path, meta, 10):
        rows.append({"frameIndex": index, "mediaTimeSec": t, "sourcePtsSec": float(f.time), "timeSource": time_source})
    if not vfr:
        assert len(rows) == 20
    assert all(b["mediaTimeSec"] > a["mediaTimeSec"] for a, b in zip(rows, rows[1:], strict=False))
    selected = choose_rows(rows, 0, rows[-1]["mediaTimeSec"], .9, 8, 8)
    assert len(selected) <= 8
    assert selected[0] == rows[0] and selected[-1] == rows[-1]
    images = source_images(path, meta, selected)
    for r in selected:
        assert np.array_equal(np.array(images[r["frameIndex"]]), all_frames[r["frameIndex"]][3].to_ndarray(format="rgb24"))


def test_window_split_and_clipping():
    event = EventDefinition(id="e", description="test", preSeconds=20, postSeconds=20)
    chunks = windows(25, event, 100)
    assert chunks[0][0] == 5 and chunks[-1][1] == 45
    assert all(b - a <= 16 for a, b in chunks)
    assert chunks[1][0] < chunks[0][1]


@pytest.mark.parametrize("maximum", [4, 6, 32, 64])
def test_sampling_obeys_configured_cap_at_minimum_and_preserves_trigger(maximum):
    rows = [{"mediaTimeSec": i / 10, "frameIndex": i} for i in range(101)]
    selected = choose_rows(rows, 0, 10, 5, 8, maximum)
    assert len(selected) <= maximum
    assert selected[0] == rows[0]
    assert selected[-1] == rows[-1]
    assert rows[50] in selected
    assert len({row["frameIndex"] for row in selected}) == len(selected)


def test_strict_output_and_advisory_quality():
    body = {"decision": "match", "confidence": .8, "reason": "visible",
            "evidence": [{"timeSec": 2, "observation": "action"}], "conditions": [{"index": 0, "value": "yes"}]}
    answer = parse_answer(json.dumps(body), 1, 3, 1)
    quality = {"targetVisibleFraction": 1, "medianTargetHeightPx": 100, "uniqueFrameCount": 10}
    assert verified_decision(answer, quality) == ("match", [])
    assert verified_decision(answer, {**quality, "postContextClipped": True}) == ("match", [])
    body["conditions"][0]["value"] = "no"
    assert "inconsistent_conditions" in verified_decision(parse_answer(json.dumps(body), 1, 3, 1), quality)[1]
    body["evidence"][0]["timeSec"] = 99
    with pytest.raises(ValueError, match="timestamp"):
        parse_answer(json.dumps(body), 1, 3, 1)
    # Out-of-window citations are advisory and do not overwrite the model decision.
    tolerant = parse_answer(json.dumps(body), 1, 3, 1, check_timestamps=False)
    assert verified_decision(tolerant, {**quality, "evidenceTimeOutsideWindow": True}) == (
        "match", ["inconsistent_conditions", "evidenceTimeOutsideWindow"])
    body["confidence"] = float("nan")
    with pytest.raises(ValidationError):
        parse_answer(json.dumps(body), 1, 3, 1)


def test_config_rejects_duplicate_ids_invalid_references_and_empty_geometry():
    cfg = request().model_dump()
    cfg["events"][0]["poiIds"] = ["missing"]
    with pytest.raises(ValidationError):
        RunRequest.model_validate(cfg)
    cfg = request().model_dump()
    cfg["pois"] *= 2
    with pytest.raises(ValidationError):
        RunRequest.model_validate(cfg)
