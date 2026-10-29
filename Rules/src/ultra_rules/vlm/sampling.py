from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw

from .contracts import EventDefinition, RunRequest
from .media import decoded
from .storage import write_json


def windows(trigger: float, event: EventDefinition, duration: float):
    start, end = max(0, trigger - event.preSeconds), min(duration, trigger + event.postSeconds)
    if end <= start:
        raise ValueError("Candidate has no video context")
    result = []
    while start < end:
        stop = min(end, start + 16)
        result.append((start, stop))
        if stop == end:
            break
        start = stop - 2
    return result


def choose_rows(rows: list[dict], start: float, end: float, trigger: float, fps: float, maximum: int):
    if not rows:
        raise ValueError("No observations in candidate window")
    times = np.array([row["mediaTimeSec"] for row in rows])
    targets = np.arange(start, end, max(1 / fps, (end - start) / max(1, maximum - 4)))
    # Reserve source boundaries and trigger neighbours before uniform coverage.
    special = [times[0], times[-1], trigger, trigger - 0.15, trigger + 0.15]
    picked = set()
    for t in special:
        if len(picked) >= maximum:
            break
        if start <= t <= end:
            picked.add(int(np.argmin(abs(times - t))))
    for target in targets:
        if len(picked) >= maximum:
            break
        picked.add(int(np.argmin(abs(times - target))))
    return [rows[i] for i in sorted(picked)]


def source_images(path: Path, meta: dict, rows: list[dict]):
    """Seek by PTS, then match exact PTS; fallback uses exact decoded frame index."""
    if all(row["sourcePtsSec"] is not None for row in rows):
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            container.seek(int(rows[0]["sourcePtsSec"] / stream.time_base), stream=stream, backward=True)
            wanted = {round(row["sourcePtsSec"], 6): row["frameIndex"] for row in rows}
            images = {}
            for frame in container.decode(stream):
                if frame.time is None:
                    continue
                t = float(frame.time)
                if t > rows[-1]["sourcePtsSec"] + 0.001:
                    break
                key = round(t, 6)
                if key in wanted:
                    images[wanted[key]] = frame.to_image()
            if len(images) == len(rows):
                return images
    wanted = {row["frameIndex"] for row in rows}
    images = {}
    for index, _time, _source, frame in decoded(path, meta):
        if index in wanted:
            images[index] = frame.to_image()
        if index >= max(wanted):
            break
    if len(images) != len(rows):
        raise ValueError("unaligned_frame: source frames differ from observation cache")
    return images


def build_sample(run_dir: Path, config: dict, request: RunRequest, candidate: dict,
                 start: float, end: float, sample_id: str):
    with sqlite3.connect(run_dir / "observations.sqlite") as db:
        rows = [json.loads(row[0]) for row in db.execute(
            "SELECT body FROM observations WHERE time >= ? AND time <= ? ORDER BY time, frame_index", (start, end))]
    selected = choose_rows(rows, start, end, candidate["triggerSec"], request.sampleFps, request.maxFrames)
    images = source_images(Path(config["sourcePath"]), config["video"], selected)
    w, h = config["video"]["width"], config["video"]["height"]
    scale = min(1, math.sqrt(request.maxPixels / (w * h)))
    width, height = max(32, int(w * scale / 32) * 32), max(32, int(h * scale / 32) * 32)
    frames, times, summaries, visible, sizes = [], [], [], 0, []
    ambiguous, time_unreliable = candidate["identityAmbiguous"], False
    poi = next(p for p in request.pois if p.id == candidate["poiId"])
    for row in selected:
        im = images.pop(row["frameIndex"]).resize((width, height), Image.Resampling.BICUBIC)
        draw = ImageDraw.Draw(im)
        polygon = [(x * width, y * height) for x, y in poi.points]
        draw.line(polygon + (polygon[:1] if poi.kind == "zone" else []), fill="#ffdd44", width=1)
        target = next((p for p in row["persons"] if p.get("trackSegmentId") == candidate["trackSegmentId"]), None)
        neighbors = []
        for p in row["persons"]:
            x1, y1, x2, y2 = p["bbox"]
            bbox = (x1 * width / w, y1 * height / h, x2 * width / w, y2 * height / h)
            is_target = p is target
            draw.rectangle(bbox, outline="#ff4466" if is_target else "#88bbaa", width=2 if is_target else 1)
            label = f"TARGET #{candidate['trackId']}" if is_target else f"#{p.get('trackId', '?')}"
            label_width = draw.textlength(label)
            draw.text((max(0, min(width - label_width - 2, bbox[0])), max(0, bbox[1] - 12)), label,
                      fill="#ff4466" if is_target else "#88bbaa")
            if not is_target:
                neighbors.append({"trackId": p.get("trackId"), "bbox": [round(v, 3) for v in (x1 / w, y1 / h, x2 / w, y2 / h)]})
        if target:
            visible += 1
            x1, y1, x2, y2 = target["bbox"]
            sizes.append((y2 - y1) * height / h)
            ambiguous = ambiguous or target.get("identityAmbiguous", False)
            valid = [k for k in target["keypoints"] if k.get("conf") is not None and k["conf"] >= 0.5]
            wrists = {name: {"x": round(target["keypoints"][i]["x"] / w, 3), "y": round(target["keypoints"][i]["y"] / h, 3)}
                      for name, i in (("leftWrist", 9), ("rightWrist", 10))
                      if len(target["keypoints"]) > i and (target["keypoints"][i].get("conf") or 0) >= 0.5}
            summary = {"bbox": [round(v, 3) for v in (x1 / w, y1 / h, x2 / w, y2 / h)],
                       "bboxConf": round(target["bboxConf"], 3), "validKeypoints": len(valid), **wrists}
        else:
            summary = None
        times.append(row["mediaTimeSec"])
        time_unreliable = time_unreliable or row["timeSource"] != "pts"
        summaries.append({"timeSec": row["mediaTimeSec"], "target": summary, "otherPeople": neighbors})
        frames.append(np.asarray(im))
    original_count = len(frames)
    if original_count % 2:
        frames.append(frames[-1])
        times.append(times[-1])
    # This installed processor computes timestamps as indices/fps. A millisecond
    # timebase preserves true PTS for VFR too; these are explicitly not source indices.
    metadata = {"fps": 1000.0, "duration": config["video"]["durationSec"],
                "total_num_frames": math.ceil(config["video"]["durationSec"] * 1000),
                "frames_indices": [round(t * 1000) for t in times]}
    quality = {"targetVisibleFraction": visible / original_count, "identityAmbiguous": ambiguous,
               "timeUnreliable": time_unreliable, "contextTruncated": False,
               "medianTargetHeightPx": float(np.median(sizes)) if sizes else 0,
               "uniqueFrameCount": original_count,
               "observationContextMissing": (selected[0]["mediaTimeSec"] - start > max(.3, 2 / request.poseFps)
                                             or end - selected[-1]["mediaTimeSec"] > max(.3, 2 / request.poseFps))}
    manifest = {"sampleId": sample_id, "window": {"startSec": start, "endSec": end},
                "sourceFrameIndices": [r["frameIndex"] for r in selected],
                "sourcePtsSeconds": [r["sourcePtsSec"] for r in selected],
                "sampleTimesSeconds": times, "encodedFrames": len(frames), "padding": len(frames) - original_count,
                "width": width, "height": height, "quality": quality, "observations": summaries,
                "videoMetadata": metadata, "timeMapping": "millisecond_timebase_v1", "poi": poi.model_dump()}
    write_json(run_dir / "samples" / f"{sample_id}.json", manifest)
    preview_path = run_dir / "previews" / f"{sample_id}.jpg"
    preview_path.parent.mkdir(exist_ok=True)
    contact_sheet(frames, times, preview_path)
    return np.stack(frames), metadata, manifest


def contact_sheet(frames, times, path):
    indices = np.linspace(0, len(frames) - 1, min(12, len(frames)), dtype=int)
    sheet = Image.new("RGB", (4 * 320, math.ceil(len(indices) / 4) * 204), "#202020")
    draw = ImageDraw.Draw(sheet)
    for n, index in enumerate(indices):
        im = Image.fromarray(frames[index])
        im.thumbnail((320, 180))
        x, y = n % 4 * 320, n // 4 * 204
        sheet.paste(im, (x, y))
        draw.text((x + 4, y + 184), f"{times[index]:.3f}s", fill="white")
    sheet.save(path)
