from __future__ import annotations

import argparse
import os
import shutil
import sys
import uuid
from pathlib import Path

from .contracts import RunRequest
from .models import validate_model_path
from .storage import VERSION, file_hash, read_json, write_json

ROOT = Path(__file__).resolve().parents[4]


def prepare(run_dir: Path, request: RunRequest, source: Path, video: dict, data_root: Path):
    # Validate before creating a run or starting expensive tracking.
    # Preview-only runs do not load a VLM and remain usable without weights.
    model_path = str(Path(request.modelPath).expanduser().resolve()) if request.dryRun else validate_model_path(request.modelPath)
    request = request.model_copy(update={"modelPath": model_path})
    run_dir.mkdir(parents=True, exist_ok=False)
    pose_python = os.environ.get("VLM_POSE_PYTHON", sys.executable)
    config = {"request": request.model_dump(), "sourcePath": str(source.resolve()), "video": video,
              "poseModelPath": str(ROOT / "backend/models/yolo11m-pose.pt"),
              "modelPath": model_path,
              "posePython": pose_python,
              "qwenPython": os.environ.get("VLM_PYTHON", "/home/user/miniconda3/envs/qwen3vl-bf16/bin/python"),
              "cacheRoot": str(data_root / "cache")}
    write_json(run_dir / "request.json", config)
    write_json(run_dir / "manifest.json", {"version": VERSION, "sourceSha256": video["sha256"],
                                           "configuration": config, "createdBy": "ultra_rules.vlm"})
    write_json(run_dir / "status.json", {"runId": run_dir.name, "videoId": request.videoId,
                                         "fileName": video["fileName"], "state": "queued"})
    return config


def main():
    parser = argparse.ArgumentParser(description="POI-triggered Qwen3-VL offline analysis")
    sub = parser.add_subparsers(dest="command", required=True)
    for phase in ("tracking", "understanding", "reprocess"):
        sub.add_parser(phase).add_argument("--run-dir", type=Path, required=True)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--input", type=Path, required=True)
    analyze.add_argument("--config", type=Path, required=True, help="RunRequest JSON (POIs and event definitions)")
    analyze.add_argument("--output", type=Path, default=ROOT / "Rules/data/vlm")
    analyze.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "tracking":
        from .observations import track
        track(args.run_dir)
    elif args.command == "understanding":
        from .qwen_worker import understand
        understand(args.run_dir)
    elif args.command == "reprocess":
        from .reprocess import reprocess_run
        print(reprocess_run(args.run_dir))
    else:
        from .media import probe
        from .runner import run
        source = args.input.resolve()
        meta = {**probe(source), "sha256": file_hash(source), "fileName": source.name}
        raw = read_json(args.config)
        raw.setdefault("videoId", meta["sha256"][:24])
        if args.dry_run:
            raw["dryRun"] = True
        request = RunRequest.model_validate(raw)
        upload_dir = args.output.resolve() / "uploads" / request.videoId
        upload_dir.mkdir(parents=True, exist_ok=True)
        local_source = upload_dir / f"source{source.suffix.lower()}"
        if (upload_dir / "video.json").exists():
            existing = read_json(upload_dir / "video.json")
            if existing["sha256"] != meta["sha256"]:
                raise ValueError("videoId already refers to a different source")
        elif source != local_source:
            shutil.copy2(source, local_source)
        meta.update(videoId=request.videoId, sourceName=local_source.name)
        write_json(upload_dir / "video.json", meta)
        source = local_source
        run_dir = args.output.resolve() / "runs" / f"vlm-{uuid.uuid4().hex}"
        prepare(run_dir, request, source, meta, args.output.resolve())
        print(f"run_dir={run_dir}", flush=True)
        run(run_dir)
        status = read_json(run_dir / "status.json")
        print(status, flush=True)
        if status["state"] != "completed":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
