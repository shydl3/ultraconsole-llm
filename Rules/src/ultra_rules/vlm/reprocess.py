"""Replay postprocessing from saved answers without loading models or videos."""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from copy import deepcopy
from pathlib import Path

from .results import POSTPROCESS_VERSION, aggregate_result, apply_answer, parse_answer
from .storage import TERMINAL, read_json, write_json


def reprocess_run(run_dir: Path):
    run_dir = run_dir.resolve()
    status = read_json(run_dir / "status.json")
    if status.get("state") not in TERMINAL:
        raise ValueError("Only terminal runs can be reprocessed")
    config = read_json(run_dir / "request.json")
    events = {e["id"]: e for e in config["request"]["events"]}
    path = run_dir / "results.jsonl"
    # Fail on malformed records instead of silently dropping historical data.
    results = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    changed = 0
    for result in results:
        if result.get("inferenceStatus") != "succeeded":
            continue
        previous = deepcopy(result)
        event = events[result["eventDefinitionId"]]
        for part in result["segments"]:
            raw_path = (run_dir / part["rawOutputPath"]).resolve()
            if not raw_path.is_relative_to(run_dir / "raw"):
                raise ValueError("Raw answer path must be inside the run's raw directory")
            window = part["window"]
            answer = parse_answer(raw_path.read_text(encoding="utf-8"), window["startSec"], window["endSec"],
                                  len(event.get("criteria") or [event["description"]]), check_timestamps=False)
            apply_answer(part, answer)
        aggregate_result(result)
        changed += result != previous
    if not changed:
        return {"changedResults": 0, "postprocessVersion": POSTPROCESS_VERSION}

    counts = dict.fromkeys(("match", "no_match", "uncertain"), 0)
    for result in results:
        if result.get("inferenceStatus") != "failed":
            counts[result["decision"]] += 1
    # Prepare and validate everything before changing any historical artifacts.
    payload = "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in results)
    summary_path = run_dir / "summary.json"
    summary = read_json(summary_path) if summary_path.exists() else dict(status.get("summary", {}))
    summary.update(resultCount=len(results), decisions=counts,
                   failedRequests=sum(r.get("inferenceStatus") == "failed" for r in results),
                   postprocessVersion=POSTPROCESS_VERSION)
    stamp = time.time()
    backup = run_dir / "postprocess-backups" / f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    backup.mkdir(parents=True)
    for name in ("results.jsonl", "summary.json", "status.json", "progress.json"):
        source = run_dir / name
        if source.exists():
            shutil.copy2(source, backup / name)
    write_json(backup / "reprocess.json", {"createdAt": stamp, "postprocessVersion": POSTPROCESS_VERSION,
                                          "changedResults": changed})
    temporary = path.with_name(f".results.{uuid.uuid4().hex}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)
    write_json(summary_path, summary)
    write_json(run_dir / "status.json", {**status, "summary": summary, "postprocessedAt": stamp})
    progress_path = run_dir / "progress.json"
    if progress_path.exists():
        write_json(progress_path, {**read_json(progress_path), "decisions": counts,
                                   "postprocessVersion": POSTPROCESS_VERSION, "postprocessedAt": stamp})
    return {"changedResults": changed, "decisions": counts, "backup": str(backup),
            "postprocessVersion": POSTPROCESS_VERSION}
