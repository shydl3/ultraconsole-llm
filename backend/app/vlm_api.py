from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from . import event_rules, vlm_jobs
from .vlm_jobs import TERMINAL, AnalysisParameters, RunRequest, iter_jsonl, prepare, read_json, write_json

ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("VLM_DATA_ROOT", str(ROOT / "Rules/data/vlm"))).resolve()
MAX_BYTES = int(os.environ.get("VLM_MAX_UPLOAD_BYTES", str(8 * 1024**3)))
MAX_DURATION_SEC = float(os.environ.get("VLM_MAX_DURATION_SEC", "7200"))
CHUNK_SIZE = 16 * 1024 * 1024
ALLOWED_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
router = APIRouter(prefix="/api/vlm", tags=["vlm"])
# In-flight chunked uploads: uploadId -> {dest, suffix, filename, received, size}
_pending_uploads: dict[str, dict] = {}


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


def _finalize_upload(dest: Path, source: Path, video_id: str, filename: str, size: int, sha: str):
    """Probe, validate limits and persist video.json for a fully received file."""
    try:
        from ultra_rules.vlm.media import probe

        meta = probe(source)
    except Exception as exc:
        raise HTTPException(400, f"Cannot decode video: {exc}") from exc
    if meta["durationSec"] > MAX_DURATION_SEC:
        raise HTTPException(400, f"Video exceeds {MAX_DURATION_SEC:.0f}s analysis limit")
    if meta["width"] * meta["height"] > 33_177_600:
        raise HTTPException(400, "Video exceeds 8K analysis limit")
    meta.update(videoId=video_id, fileName=Path(filename or "video").name,
                sha256=sha, sizeBytes=size, sourceName=source.name)
    write_json(dest / "video.json", meta)
    return meta


@router.post("/videos")
def upload(file: UploadFile):
    """Single-request upload kept for small files; large files use the chunked API."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
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
        return _finalize_upload(dest, source, video_id, file.filename or "video", size, h.hexdigest())
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        file.file.close()


@router.post("/videos/init")
async def upload_init(request: Request):
    """Start a chunked upload; returns uploadId and the expected chunk size."""
    body = await request.json()
    filename = str(body.get("filename") or "video")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, "Unsupported video extension")
    total = int(body.get("size") or 0)
    if total <= 0:
        raise HTTPException(400, "Size must be positive")
    if total > MAX_BYTES:
        raise HTTPException(413, "Video exceeds configured upload limit")
    upload_id = uuid.uuid4().hex
    dest = DATA_ROOT / "uploads" / upload_id
    (dest / "parts").mkdir(parents=True)
    _pending_uploads[upload_id] = {"dest": dest, "suffix": suffix, "filename": filename,
                                   "received": set(), "size": 0, "created": time.time()}
    return {"uploadId": upload_id, "chunkSize": CHUNK_SIZE}


@router.put("/videos/{upload_id}/parts/{index}")
async def upload_part(upload_id: str, index: int, request: Request):
    """Receive one byte-chunk; chunks may arrive in any order."""
    state = _pending_uploads.get(upload_id)
    if state is None:
        raise HTTPException(404, "Unknown or expired upload")
    if index < 0:
        raise HTTPException(400, "Invalid part index")
    part = state["dest"] / "parts" / f"{index:06d}.bin"
    data = await request.body()
    if not data:
        raise HTTPException(400, "Empty part")
    state["size"] += len(data) - (part.stat().st_size if part.exists() else 0)
    if state["size"] > MAX_BYTES:
        shutil.rmtree(state["dest"], ignore_errors=True)
        _pending_uploads.pop(upload_id, None)
        raise HTTPException(413, "Video exceeds configured upload limit")
    part.write_bytes(data)
    state["received"].add(index)
    return {"received": index, "totalBytes": state["size"]}


@router.post("/videos/{upload_id}/complete")
def upload_complete(upload_id: str):
    """Assemble parts, verify continuity, probe and register the video."""
    state = _pending_uploads.pop(upload_id, None)
    if state is None:
        raise HTTPException(404, "Unknown or expired upload")
    dest, parts_dir = state["dest"], state["dest"] / "parts"
    try:
        received = sorted(state["received"])
        if not received or received != list(range(len(received))):
            raise HTTPException(400, f"Missing parts; received {len(received)} non-contiguous")
        source = dest / f"source{state['suffix']}"
        size, h = 0, hashlib.sha256()
        with source.open("wb") as output:
            for index in received:
                chunk = (parts_dir / f"{index:06d}.bin").read_bytes()
                size += len(chunk)
                h.update(chunk)
                output.write(chunk)
        return _finalize_upload(dest, source, upload_id, state["filename"], size, h.hexdigest())
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(parts_dir, ignore_errors=True)


@router.delete("/videos/{upload_id}")
def upload_abort(upload_id: str):
    """Cancel an in-flight chunked upload and remove its parts."""
    state = _pending_uploads.pop(upload_id, None)
    if state is not None:
        shutil.rmtree(state["dest"], ignore_errors=True)
    return {"ok": True}


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
