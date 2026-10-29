"""One decoder/time base for tracking and model samples (including VFR)."""
from __future__ import annotations

import math
from pathlib import Path

import av


def probe(path: Path):
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("No video stream")
        stream = container.streams.video[0]
        rotation = int(float(stream.metadata.get("rotate", "0"))) % 360
        if rotation:
            raise ValueError("Please normalize video rotation before upload")
        fps = float(stream.average_rate or stream.guessed_rate or 25)
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else float(container.duration or 0) / av.time_base
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Cannot determine video duration")
        first = next(container.decode(stream), None)
        if first is None:
            raise ValueError("Video has no decodable frames")
        if getattr(first, "rotation", 0):
            raise ValueError("Please normalize video rotation before upload")
        return {"width": first.width, "height": first.height, "fps": fps, "durationSec": duration,
                "frameCount": stream.frames, "timeOriginSec": float(first.time or 0), "timeBase": str(stream.time_base)}


def decoded(path: Path, meta: dict):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        last = -1.0
        for index, frame in enumerate(container.decode(stream)):
            if frame.time is None:
                timestamp, source = index / meta["fps"], "frame_index"
            else:
                timestamp, source = float(frame.time) - meta["timeOriginSec"], "pts"
            if timestamp < last - 1e-6:
                raise ValueError("Non-monotonic video timestamps; remux video before analysis")
            last = timestamp
            yield index, timestamp, source, frame


def sampled(path: Path, meta: dict, fps: float):
    target = 0.0
    for index, timestamp, source, frame in decoded(path, meta):
        if timestamp + 1e-6 < target:
            continue
        yield index, timestamp, source, frame
        target = (math.floor(timestamp * fps + 1e-6) + 1) / fps
