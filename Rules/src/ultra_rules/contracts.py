"""Shared pose observations used by video semantic analysis."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")


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
