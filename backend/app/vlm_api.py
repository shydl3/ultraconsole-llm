from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from . import event_rules, vlm_jobs
from .vlm_jobs import TERMINAL, AnalysisParameters, RunRequest, iter_jsonl, prepare, read_json, write_json

ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("VLM_DATA_ROOT", str(ROOT / "Rules/data/vlm"))).resolve()
MAX_BYTES = int(os.environ.get("VLM_MAX_UPLOAD_BYTES", str(1024 * 1024 * 1024)))
router = APIRouter(prefix="/api/vlm", tags=["vlm"])


def directory(kind: str, identifier: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier):
        raise HTTPException(400, "Invalid identifier")
    path = DATA_ROOT / kind / identifier
    if not path.is_dir() or not path.resolve().is_relative_to((DATA_ROOT / kind).resolve()):
        raise HTTPException(404, "Not found")
    return path


@router.get("/defaults")
def defaults():
    return read_json(ROOT / "Rules/configs/vlm_events.json")


@router.get("/parameters")
def parameters():
    from ultra_rules.vlm.models import available_models

    return {"defaults": AnalysisParameters().model_dump(),
            "fields": AnalysisParameters.model_json_schema()["properties"], "models": available_models()}


@router.get("/event-rules")
def get_event_rules():
    return event_rules.load(DATA_ROOT)


@router.post("/event-rules/batch")
def save_event_rules(request: event_rules.Batch):
    return event_rules.save(DATA_ROOT, request)


@router.post("/videos")
def upload(file: UploadFile):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}:
        raise HTTPException(400, "Unsupported video extension")
    video_id = uuid.uuid4().hex
    dest = DATA_ROOT / "uploads" / video_id
    dest.mkdir(parents=True)
    source = dest / f"source{suffix}"
    try:
        size, h = 0, hashlib.sha256()
        with source.open("wb") as output:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Video exceeds configured upload limit")
                h.update(chunk)
                output.write(chunk)
        try:
            from ultra_rules.vlm.media import probe

            meta = probe(source)
        except Exception as exc:
            raise HTTPException(400, f"Cannot decode video: {exc}") from exc
        if meta["durationSec"] > 7200 or meta["width"] * meta["height"] > 33_177_600:
            raise HTTPException(400, "Video exceeds 2 hour / 8K analysis limit")
        meta.update(videoId=video_id, fileName=Path(file.filename or "video").name, sha256=h.hexdigest(), sizeBytes=size,
                    sourceName=source.name)
        write_json(dest / "video.json", meta)
        return meta
    except Exception:
        shutil.rmtree(dest)
        raise
    finally:
        file.file.close()


@router.get("/videos/{video_id}/source")
def source_video(video_id: str):
    folder = directory("uploads", video_id)
    meta = read_json(folder / "video.json")
    path = folder / meta["sourceName"]
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")


@router.get("/runs")
def list_runs(limit: int = Query(default=30, ge=1, le=200)):
    root = DATA_ROOT / "runs"
    if not root.exists():
        return []
    paths = sorted(root.glob("*/status.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [vlm_jobs.status(p.parent) for p in paths[:limit]]


@router.post("/runs")
def start_run(request: RunRequest):
    return _start_run(request)


@router.post("/runs/from-rules")
def start_from_rules(request: event_rules.ManagedRun):
    run, snapshot = event_rules.resolve(DATA_ROOT, request)
    return _start_run(run, snapshot)


def _start_run(request: RunRequest, snapshot: dict | None = None):
    video_dir = directory("uploads", request.videoId)
    meta = read_json(video_dir / "video.json")
    with vlm_jobs.launch_lock:
        if any(item["state"] not in TERMINAL for item in list_runs(200)):
            raise HTTPException(409, "Another semantic analysis is running")
        run_id = f"vlm-{uuid.uuid4().hex}"
        run_dir = DATA_ROOT / "runs" / run_id
        try:
            cfg = prepare(run_dir, request, video_dir / meta["sourceName"], meta, DATA_ROOT)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if snapshot is not None:
            write_json(run_dir / "rules-snapshot.json", snapshot)
        try:
            vlm_jobs.launch(run_dir, cfg["posePython"])
        except Exception as exc:
            write_json(run_dir / "status.json", {"runId": run_id, "videoId": request.videoId,
                                                "state": "failed", "error": str(exc), "updatedAt": time.time()})
            raise HTTPException(500, f"Worker launch failed: {exc}") from exc
        return {"runId": run_id, "videoId": request.videoId}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    folder = directory("runs", run_id)
    result = vlm_jobs.status(folder)
    if (folder / "request.json").exists():
        config = read_json(folder / "request.json")
        request = {**config.get("request", {})}
        # The outer configuration is the path actually passed to the worker,
        # including historical runs made before model selection existed.
        if config.get("modelPath"):
            request["modelPath"] = config["modelPath"]
        result["parameters"] = AnalysisParameters.model_validate(
            {key: request[key] for key in AnalysisParameters.model_fields if key in request}).model_dump()
    return result


@router.get("/runs/{run_id}/rules")
def run_rules(run_id: str):
    return event_rules.snapshot(directory("runs", run_id))


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    return vlm_jobs.cancel(directory("runs", run_id))


@router.get("/runs/{run_id}/results")
def results(run_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200),
            trackId: int | None = None, eventId: str | None = None, decision: str | None = None):
    folder = directory("runs", run_id)
    items, total = [], 0
    for row in iter_jsonl(folder / "results.jsonl"):
        if trackId is not None and row["trackId"] != trackId:
            continue
        if eventId and row["eventDefinitionId"] != eventId:
            continue
        if decision and row["decision"] != decision:
            continue
        if offset <= total < offset + limit:
            items.append(row)
        total += 1
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get("/runs/{run_id}/artifacts/{path:path}")
def artifact(run_id: str, path: str):
    folder = directory("runs", run_id).resolve()
    target = (folder / path).resolve()
    if not target.is_relative_to(folder) or Path(path).parts[0] not in {"previews", "samples", "raw"}:
        raise HTTPException(400, "Invalid artifact path")
    if not target.is_file() or target.suffix not in {".jpg", ".json", ".txt"}:
        raise HTTPException(404, "Artifact not found")
    return FileResponse(target)
