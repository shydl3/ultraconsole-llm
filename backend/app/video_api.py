"""Browser playback fallback for surveillance containers/codecs (video-only preview)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/video/previews", tags=["video"])
ROOT = Path(__file__).resolve().parents[2] / "tmp" / "video-previews"
CACHE_ROOT = Path(os.getenv(
    "ULTRA_VIDEO_PREVIEW_DIR", str(Path(__file__).resolve().parents[2] / "data" / "video-previews"),
)).expanduser().resolve()
WORKER = Path(__file__).with_name("video_transcode.py")
MAX_BYTES = int(os.getenv("ULTRA_VIDEO_MAX_UPLOAD_BYTES", str(2 * 1024**3)))
jobs: dict[str, dict] = {}
lock = threading.RLock()


def _publish_preview(job: dict) -> None:
    """Publish only a finished MP4, atomically even on a separate cache volume."""
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    staged = None
    metadata = None
    try:
        with tempfile.NamedTemporaryFile(dir=CACHE_ROOT, suffix=".tmp", delete=False) as output:
            staged = Path(output.name)
            with (job["folder"] / "preview.mp4").open("rb") as source:
                shutil.copyfileobj(source, output)
        with tempfile.NamedTemporaryFile(dir=CACHE_ROOT, suffix=".tmp", mode="w", encoding="utf-8", delete=False) as output:
            metadata = Path(output.name)
            json.dump({
                "originalFilename": job["filename"], "sourceSha256": job["sha256"],
                "sourceBytes": job["size"], "createdAt": time.time(),
                "previewFile": job["preview"].name, "encoder": "h264_nvenc", "version": 1,
            }, output, ensure_ascii=False, indent=2)
        os.replace(metadata, job["preview"].with_suffix(".json"))
        os.replace(staged, job["preview"])
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
        if metadata is not None:
            metadata.unlink(missing_ok=True)


def transcode(source: Path, target: Path, cancel: threading.Event, progress) -> None:
    """Use NVIDIA hardware for both codecs; never retry with software codecs."""
    if cancel.is_set():
        raise InterruptedError("Preview cancelled")
    try:
        # A codec/driver crash must fail this job, not the web server. Files
        # avoid pipe backpressure and let cancellation run while native code stalls.
        with tempfile.TemporaryFile(mode="w+") as updates, tempfile.TemporaryFile(mode="w+") as errors:
            process = subprocess.Popen(
                [sys.executable, str(WORKER), str(source), str(target)],
                stdout=updates, stderr=errors,
            )
            try:
                offset = 0
                while True:
                    if cancel.is_set():
                        raise InterruptedError("Preview cancelled")
                    # Read through a separate descriptor position: seeking the
                    # child's output descriptor would corrupt its next write.
                    data = os.pread(updates.fileno(), 65536, offset).decode()
                    complete = data.rfind("\n") + 1
                    for line in data[:complete].splitlines():
                        progress(int(line))
                    offset += complete
                    code = process.poll()
                    if code is not None:
                        if code:
                            length = os.fstat(errors.fileno()).st_size
                            detail = os.pread(errors.fileno(), min(length, 4000), max(0, length - 4000)).decode(errors="replace")
                            raise RuntimeError(f"GPU worker exited ({code}): {detail.strip()}")
                        if not target.is_file() or not target.stat().st_size:
                            raise RuntimeError("GPU worker produced no preview")
                        return
                    cancel.wait(.1)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    except InterruptedError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "GPU preview conversion failed (NVIDIA NVDEC + NVENC required; CPU fallback disabled). "
            f"Check GPU/driver availability and hardware support for this video: {exc}"
        ) from exc


def _run(identifier: str) -> None:
    job = jobs[identifier]
    folder = job["folder"]
    try:
        def progress(value):
            with lock:
                job["progress"] = value
        transcode(folder / "source", folder / "preview.mp4", job["cancel"], progress)
        if job["cancel"].is_set():
            raise InterruptedError("Preview cancelled")
        _publish_preview(job)
        state, error = "ready", None
    except InterruptedError:
        state, error = "cancelled", None
    except Exception as exc:
        state, error = "failed", str(exc)
    finally:
        with lock:
            # Successful previews live outside the disposable upload/job folder.
            shutil.rmtree(folder, ignore_errors=True)
            job.update(state=state, error=error, finished=time.time())
            if state == "ready":
                job["progress"] = 100
            if job["cancel"].is_set():
                jobs.pop(identifier, None)


def _job(identifier: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{32}", identifier) or identifier not in jobs:
        raise HTTPException(404, "Preview not found; select the video again")
    return jobs[identifier]


@router.post("", status_code=202)
def create_preview(file: UploadFile):
    with lock:
        # Expire session records and abandoned uploads, never completed cache files.
        for identifier, job in list(jobs.items()):
            if job.get("finished", float("inf")) < time.time() - 86400:
                shutil.rmtree(job["folder"], ignore_errors=True)
                jobs.pop(identifier)
        if any(job["state"] in {"uploading", "converting"} for job in jobs.values()):
            file.file.close()
            raise HTTPException(409, "Another video preview is being converted; retry when it finishes")
        identifier = uuid.uuid4().hex
        ROOT.mkdir(parents=True, exist_ok=True)
        active_folders = {job["folder"] for job in jobs.values()}
        for old in ROOT.glob("preview-*"):
            if old not in active_folders and old.stat().st_mtime < time.time() - 86400:
                shutil.rmtree(old, ignore_errors=True)
        folder = Path(tempfile.mkdtemp(prefix="preview-", dir=ROOT))
        jobs[identifier] = {"folder": folder, "state": "uploading", "progress": 0, "cancel": threading.Event()}
    try:
        size = 0
        digest = hashlib.sha256()
        with (folder / "source").open("wb") as output:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Video exceeds preview upload limit")
                output.write(chunk)
                digest.update(chunk)
        if not size:
            raise HTTPException(400, "Empty video")
        with lock:
            job = jobs[identifier]
            checksum = digest.hexdigest()
            preview = CACHE_ROOT / f"{checksum}.mp4"
            job.update(
                sha256=checksum, preview=preview, size=size,
                filename=re.split(r"[/\\]", file.filename or "video")[-1],
            )
            if preview.is_file() and preview.stat().st_size > 0:
                job.update(state="ready", progress=100, finished=time.time())
                shutil.rmtree(folder, ignore_errors=True)
                return {"id": identifier}
            job["state"] = "converting"
        threading.Thread(target=_run, args=(identifier,), daemon=True).start()
        return {"id": identifier}
    except Exception:
        with lock:
            jobs.pop(identifier, None)
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        file.file.close()


@router.get("/{identifier}")
def preview_status(identifier: str):
    with lock:
        job = _job(identifier)
        return {"state": job["state"], "progress": job["progress"], "error": job.get("error")}


@router.get("/{identifier}/source")
def preview_source(identifier: str):
    with lock:
        job = _job(identifier)
        if job["state"] != "ready":
            raise HTTPException(409, "Preview is not ready")
        if not job["preview"].is_file():
            raise HTTPException(404, "Cached preview was removed; select the video again")
        return FileResponse(job["preview"], media_type="video/mp4")


@router.delete("/{identifier}")
def delete_preview(identifier: str):
    # Release this browser session; retain successful conversions for future uploads.
    with lock:
        job = _job(identifier)
        job["cancel"].set()
        if job["state"] not in {"uploading", "converting"}:
            shutil.rmtree(job["folder"], ignore_errors=True)
            jobs.pop(identifier)
    return {"ok": True}
