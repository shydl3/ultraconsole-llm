"""Browser-friendly playback proxy for uploaded source videos.

Surveillance MP4s usually keep the `moov` index at the end of the file and use
multi-second GOPs, so a browser can seek only after fetching the tail index and
decoding up to a full GOP. Transcoding once with faststart + 1s GOP + no B-frames
(see ``video_transcode.py``) makes review seeking near-instant while the original
file stays untouched for analysis.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from . import vlm_api
from .vlm_jobs import write_json

router = APIRouter(prefix="/api/vlm/videos", tags=["playback"])
WORKER = Path(__file__).with_name("video_transcode.py")
# Bump when the encode profile changes so stale proxies are rebuilt on demand.
VERSION = 2
# Review copies trade a little quality for small size and instant seeking.
ENCODE_ARGS = ["--cq", "26", "--gop", "1", "--maxrate", "6000"]
# A 24h proxy at 6 Mbps is ~65 GB and can occupy NVENC for hours while analyses
# run, so long recordings keep using the original source unless this is raised
# (seconds, 0 = unlimited).
MAX_DURATION_SEC = float(os.environ.get("VLM_PLAYBACK_MAX_DURATION_SEC", "43200"))

_lock = threading.RLock()
# One NVENC job at a time keeps a long transcode from starving inference.
_gpu = threading.Semaphore(1)
_active: set[str] = set()


def _paths(video_id: str) -> tuple[Path, Path, Path]:
    folder = vlm_api.directory("uploads", video_id)
    return folder, folder / "playback.mp4", folder / "playback.json"


def _read(folder: Path) -> dict:
    try:
        data = json.loads((folder / "playback.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _status(video_id: str, folder: Path, target: Path) -> dict:
    data = _read(folder)
    state = str(data.get("state") or "missing")
    if state == "ready" and (not target.is_file() or data.get("version") != VERSION):
        state = "missing"
    if state == "running" and video_id not in _active:
        # A worker that outlived the previous server process cannot be tracked
        # any more; report it instead of pretending to be in progress forever.
        state, data = "failed", {**data, "error": data.get("error") or "Transcode interrupted"}
    return {
        "state": state,
        "progress": int(data.get("progress") or (100 if state == "ready" else 0)),
        "error": data.get("error"),
        "bytes": data.get("bytes"),
        "version": data.get("version"),
    }


def _run(video_id: str, source: Path, target: Path, status_path: Path) -> None:
    started = time.time()
    partial = target.with_name(f"{target.stem}.partial.mp4")
    write_json(status_path, {"videoId": video_id, "state": "running", "progress": 0,
                             "startedAt": started, "pid": os.getpid(), "version": VERSION})
    try:
        with _gpu, tempfile.TemporaryFile(mode="w+") as updates, tempfile.TemporaryFile(mode="w+") as errors:
            process = subprocess.Popen([sys.executable, str(WORKER), str(source), str(partial), *ENCODE_ARGS],
                                       stdout=updates, stderr=errors)
            write_json(status_path, {"videoId": video_id, "state": "running", "progress": 0,
                                     "startedAt": started, "pid": process.pid, "version": VERSION})
            offset = 0
            while True:
                # Read through a separate descriptor position: seeking the child's
                # output descriptor would corrupt its next write.
                data = os.pread(updates.fileno(), 65536, offset).decode(errors="replace")
                complete = data.rfind("\n") + 1
                for line in data[:complete].splitlines():
                    try:
                        progress = max(0, min(99, int(line.strip())))
                    except ValueError:
                        continue
                    write_json(status_path, {"videoId": video_id, "state": "running", "progress": progress,
                                             "startedAt": started, "pid": process.pid, "version": VERSION})
                offset += complete
                code = process.poll()
                if code is not None:
                    if code:
                        length = os.fstat(errors.fileno()).st_size
                        detail = os.pread(errors.fileno(), min(length, 4000), max(0, length - 4000)).decode(errors="replace")
                        raise RuntimeError(detail.strip() or f"Transcode worker exited ({code})")
                    break
                time.sleep(0.2)
        if not partial.is_file() or not partial.stat().st_size:
            raise RuntimeError("Transcode worker produced no playback file")
        os.replace(partial, target)
        write_json(status_path, {"videoId": video_id, "state": "ready", "progress": 100, "startedAt": started,
                                 "finishedAt": time.time(), "bytes": target.stat().st_size, "version": VERSION})
    except Exception as exc:
        partial.unlink(missing_ok=True)
        write_json(status_path, {"videoId": video_id, "state": "failed", "progress": 0, "startedAt": started,
                                 "finishedAt": time.time(), "error": str(exc), "version": VERSION})
    finally:
        with _lock:
            _active.discard(video_id)


@router.get("/{video_id}/playback")
def playback_status(video_id: str):
    folder, target, _status_path = _paths(video_id)
    return _status(video_id, folder, target)


@router.post("/{video_id}/playback", status_code=202)
def start_playback(video_id: str):
    folder, target, status_path = _paths(video_id)
    meta = vlm_api.read_json(folder / "video.json")
    duration = float(meta.get("durationSec") or 0)
    if MAX_DURATION_SEC > 0 and duration > MAX_DURATION_SEC:
        raise HTTPException(409, f"Playback proxy is limited to {MAX_DURATION_SEC / 3600:.1f}h videos; play the "
                                 "original source or raise VLM_PLAYBACK_MAX_DURATION_SEC (seconds, 0 = unlimited)")
    source = folder / str(meta.get("sourceName") or "")
    if not source.is_file():
        raise HTTPException(404, "Source video is missing")
    with _lock:
        current = _status(video_id, folder, target)
        if current["state"] in {"ready", "running"}:
            return current
        _active.add(video_id)
    threading.Thread(target=_run, args=(video_id, source, target, status_path), daemon=True).start()
    return {"state": "running", "progress": 0, "error": None, "bytes": None, "version": VERSION}


@router.get("/{video_id}/playback/source")
def playback_source(video_id: str):
    folder, target, _status_path = _paths(video_id)
    if not target.is_file() or _status(video_id, folder, target)["state"] != "ready":
        raise HTTPException(409, "Playback proxy is not ready")
    return FileResponse(target, media_type="video/mp4",
                        headers={"Cache-Control": "private, max-age=3600"})
