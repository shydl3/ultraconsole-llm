"""顺序视频解码 + 采样 + 时间戳校验。

策略：
- 使用 OpenCV VideoCapture，按视频时间（`CAP_PROP_POS_MSEC`）采样；PTS 不可
  信时退化到 `frame_index / fps`。
- 顺序读取，不做随机 seek，避免关键帧定位偏差。
- 校验单调性、时长一致性、异常间隔，输出 `time_source` 与 warnings。
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Iterator

import cv2
import numpy as np


@dataclass
class VideoMeta:
    path: str
    video_id: str
    width: int
    height: int
    fps_nominal: float
    frame_count: int
    duration_sec: float
    sha1_head: str
    file_size: int


@dataclass
class DecodedFrame:
    frame_index: int
    media_time_sec: float
    image_bgr: np.ndarray
    time_source: str  # 'pts' | 'frame_index'


@dataclass
class ReadReport:
    time_source: str = "frame_index"
    warnings: list[str] = field(default_factory=list)
    sampled_frames: int = 0
    max_gap_sec: float = 0.0
    non_monotonic: int = 0


def _sha1_head(path: str, chunk_bytes: int = 1 << 20) -> str:
    """文件前 chunk_bytes + 文件大小，快速稳定标识。"""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read(chunk_bytes))
    h.update(str(os.path.getsize(path)).encode())
    return h.hexdigest()[:16]


def probe(path: str) -> VideoMeta:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {path}")
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if not (fps > 0):
            fps = 25.0
        duration = frames / fps if fps > 0 and frames > 0 else 0.0
        return VideoMeta(
            path=os.path.abspath(path),
            video_id=_sha1_head(path),
            width=width,
            height=height,
            fps_nominal=fps,
            frame_count=frames,
            duration_sec=duration,
            sha1_head=_sha1_head(path),
            file_size=os.path.getsize(path),
        )
    finally:
        cap.release()


def iter_frames(
    path: str,
    *,
    sample_fps: float | None = None,
    meta: VideoMeta | None = None,
    report: ReadReport | None = None,
) -> Iterator[DecodedFrame]:
    """按 sample_fps 顺序解码。sample_fps=None 表示逐帧输出。

    时间来源：优先使用 `CAP_PROP_POS_MSEC`（读帧后取值），异常时退化到
    frame_index / fps_nominal。
    """
    meta = meta or probe(path)
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {path}")
    try:
        fps = meta.fps_nominal or 25.0
        min_interval = (1.0 / sample_fps) if (sample_fps and sample_fps > 0) else 0.0
        frame_index = 0
        last_emitted_t = -1.0
        prev_t: float | None = None
        pts_bad = 0
        while True:
            ok, img = cap.read()
            if not ok or img is None:
                break
            pts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            t_pts = float(pts_ms) / 1000.0 if pts_ms and pts_ms > 0 else None
            t_idx = frame_index / fps if fps > 0 else 0.0
            if t_pts is None or not np.isfinite(t_pts) or t_pts <= 0:
                t = t_idx
                source = "frame_index"
                pts_bad += 1
            else:
                t = t_pts
                source = "pts"

            if prev_t is not None:
                if t < prev_t - 1e-3:
                    if report is not None:
                        report.non_monotonic += 1
                    t = prev_t  # 强制单调
                if report is not None:
                    gap = t - prev_t
                    if gap > report.max_gap_sec:
                        report.max_gap_sec = gap
            prev_t = t

            if min_interval > 0 and last_emitted_t >= 0 and (t - last_emitted_t) < min_interval - 1e-6:
                frame_index += 1
                continue

            last_emitted_t = t
            if report is not None:
                report.sampled_frames += 1
                report.time_source = source if report.sampled_frames == 1 else report.time_source

            yield DecodedFrame(frame_index=frame_index, media_time_sec=t, image_bgr=img, time_source=source)
            frame_index += 1

        if report is not None:
            if pts_bad > 0:
                report.warnings.append(f"pts_unavailable_frames={pts_bad}")
            if report.non_monotonic > 0:
                report.warnings.append(f"non_monotonic_pts={report.non_monotonic}")
            if report.max_gap_sec > 1.0:
                report.warnings.append(f"max_gap_sec={report.max_gap_sec:.2f}")
    finally:
        cap.release()


def decode_at(path: str, t_sec: float) -> np.ndarray | None:
    """按时间戳定位并解码一帧，用于证据截图。允许使用 seek（只做展示）。"""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return None
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t_sec) * 1000.0))
        ok, img = cap.read()
        if not ok or img is None:
            # 退化到帧号
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(max(0.0, t_sec) * fps))
            ok, img = cap.read()
        return img if ok else None
    finally:
        cap.release()
