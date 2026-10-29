"""Management of persisted analysis runs and reusable converted videos."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from . import rules_api, video_api, vlm_api, vlm_jobs

router = APIRouter(prefix="/api/library", tags=["library"])
Kind = Literal["vlm", "rules", "video"]


class Rename(BaseModel):
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Name must not be empty or contain control characters")
        return value


def _read(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _root(kind: Kind):
    return {"vlm": vlm_api.DATA_ROOT / "runs", "rules": rules_api.RUNS_ROOT, "video": video_api.CACHE_ROOT}[kind]


def _path(kind: Kind, identifier: str):
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,159}", identifier):
        raise HTTPException(400, "Invalid identifier")
    root = _root(kind).resolve()
    path = root / (f"{identifier}.mp4" if kind == "video" else identifier)
    if path.is_symlink() or path.resolve().parent != root:
        raise HTTPException(400, "Invalid path")
    if not (path.is_file() if kind == "video" else path.is_dir()):
        raise HTTPException(404, "Item not found")
    return path


def _metadata(kind: Kind, path: Path):
    return path.with_suffix(".json") if kind == "video" else path / "library.json"


def _timestamp(value, fallback: float):
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return fallback


def _entry(kind: Kind, path: Path):
    meta = _read(_metadata(kind, path))
    identifier = path.stem if kind == "video" else path.name
    original = identifier
    state, count, size, active = "ready", None, None, False
    date = path.stat().st_mtime
    if kind == "video":
        original = meta.get("originalFilename") or path.name
        size = path.stat().st_size
        date = _timestamp(meta.get("createdAt", meta.get("importedAt")), date)
    elif kind == "vlm":
        status = vlm_jobs.status(path)
        original = status.get("fileName") or identifier
        state = status.get("state", "unknown")
        active = state not in vlm_jobs.TERMINAL
        count = status.get("summary", {}).get("resultCount")
        date = _timestamp(status.get("startedAt"), date)
    else:
        report = _read(path / "run.json")
        state = "completed" if report.get("finishedAt") else "running"
        active = state == "running"
        count = report.get("totals", {}).get("events", len(report.get("events", [])))
        date = _timestamp(report.get("startedAt"), date)
    return {"kind": kind, "id": identifier, "name": meta.get("displayName") or original,
            "originalName": original, "state": state, "createdAt": date,
            "sizeBytes": size, "resultCount": count, "active": active}


@router.get("")
def list_items(kind: Literal["all", "vlm", "rules", "video"] = "all", q: str = Query(default="", max_length=200),
               offset: int = Query(default=0, ge=0), limit: int = Query(default=30, ge=1, le=100)):
    items, counts = [], {"vlm": 0, "rules": 0, "video": 0}
    for category in ("vlm", "rules", "video"):
        root = _root(category)
        if not root.exists():
            continue
        for candidate in root.iterdir():
            if category == "video" and candidate.suffix != ".mp4":
                continue
            if category != "video" and not (candidate / ("status.json" if category == "vlm" else "run.json")).is_file():
                continue
            try:
                path = _path(category, candidate.stem if category == "video" else candidate.name)
                item = _entry(category, path)
            except (HTTPException, OSError, ValueError, KeyError):
                continue
            counts[category] += 1
            if kind not in ("all", category):
                continue
            if q.casefold() not in f"{item['name']} {item['originalName']} {item['id']}".casefold():
                continue
            items.append(item)
    items.sort(key=lambda item: (item["createdAt"], item["id"]), reverse=True)
    return {"items": items[offset:offset + limit], "total": len(items), "counts": counts,
            "offset": offset, "limit": limit, "videoDirectory": str(video_api.CACHE_ROOT)}


@router.get("/{kind}/{identifier}")
def detail(kind: Kind, identifier: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100)):
    path = _path(kind, identifier)
    item = _entry(kind, path)
    if kind == "video":
        return {"item": item, "metadata": _read(_metadata(kind, path)), "results": [], "total": 0,
                "videos": [{"id": identifier, "name": item["name"], "url": f"/api/library/video/{identifier}/source"}]}
    if kind == "vlm":
        status = vlm_jobs.status(path)
        page = vlm_api.results(identifier, offset=offset, limit=limit)
        video_id = status.get("videoId")
        videos = [{"id": video_id, "name": item["originalName"], "url": f"/api/vlm/videos/{video_id}/source"}] if video_id else []
        return {"item": item, "metadata": status, "results": page["items"], "total": page["total"], "videos": videos,
                "ruleSnapshot": vlm_api.run_rules(identifier)}
    report = _read(path / "run.json")
    events = rules_api.get_events(identifier, videoId=None, ruleId=None, review=None)
    videos = [{"id": video["videoId"], "name": Path(video.get("sourcePath", video["videoId"])).name,
               "url": f"/api/rules/runs/{identifier}/videos/{video['videoId']}/source"} for video in report.get("videos", [])]
    return {"item": item, "metadata": report, "results": events[offset:offset + limit], "total": len(events), "videos": videos}


@router.get("/video/{identifier}/source")
def saved_video(identifier: str, download: bool = False):
    path = _path("video", identifier)
    name = _entry("video", path)["name"]
    name = re.sub(r'[/\\\x00-\x1f]', "_", name)
    if not name.lower().endswith(".mp4"):
        name += ".mp4"
    return FileResponse(path, media_type="video/mp4", filename=name if download else None)


@router.patch("/{kind}/{identifier}")
def rename(kind: Kind, identifier: str, request: Rename):
    # Labels are stored separately from worker status and content-addressed filenames.
    with vlm_jobs.launch_lock, rules_api._jobs_lock, video_api.lock:
        path = _path(kind, identifier)
        metadata = _metadata(kind, path)
        if metadata.is_symlink():
            raise HTTPException(400, "Invalid metadata path")
        data = _read(metadata)
        data["displayName"] = request.name
        vlm_jobs.write_json(metadata, data)
        return _entry(kind, path)


@router.delete("/{kind}/{identifier}")
def delete(kind: Kind, identifier: str):
    with vlm_jobs.launch_lock, rules_api._jobs_lock, video_api.lock:
        path = _path(kind, identifier)
        if _entry(kind, path)["active"]:
            raise HTTPException(409, "Running analyses cannot be deleted; stop the task first")
        if kind == "video":
            # Pre-cache conversions may have been preserved with hard links.
            # Remove only completed legacy aliases of this exact saved file.
            for alias in video_api.ROOT.glob("preview-*/preview.mp4"):
                if not alias.is_symlink() and not (alias.parent / "source").exists() and alias.samefile(path):
                    alias.unlink()
            path.unlink()
            _metadata(kind, path).unlink(missing_ok=True)
            for job in video_api.jobs.values():
                if job.get("preview") == path:
                    job.update(state="failed", error="Saved preview was deleted from the library")
        else:
            # Run-local evidence is removed; shared uploaded sources and tracking caches remain.
            shutil.rmtree(path)
            if kind == "rules":
                (path.parent / f"{identifier}.log").unlink(missing_ok=True)
        return {"ok": True}
