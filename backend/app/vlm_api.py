from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from . import event_rules, vlm_jobs
from .vlm_jobs import TERMINAL, AnalysisParameters, RunRequest, iter_jsonl, prepare, read_json, write_json

ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("VLM_DATA_ROOT", str(ROOT / "Rules/data/vlm"))).resolve()
# Surveillance deployments analyze 12-24h recordings in one run. Both limits are
# byte/hour thresholds for the upload only; set 0 to disable a check entirely.
MAX_BYTES = int(os.environ.get("VLM_MAX_UPLOAD_BYTES", str(128 * 1024**3)))
MAX_DURATION_SEC = float(os.environ.get("VLM_MAX_DURATION_SEC", "172800"))
CHUNK_SIZE = 16 * 1024 * 1024
ALLOWED_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
router = APIRouter(prefix="/api/vlm", tags=["vlm"])
# In-flight chunked uploads: uploadId -> {dest, suffix, filename, received, size}
_pending_uploads: dict[str, dict] = {}

# One GPU worker at a time: submitted runs stay "queued" until the scheduler
# launches them in submission order. State lives on disk, so the queue survives
# a backend restart and the browser can re-attach by polling /runs?active=true.
MAX_CONCURRENT = 1
_scheduler_lock = threading.RLock()
_scheduler_stop = threading.Event()
_scheduler_thread: threading.Thread | None = None


def _occupies_worker(run_dir: Path, state: str) -> bool:
    # A terminal status written milliseconds before the worker process exits still
    # holds the worker lock, so process liveness wins over the status file.
    return (state not in TERMINAL and state != "queued") or vlm_jobs.owner_alive(run_dir)


def _started_at(run_dir: Path) -> float:
    try:
        return float(read_json(run_dir / "status.json").get("startedAt") or 0)
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0


def _pump():
    """Launch the oldest queued run while the worker slot is free."""
    root = DATA_ROOT / "runs"
    if not root.exists():
        return
    # Cheap scan first: the full status pass (which may rewrite stale records) only
    # runs while something is waiting and the worker slot looks free.
    queued, busy = [], 0
    for status_path in root.glob("*/status.json"):
        try:
            state = read_json(status_path).get("state", "unknown")
        except (OSError, ValueError, TypeError):
            continue
        if state == "queued":
            queued.append(status_path.parent)
        elif state not in TERMINAL:
            busy += 1
    if not queued or busy >= MAX_CONCURRENT:
        return
    with vlm_jobs.launch_lock, _scheduler_lock:
        pending = []
        for folder in queued:
            try:
                state = vlm_jobs.status(folder).get("state", "unknown")
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if _occupies_worker(folder, state):
                busy += 1
            elif state == "queued" and not (folder / "cancel.requested").exists():
                pending.append(folder)
        if busy >= MAX_CONCURRENT or not pending:
            return
        pending.sort(key=_started_at)
        for folder in pending:
            config, error = {}, None
            try:
                config = read_json(folder / "request.json")
            except Exception as exc:
                error = f"Cannot read run request: {exc}"
            if error is None:
                try:
                    vlm_jobs.launch(folder, config["posePython"])
                    return
                except Exception as exc:
                    error = f"Worker launch failed: {exc}"
            write_json(folder / "status.json", {"runId": folder.name,
                                                "videoId": config.get("request", {}).get("videoId"),
                                                "state": "failed", "error": error,
                                                "finishedAt": time.time(), "updatedAt": time.time()})


def _scheduler_loop():
    while not _scheduler_stop.is_set():
        try:
            _pump()
        except Exception:  # pragma: no cover - the queue must never kill the loop
            pass
        _scheduler_stop.wait(2.0)


def start_scheduler():
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        return
    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, name="vlm-queue", daemon=True)
    _scheduler_thread.start()


def stop_scheduler():
    _scheduler_stop.set()
    thread = _scheduler_thread
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=3)


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


def _too_large(size: int) -> bool:
    return MAX_BYTES > 0 and size > MAX_BYTES


def _finalize_upload(dest: Path, source: Path, video_id: str, filename: str, size: int, sha: str):
    """Probe, validate limits and persist video.json for a fully received file."""
    try:
        from ultra_rules.vlm.media import probe

        meta = probe(source)
    except Exception as exc:
        raise HTTPException(400, f"Cannot decode video: {exc}") from exc
    if MAX_DURATION_SEC > 0 and meta["durationSec"] > MAX_DURATION_SEC:
        hours = meta["durationSec"] / 3600
        raise HTTPException(400, f"Video is {hours:.1f}h long, over the {MAX_DURATION_SEC / 3600:.1f}h limit; "
                                 "raise VLM_MAX_DURATION_SEC (seconds, 0 = unlimited) to analyze it")
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
                if _too_large(size):
                    raise HTTPException(413, f"Video exceeds the {MAX_BYTES / 1024**3:.0f}GiB upload limit (VLM_MAX_UPLOAD_BYTES)")
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
    if _too_large(total):
        raise HTTPException(413, f"Video exceeds the {MAX_BYTES / 1024**3:.0f}GiB upload limit (VLM_MAX_UPLOAD_BYTES)")
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
    if _too_large(state["size"]):
        shutil.rmtree(state["dest"], ignore_errors=True)
        _pending_uploads.pop(upload_id, None)
        raise HTTPException(413, f"Video exceeds the {MAX_BYTES / 1024**3:.0f}GiB upload limit (VLM_MAX_UPLOAD_BYTES)")
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
    # Let the browser reuse already fetched ranges; uploads are immutable per videoId.
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        headers={"Cache-Control": "private, max-age=3600"})


@router.get("/runs")
def list_runs(limit: int = Query(default=30, ge=1, le=200), active: bool = False,
              state: str | None = Query(default=None, max_length=32)):
    root = DATA_ROOT / "runs"
    if not root.exists():
        return []
    paths = sorted(root.glob("*/status.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if active or state:
        # Filters run before the limit so a record list is not starved by newer,
        # unfinished runs; the browser restores active tasks the same way.
        results = [vlm_jobs.status(p.parent) for p in paths[:200]]
        if active:
            results = [result for result in results if result["state"] not in TERMINAL]
        if state:
            results = [result for result in results if result["state"] == state]
        return results if active and state is None else results[:limit]
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
        run_id = f"vlm-{uuid.uuid4().hex}"
        run_dir = DATA_ROOT / "runs" / run_id
        try:
            prepare(run_dir, request, video_dir / meta["sourceName"], meta, DATA_ROOT)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if snapshot is not None:
            write_json(run_dir / "rules-snapshot.json", snapshot)
        _pump()
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
    result = vlm_jobs.cancel(directory("runs", run_id))
    _pump()
    return result


@router.get("/runs/{run_id}/results")
def results(run_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200),
            trackId: int | None = None, eventId: str | None = None, decision: str | None = None,
            inferenceStatus: str | None = None, flagged: bool = False,
            q: str = "", sort: Literal["time", "track"] = "time",
            order: Literal["asc", "desc"] = "asc"):
    """One filtered page of judgments plus full-coverage facets and timeline markers.

    Facet counts skip their own dimension so a selected chip never zeroes the
    other chips; ``markers`` covers every matching row (not just this page) so
    the review window can draw a timeline and step through events across pages.
    """
    folder = directory("runs", run_id)
    if len(q) > 200:
        raise HTTPException(400, "Search query is too long")
    needle = q.casefold()

    def keeps(row: dict, skip: str | tuple[str, ...] = ()) -> bool:
        skips = (skip,) if isinstance(skip, str) else skip
        if "track" not in skips and trackId is not None and row.get("trackId") != trackId:
            return False
        if "event" not in skips and eventId and row.get("eventDefinitionId") != eventId:
            return False
        if "decision" not in skips and decision and row.get("decision") != decision:
            return False
        if "status" not in skips and inferenceStatus and row.get("inferenceStatus") != inferenceStatus:
            return False
        if "flag" not in skips and flagged and not (row.get("downgradeReasons") or row.get("qualityWarnings")):
            return False
        if needle:
            haystack = f"{row.get('description', '')} {row.get('reason', '')} {row.get('trackId', '')}"
            if needle not in haystack.casefold():
                return False
        return True

    facets: dict = {"decision": {}, "inferenceStatus": {}, "eventDefinitionId": {}, "trackId": {}}
    counts = {"decision": facets["decision"], "status": facets["inferenceStatus"],
              "event": facets["eventDefinitionId"], "track": facets["trackId"]}
    fields = {"decision": "decision", "status": "inferenceStatus", "event": "eventDefinitionId", "track": "trackId"}
    rows = []
    flaggedTotal = 0
    allTotal = 0
    for row in iter_jsonl(folder / "results.jsonl"):
        # "All" ignores the mutually exclusive verdict filters it is meant to clear.
        if keeps(row, ("decision", "status", "flag")):
            allTotal += 1
        if not keeps(row):
            continue
        rows.append(row)
        for dimension, field in fields.items():
            if keeps(row, dimension):
                value = row.get(field)
                if value is not None:
                    counts[dimension][value] = counts[dimension].get(value, 0) + 1
        if keeps(row, "flag") and (row.get("downgradeReasons") or row.get("qualityWarnings")):
            flaggedTotal += 1
    rows.sort(key=lambda row: (row.get("triggerSec") or 0, row.get("trackId") or 0) if sort == "time"
              else (row.get("trackId") or 0, row.get("triggerSec") or 0))
    if order == "desc":
        rows.reverse()
    markers = [{"resultId": row.get("resultId"), "triggerSec": row.get("triggerSec") or 0,
                "decision": row.get("decision"), "trackId": row.get("trackId"),
                "inferenceStatus": row.get("inferenceStatus")} for row in rows]
    facets["flagged"] = flaggedTotal
    facets["total"] = allTotal
    return {"items": rows[offset:offset + limit], "total": len(rows), "offset": offset, "limit": limit,
            "facets": facets, "markers": markers}


@router.get("/runs/{run_id}/artifacts/{path:path}")
def artifact(run_id: str, path: str):
    folder = directory("runs", run_id).resolve()
    target = (folder / path).resolve()
    if not target.is_relative_to(folder) or Path(path).parts[0] not in {"previews", "samples", "raw"}:
        raise HTTPException(400, "Invalid artifact path")
    if not target.is_file() or target.suffix not in {".jpg", ".json", ".txt"}:
        raise HTTPException(404, "Artifact not found")
    return FileResponse(target)
