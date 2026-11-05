from __future__ import annotations

from fractions import Fraction

import av
import numpy as np
import pytest
from ultra_rules.vlm.media import probe
from ultra_rules.vlm.segmenting import (
    plan_segments,
    probe_keyframes,
    segment_video,
    write_segment,
)


def make_video_with_keyframes(path, frames=100, fps=25, gop=10):
    """Encode a clip forcing a keyframe every `gop` frames so cut points exist."""
    with av.open(str(path), "w") as output:
        stream = output.add_stream("libx264", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 96, 64, "yuv420p"
        stream.codec_context.time_base = Fraction(1, fps)
        stream.options = {"g": str(gop), "keyint_min": str(gop), "sc_threshold": "0"}
        for i in range(frames):
            f = av.VideoFrame.from_ndarray(np.full((64, 96, 3), i * 2 % 255, np.uint8), format="rgb24")
            f.pts = i
            f.time_base = Fraction(1, fps)
            for packet in stream.encode(f):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


def test_probe_keyframes_finds_forced_gop(tmp_path):
    path = tmp_path / "clip.mp4"
    make_video_with_keyframes(path, frames=100, fps=25, gop=10)
    keys = probe_keyframes(path)
    # Keyframes at 0, 10, 20, ... frames -> 0.0, 0.4, 0.8, ... seconds.
    assert keys[0] == pytest.approx(0.0)
    assert len(keys) >= 5
    assert all(b >= a for a, b in zip(keys, keys[1:], strict=False))


def test_plan_segments_short_video_is_single_range():
    assert plan_segments(30.0, [0.0, 10.0, 20.0], 600) == [(0.0, 30.0)]


def test_plan_segments_cuts_at_nearest_keyframe():
    keys = [round(i * 0.4, 6) for i in range(26)]  # 0..10s every 0.4s
    plan = plan_segments(10.0, keys, 3.0)
    assert plan[0][0] == 0.0
    assert plan[-1][1] == 10.0
    # Every interior cut must land on an actual keyframe.
    for start, _end in plan[1:]:
        assert start in keys
    # Segments are contiguous and cover the whole duration.
    for (_, prev_end), (next_start, _) in zip(plan, plan[1:], strict=False):
        assert prev_end == next_start
    assert all(end > start for start, end in plan)


def test_plan_segments_handles_sparse_keyframes():
    # Only two keyframes: cannot cut near the 3s target, falls back to fewer segments.
    plan = plan_segments(10.0, [0.0, 8.0], 3.0)
    assert plan[0][0] == 0.0 and plan[-1][1] == 10.0
    assert all(end > start for start, end in plan)


def test_write_segment_produces_independently_decodable_clip(tmp_path):
    source = tmp_path / "clip.mp4"
    make_video_with_keyframes(source, frames=100, fps=25, gop=10)
    meta = probe(source)
    target = tmp_path / "seg.mp4"
    # Cut [2.0, 3.0)s -> frames 50..74, starting on a forced keyframe.
    write_segment(source, target, 2.0, 3.0)
    seg_meta = probe(target)
    assert seg_meta["width"] == meta["width"] and seg_meta["height"] == meta["height"]
    with av.open(str(target)) as container:
        times = [float(f.time) for f in container.decode(video=0)]
    # First frame starts at 0 and the segment holds ~1s of frames.
    assert times[0] == pytest.approx(0.0, abs=1e-3)
    assert 0.9 <= times[-1] <= 1.1


def test_segment_video_single_file_for_short_clip(tmp_path):
    source = tmp_path / "clip.mp4"
    make_video_with_keyframes(source, frames=50, fps=25, gop=10)
    meta = probe(source)
    out_dir = tmp_path / "segments"
    segments = segment_video(source, meta, out_dir, target_seconds=600)
    assert len(segments) == 1
    assert segments[0]["singleFile"] is True
    assert segments[0]["path"] == str(source.resolve())
    assert segments[0]["startSec"] == 0.0


def test_segment_video_splits_long_clip_at_keyframes(tmp_path):
    source = tmp_path / "clip.mp4"
    make_video_with_keyframes(source, frames=100, fps=25, gop=10)  # 4s duration
    meta = probe(source)
    out_dir = tmp_path / "segments"
    segments = segment_video(source, meta, out_dir, target_seconds=1.5)
    assert len(segments) >= 2
    assert all(not s["singleFile"] for s in segments)
    # Contiguous coverage of the full duration.
    assert segments[0]["startSec"] == 0.0
    assert segments[-1]["endSec"] == pytest.approx(meta["durationSec"], abs=1e-3)
    for prev, nxt in zip(segments, segments[1:], strict=False):
        assert prev["endSec"] == nxt["startSec"]
    # Every physical segment exists and is independently decodable.
    for seg in segments:
        from pathlib import Path
        assert Path(seg["path"]).is_file()
        assert seg["sha256"]
        with av.open(seg["path"]) as container:
            assert sum(1 for _ in container.decode(video=0)) > 0
