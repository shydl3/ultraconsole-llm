"""运行产物写入：JSON、CSV、证据截图，以及原子落盘。"""
from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
from typing import Any, Iterable

import cv2
import numpy as np

from .contracts import Event, RunSummary, VideoReport


def _atomic_write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def write_run_json(run_dir: str, summary: RunSummary) -> str:
    path = os.path.join(run_dir, "run.json")
    _atomic_write_text(path, json.dumps(summary.model_dump(by_alias=True), ensure_ascii=False, indent=2))
    return path


def write_events_json(run_dir: str, events: Iterable[Event]) -> str:
    path = os.path.join(run_dir, "events.json")
    payload = [e.model_dump(by_alias=True) for e in events]
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


_CSV_FIELDS = [
    "eventId",
    "ruleId",
    "videoId",
    "cameraProfileId",
    "subjectIds",
    "zoneId",
    "startSec",
    "triggerSec",
    "endSec",
    "status",
    "evidenceLevel",
    "reasons",
    "review",
]


def write_events_csv(run_dir: str, events: Iterable[Event]) -> str:
    path = os.path.join(run_dir, "events.csv")
    os.makedirs(run_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=run_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
            w.writeheader()
            for e in events:
                d = e.model_dump(by_alias=True)
                row = {k: d.get(k) for k in _CSV_FIELDS}
                row["subjectIds"] = "|".join(d.get("subjectIds") or [])
                row["reasons"] = " | ".join(d.get("reasons") or [])
                w.writerow(row)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def write_video_report(run_dir: str, report: VideoReport) -> str:
    per_dir = os.path.join(run_dir, "per-video")
    os.makedirs(per_dir, exist_ok=True)
    path = os.path.join(per_dir, f"{report.video_id}.json")
    _atomic_write_text(path, json.dumps(report.model_dump(by_alias=True), ensure_ascii=False, indent=2))
    return path


def save_evidence_frame(
    run_dir: str,
    event: Event,
    image_bgr: np.ndarray,
    phase: str = "trigger",
    overlay: dict[str, Any] | None = None,
) -> str | None:
    """把证据帧写入 run_dir/frames/{eventId}_{phase}.jpg，返回相对路径。"""
    if image_bgr is None:
        return None
    frames_dir = os.path.join(run_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    safe_id = event.event_id.replace("/", "_").replace(":", "_")
    fname = f"{safe_id}_{phase}.jpg"
    fpath = os.path.join(frames_dir, fname)
    img = image_bgr
    if overlay:
        img = _draw_overlay(image_bgr, overlay)
    ok = cv2.imwrite(fpath, img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    return os.path.relpath(fpath, run_dir) if ok else None


def _draw_overlay(img: np.ndarray, overlay: dict[str, Any]) -> np.ndarray:
    out = img.copy()
    for box in overlay.get("bboxes", []) or []:
        x1, y1, x2, y2 = [int(v) for v in box["xyxy"]]
        color = (0, 220, 0) if box.get("role") == "guard" else (80, 200, 255)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        label = box.get("label") or ""
        if label:
            cv2.putText(out, label, (x1, max(20, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    for kp_set in overlay.get("keypoints", []) or []:
        for pt in kp_set.get("points", []) or []:
            if pt.get("conf") is None or pt["conf"] < 0.4:
                continue
            cv2.circle(out, (int(pt["x"]), int(pt["y"])), 3, (255, 120, 120), -1)
    for z in overlay.get("zones", []) or []:
        pts = np.array(z["points"], dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(out, [pts], isClosed=True, color=(255, 200, 0), thickness=2)
        if z.get("label"):
            x, y = pts[0][0]
            cv2.putText(out, str(z["label"]), (int(x), int(max(20, y - 6))), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 200, 0), 2)
    for ln in overlay.get("lines", []) or []:
        a = (int(ln["a"][0]), int(ln["a"][1]))
        b = (int(ln["b"][0]), int(ln["b"][1]))
        cv2.arrowedLine(out, a, b, (0, 120, 255), 2, tipLength=0.08)
    return out


def copy_source_reference(run_dir: str, source_path: str, video_id: str) -> None:
    """在 run 目录写入源视频的软链（失败则跳过），便于前端直接播放。"""
    link_dir = os.path.join(run_dir, "sources")
    os.makedirs(link_dir, exist_ok=True)
    ext = os.path.splitext(source_path)[1] or ".mp4"
    target = os.path.join(link_dir, f"{video_id}{ext}")
    if os.path.exists(target) or os.path.islink(target):
        return
    try:
        os.symlink(os.path.abspath(source_path), target)
    except OSError:
        try:
            shutil.copyfile(source_path, target)
        except OSError:
            pass
