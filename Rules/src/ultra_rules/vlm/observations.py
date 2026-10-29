from __future__ import annotations

import gzip
import json
import os
import sqlite3
import time
from pathlib import Path

from .contracts import RunRequest
from .media import sampled
from .poi import PoiTracker
from .storage import VERSION, append_json, digest, file_hash, progress, read_json, write_json


def track(run_dir: Path):
    # Imported only inside the YOLO process, never by the Qwen worker/API schema.
    import torch
    import ultralytics
    import yaml

    from ..pose_adapter import PoseAdapter

    config = read_json(run_dir / "request.json")
    request = RunRequest.model_validate(config["request"])
    source, meta = Path(config["sourcePath"]), config["video"]
    model = Path(config["poseModelPath"])
    tracker_config = yaml.safe_load((Path(ultralytics.__file__).parent / "cfg/trackers/bytetrack.yaml").read_text())
    tracker_config["track_buffer"] = max(1, round(request.poseFps * request.maxGapSeconds))
    tracker_path = run_dir / "tracker.yaml"
    tracker_path.write_text(yaml.safe_dump(tracker_config))
    fingerprint = {"version": VERSION, "source": meta["sha256"], "model": file_hash(model),
                   "poseFps": request.poseFps, "conf": request.conf, "iou": request.iou,
                   "tracker": tracker_config, "ultralytics": ultralytics.__version__,
                   "device": request.poseDevice, "timePolicy": "pyav-origin-scheduled-v1"}
    key = digest(fingerprint)
    cache = Path(config["cacheRoot"]) / f"{key}.jsonl.gz"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache_hit = cache.exists()
    progress(run_dir, stage="tracking", sampledFrames=0, candidateCount=0, cacheHit=cache_hit)
    tracker = PoiTracker(request)
    db = sqlite3.connect(run_dir / "observations.sqlite")
    db.execute("CREATE TABLE observations (frame_index INTEGER PRIMARY KEY, time REAL NOT NULL, body TEXT NOT NULL)")
    db.execute("CREATE INDEX observation_time ON observations(time)")
    adapter = None

    def generate():
        nonlocal adapter
        torch.set_num_threads(4)
        adapter = PoseAdapter(str(model), request.poseDevice, request.conf, request.iou, str(tracker_path))
        adapter.load()
        if adapter._model.task != "pose":
            raise ValueError("Semantic analysis requires a pose model")
        for index, t, source_type, frame in sampled(source, meta, request.poseFps):
            persons = adapter.infer(frame.to_ndarray(format="bgr24"))
            yield {"frameIndex": index, "mediaTimeSec": t, "sourcePtsSec": float(frame.time) if frame.time is not None else None,
                   "timeSource": source_type, "width": frame.width, "height": frame.height,
                   "persons": [p.model_dump(by_alias=True) for p in persons]}

    tmp = cache.with_suffix(f".{run_dir.name}.tmp")
    reader = gzip.open(cache, "rt", encoding="utf-8") if cache_hit else None
    writer = gzip.open(tmp, "wt", encoding="utf-8") if not cache_hit else None
    count, candidates, last_t = 0, 0, 0.0
    started = time.perf_counter()
    try:
        rows = (json.loads(line) for line in reader) if reader else generate()
        for row in rows:
            if writer:
                writer.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            for candidate in tracker.update(row):
                applicable = [e.id for e in request.events if e.enabled and candidate["triggerKind"] in e.triggerKinds
                              and (not e.poiIds or candidate["poiId"] in e.poiIds)]
                if applicable:
                    candidate["eventDefinitionIds"] = applicable
                    append_json(run_dir / "candidates.jsonl", candidate)
                    candidates += 1
            db.execute("INSERT INTO observations VALUES (?, ?, ?)",
                       (row["frameIndex"], row["mediaTimeSec"], json.dumps(row, ensure_ascii=False)))
            count += 1
            last_t = row["mediaTimeSec"]
            if count % 25 == 0:
                db.commit()
                progress(run_dir, sampledFrames=count, candidateCount=candidates, mediaTimeSec=last_t,
                         trackingFraction=min(1, last_t / meta["durationSec"]))
        db.commit()
        if not count:
            raise ValueError("No observations decoded")
        if writer:
            writer.close()
            os.replace(tmp, cache)
        write_json(run_dir / "observations.ref.json", {"path": str(cache), "fingerprint": fingerprint, "cacheHit": cache_hit})
        progress(run_dir, sampledFrames=count, candidateCount=candidates, mediaTimeSec=last_t,
                 trackingFraction=1, trackingSeconds=time.perf_counter() - started,
                 actualPoseFps=(count - 1) / last_t if last_t else 0)
    finally:
        db.close()
        if reader:
            reader.close()
        if writer:
            writer.close()
        if tmp.exists():
            tmp.unlink()
        if adapter:
            adapter.close()
