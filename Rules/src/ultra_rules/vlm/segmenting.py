"""Keyframe-aligned video segmentation via packet-level remux (no re-encode).

Splits a long video into independently decodable MP4 segments so that the VLM
understanding phase and browser preview can process one bounded slice at a
time. Tracking and cue detection intentionally stay on the full timeline
because PoiTracker and red-light hysteresis accumulate state across frames.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import av

from .storage import file_hash, progress, read_json, write_json

SEGMENT_VERSION = "segments-v1"
DEFAULT_SEGMENT_SECONDS = int(os.environ.get("VLM_SEGMENT_SECONDS", "600"))


def probe_keyframes(source: Path) -> list[float]:
    """Return sorted PTS seconds of every video keyframe."""
    times: list[float] = []
    with av.open(str(source)) as container:
        if not container.streams.video:
            raise ValueError("No video stream")
        stream = container.streams.video[0]
        time_base = stream.time_base
        for packet in container.demux(stream):
            if packet.is_keyframe and packet.pts is not None:
                times.append(float(packet.pts * time_base))
    if not times:
        raise ValueError("Video has no keyframes")
    return sorted(times)


def plan_segments(duration: float, keyframe_times: list[float],
                  target_seconds: float = DEFAULT_SEGMENT_SECONDS) -> list[tuple[float, float]]:
    """Cut at the keyframe nearest each target boundary; first cut is always 0."""
    if duration <= 0:
        raise ValueError("Video has no duration")
    if duration <= target_seconds or len(keyframe_times) < 2:
        return [(0.0, duration)]
    cuts = [0.0]
    while cuts[-1] + target_seconds < duration:
        target = cuts[-1] + target_seconds
        candidates = [k for k in keyframe_times if cuts[-1] < k < duration]
        if not candidates:
            break
        cuts.append(min(candidates, key=lambda k: abs(k - target)))
    cuts.append(duration)
    return [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]


def fixed_segments(duration: float, target_seconds: float = DEFAULT_SEGMENT_SECONDS) -> list[tuple[float, float]]:
    """Even time buckets for logical (understanding) grouping.

    Logical buckets only decide which candidates are processed together, so they
    do not need keyframe alignment; skipping the full-file keyframe scan keeps the
    start of a 12-24h run immediate instead of demuxing tens of GB first.
    """
    if duration <= 0:
        raise ValueError("Video has no duration")
    segments: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        end = min(float(duration), start + target_seconds)
        segments.append((start, end))
        start = end
    return segments


def write_segment(source: Path, target: Path, start_sec: float, end_sec: float) -> None:
    """Remux packets in [start_sec, end_sec) into a standalone faststart MP4."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(source)) as incoming:
        stream = incoming.streams.video[0]
        time_base = stream.time_base
        start_pts = int(start_sec / time_base)
        end_pts = int(end_sec / time_base)
        stream.thread_type = "NONE"
        incoming.seek(max(0, start_pts), stream=stream, backward=True)
        with av.open(str(target), "w", format="mp4",
                     options={"movflags": "+faststart"}) as outgoing:
            # PyAV >=14 renamed the remux helper; fall back for older wheels.
            if hasattr(outgoing, "add_stream_from_template"):
                out_stream = outgoing.add_stream_from_template(stream)
            else:
                out_stream = outgoing.add_stream(template=stream)
            offset: int | None = None
            for packet in incoming.demux(stream):
                if packet.pts is None:
                    continue
                if packet.pts >= end_pts:
                    break
                if packet.pts < start_pts:
                    continue
                if offset is None:
                    offset = packet.pts
                packet.pts -= offset
                if packet.dts is not None:
                    packet.dts -= offset
                packet.stream = out_stream
                outgoing.mux(packet)
            if offset is None:
                raise ValueError(f"Segment {start_sec}-{end_sec}s has no packets")


def segment_video(source: Path, meta: dict, out_dir: Path,
                  target_seconds: float = DEFAULT_SEGMENT_SECONDS) -> list[dict]:
    """Split source into keyframe-aligned segments; returns segment manifests.

    When the video is shorter than target_seconds a single pseudo-segment
    pointing at the original file is returned without copying any bytes.
    """
    duration = float(meta["durationSec"])
    keyframes = probe_keyframes(source)
    plan = plan_segments(duration, keyframes, target_seconds)
    out_dir.mkdir(parents=True, exist_ok=True)
    segments: list[dict] = []
    for index, (start, end) in enumerate(plan):
        if len(plan) == 1:
            path = source.resolve()
            single = True
        else:
            path = (out_dir / f"seg-{index:03d}.mp4").resolve()
            write_segment(source, path, start, end)
            single = False
        segments.append({
            "index": index, "startSec": round(start, 6), "endSec": round(end, 6),
            "durationSec": round(end - start, 6), "path": str(path),
            "singleFile": single, "sha256": None if single else file_hash(path),
        })
    return segments


def write_manifest(run_dir: Path, segments: list[dict], source_sha: str, mode: str = "physical") -> None:
    write_json(run_dir / "segments.json", {
        "version": SEGMENT_VERSION, "sourceSha256": source_sha,
        "mode": mode, "count": len(segments), "segments": segments,
    })


def read_manifest(run_dir: Path) -> dict:
    path = run_dir / "segments.json"
    if not path.exists():
        return {"version": SEGMENT_VERSION, "count": 0, "segments": []}
    return json.loads(path.read_text(encoding="utf-8"))


def run_segment_phase(run_dir: Path) -> None:
    """Idempotent runner phase: plan logical time-range segments for understanding.

    Analysis stays in global time: observations.sqlite holds whole-video PTS and
    frame indices, and build_sample seeks the full source per candidate. So the
    understanding phase only needs [startSec, endSec) buckets to group candidates
    and checkpoint progress; no physical split is performed here. Physical segment
    files are produced separately by the browser preview path.
    """
    config = read_json(run_dir / "request.json")
    meta = config["video"]
    existing = read_manifest(run_dir)
    if existing.get("segments"):
        progress(run_dir, stage="segment", segmentCount=len(existing["segments"]), segmentCacheHit=True)
        return
    duration = float(meta["durationSec"])
    plan = fixed_segments(duration, DEFAULT_SEGMENT_SECONDS)
    segments = [{"index": i, "startSec": round(s, 6), "endSec": round(e, 6),
                 "durationSec": round(e - s, 6)} for i, (s, e) in enumerate(plan)]
    write_manifest(run_dir, segments, meta["sha256"], mode="logical")
    progress(run_dir, stage="segment", segmentCount=len(segments), segmentCacheHit=False,
             segmentSeconds=DEFAULT_SEGMENT_SECONDS)
