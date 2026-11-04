from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from ultra_rules.vlm.contracts import CueDefinition, RunRequest
from ultra_rules.vlm.cues import _hysteresis, _red_ratio, _roi_mask, calibrate, detect_run, detect_timeline
from ultra_rules.vlm.storage import read_json


def cue():
    return CueDefinition(id="red", points=[[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]])


def test_hysteresis_persistence():
    c = CueDefinition(id="red", points=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]], minOnSec=0.3, minOffSec=0.2)
    samples = [(0.0, True), (0.2, True), (0.4, True), (0.6, False), (0.8, False), (1.0, False)]
    assert _hysteresis(samples, c) == [[0.0, 0.4]]


def test_roi_mask_and_red_ratio():
    c = cue()
    mask, bbox = _roi_mask(c, 64, 64)
    assert bbox == (16, 16, 49, 49)
    bgr = np.zeros((64, 64, 3), dtype=np.uint8)
    bgr[16:48, 16:48] = (0, 0, 255)  # red square inside ROI
    crop = bgr[bbox[1]:bbox[3], bbox[0]:bbox[2]]
    assert _red_ratio(crop, mask, c) > 0.9
    bgr[16:48, 16:48] = (255, 255, 255)  # white -> not red
    assert _red_ratio(crop, mask, c) == 0.0


def _make_video(path: Path, red_frames: set[int], fps=10, frames=20):
    with av.open(str(path), "w") as output:
        stream = output.add_stream("libx264", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 64, 64, "yuv420p"
        for i in range(frames):
            arr = np.full((64, 64, 3), 128, np.uint8)  # gray background
            if i in red_frames:
                arr[16:48, 16:48] = (0, 0, 255)  # BGR red
            frame = av.VideoFrame.from_ndarray(arr, format="bgr24")
            frame.pts = i
            frame.time_base = Fraction(1, fps)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


def test_detect_timeline_red_interval(tmp_path):
    src = tmp_path / "v.mp4"
    _make_video(src, red_frames=set(range(5, 15)))
    meta = {"width": 64, "height": 64, "fps": 10, "durationSec": 2.0,
            "timeOriginSec": 0.0, "timeBase": "1/10", "sha256": "x"}
    result = detect_timeline(src, meta, cue())
    assert len(result["intervals"]) == 1
    onset, offset = result["intervals"][0]
    assert 0.4 <= onset <= 0.6
    assert 1.3 <= offset <= 1.5


def test_calibrate_reports_ratio_stats_and_suggestion(tmp_path):
    src = tmp_path / "v.mp4"
    _make_video(src, red_frames=set(range(5, 15)))
    meta = {"width": 64, "height": 64, "fps": 10, "durationSec": 2.0,
            "timeOriginSec": 0.0, "timeBase": "1/10", "sha256": "x"}
    result = calibrate(src, meta, cue())
    assert result["roiPixelCount"] > 0
    assert result["sampleCount"] > 0
    assert result["maxRatio"] > 0.9
    assert result["minRatio"] < 0.01
    assert result["suggestedMinOnRatio"] is None or 0.0 <= result["suggestedMinOnRatio"] <= 1.0
    assert result["samples"][0][0] == 0.0
    assert result["peakFrame"]["redPixelCount"] > 0


def test_detect_run_without_cues(tmp_path):
    request = RunRequest(videoId="v1", pois=[{"id": "p", "kind": "line", "points": [[0, 0], [1, 1]]}],
                         events=[{"id": "e", "description": "x"}]).model_dump()
    (tmp_path / "request.json").write_text(
        '{"request": ' + __import__("json").dumps(request) + '}', encoding="utf-8")
    detect_run(tmp_path)
    assert read_json(tmp_path / "cues.json") == {"cues": {}}
