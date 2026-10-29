"""数据契约：观测、事件、运行元信息与配置。

所有时间字段一律使用视频时间（秒，float），字段命名与前端 JSON 一致（camelCase
用于对外，snake_case 用于内部）。为简化 Pydantic 序列化，模型统一采用
`alias_generator=to_camel` + `populate_by_name=True`。
"""
from __future__ import annotations

from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


class ZoneConfig(_Camel):
    """归一化多边形区域。points: [[x, y], ...]，x/y ∈ [0, 1]。"""

    id: str
    kind: Literal["approach", "check", "post", "exit", "restricted", "custom"] = "custom"
    points: list[list[float]] = Field(default_factory=list)
    label: str | None = None


class LineConfig(_Camel):
    """有向线段，用于定向穿越。方向 from a → b。"""

    id: str
    kind: Literal["entry", "exit", "restricted", "custom"] = "custom"
    a: list[float]
    b: list[float]
    label: str | None = None


class CameraProfile(_Camel):
    id: str
    label: str | None = None
    width: int | None = None
    height: int | None = None
    zones: list[ZoneConfig] = Field(default_factory=list)
    lines: list[LineConfig] = Field(default_factory=list)
    # 角色先验：某个 zone 长时间驻留即可推定为 guard（低证据）
    guard_zone_id: str | None = None
    guard_min_dwell_sec: float = 3.0


class RuleThresholds(_Camel):
    keypoint_conf: float = 0.5
    unknown_grace_sec: float = 0.5
    min_valid_samples: int = 5
    min_coverage: float = 0.8
    sample_fps: float = 10.0
    ema_tau_sec: float = 0.4


class DetectorConfig(_Camel):
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class RulesConfig(_Camel):
    version: str = "1"
    camera_profiles: list[CameraProfile] = Field(default_factory=list)
    default_profile_id: str | None = None
    thresholds: RuleThresholds = Field(default_factory=RuleThresholds)
    detectors: dict[str, DetectorConfig] = Field(default_factory=dict)
    model_id: str = "yolo11m-pose.pt"
    device: str = "cpu"
    conf: float = 0.25
    iou: float = 0.5

    def profile(self, profile_id: str | None) -> CameraProfile | None:
        target = profile_id or self.default_profile_id
        if not target:
            return self.camera_profiles[0] if self.camera_profiles else None
        for p in self.camera_profiles:
            if p.id == target:
                return p
        return None


# ---------------------------------------------------------------------------
# 观测
# ---------------------------------------------------------------------------


class Keypoint(_Camel):
    x: float
    y: float
    conf: float | None = None


class PersonObservation(_Camel):
    track_id: int | None = None
    bbox: list[float]  # [x1, y1, x2, y2]，像素坐标
    bbox_conf: float
    cls: str = "person"
    keypoints: list[Keypoint] = Field(default_factory=list)  # 17 点 COCO
    role: Literal["guard", "entrant", "unknown"] = "unknown"
    role_source: Literal["prior", "dwell", "manual", "none"] = "none"


class FrameObservation(_Camel):
    run_id: str
    video_id: str
    camera_profile_id: str | None
    frame_index: int
    media_time_sec: float
    width: int
    height: int
    time_source: Literal["pts", "frame_index", "container"] = "frame_index"
    persons: list[PersonObservation] = Field(default_factory=list)
    quality: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------


EvidenceLevel = Literal["visual", "fused", "workflow", "record"]
EventStatus = Literal["ended", "interrupted", "clipped_start", "clipped_end", "active"]


class Event(_Camel):
    event_id: str
    rule_id: str
    rule_version: str = "1"
    run_id: str
    video_id: str
    camera_profile_id: str | None = None
    subject_ids: list[str] = Field(default_factory=list)
    zone_id: str | None = None
    start_sec: float
    trigger_sec: float | None = None
    end_sec: float
    status: EventStatus = "ended"
    evidence_level: EvidenceLevel = "visual"
    reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)
    clip_flags: dict[str, bool] = Field(default_factory=dict)
    keyframes: list[dict[str, Any]] = Field(default_factory=list)  # [{tSec, path, phase}]
    review: Literal["unreviewed", "confirmed", "rejected"] = "unreviewed"


# ---------------------------------------------------------------------------
# 运行元信息
# ---------------------------------------------------------------------------


class VideoReport(_Camel):
    video_id: str
    source_path: str
    camera_profile_id: str | None
    width: int
    height: int
    fps_nominal: float
    frame_count: int
    duration_sec: float
    sampled_frames: int
    analyzable_sec: float
    time_source: Literal["pts", "frame_index", "container"]
    event_count: int
    rule_coverage: dict[str, float] = Field(default_factory=dict)
    unknown_sec: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    started_at: float
    finished_at: float
    status: Literal["ok", "partial", "failed"] = "ok"
    error: str | None = None


class RunSummary(_Camel):
    run_id: str
    started_at: float
    finished_at: float
    config_path: str | None
    config_hash: str
    model_id: str
    model_hash: str | None
    device: str
    videos: list[VideoReport] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    totals: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 帮助函数
# ---------------------------------------------------------------------------


def point_pairs(points: Iterable[Iterable[float]]) -> list[tuple[float, float]]:
    return [(float(p[0]), float(p[1])) for p in points]
