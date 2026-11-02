from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from .models import default_model_path

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
Coordinate = Annotated[float, Field(ge=0, le=1)]
Point = tuple[Coordinate, Coordinate]
Trigger = Literal["line.cross", "zone.enter", "zone.leave", "initial_inside"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Poi(StrictModel):
    id: Identifier
    label: str = Field(default="", max_length=200)
    kind: Literal["line", "zone"]
    points: list[Point] = Field(min_length=2, max_length=100)
    anchor: Literal["bbox_center", "bbox_bottom_center"] = "bbox_center"

    @model_validator(mode="after")
    def geometry(self):
        if self.kind == "line":
            if len(self.points) != 2 or self.points[0] == self.points[1]:
                raise ValueError("A line needs two distinct endpoints")
        else:
            if self.points[0] == self.points[-1]:
                self.points = self.points[:-1]
            if len(self.points) < 3:
                raise ValueError("A zone needs at least three vertices")
            if any(a == b for a, b in zip(self.points, self.points[1:] + self.points[:1], strict=True)):
                raise ValueError("A zone cannot contain zero-length edges")
            area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(self.points, self.points[1:] + self.points[:1], strict=True))
            if abs(area) < 1e-8:
                raise ValueError("Zone area must be positive")
        return self


class EventDefinition(StrictModel):
    id: Identifier
    description: str = Field(min_length=1, max_length=1500)
    enabled: bool = True
    poiIds: list[Identifier] = Field(default_factory=list, max_length=50)
    triggerKinds: list[Trigger] = Field(default_factory=lambda: ["line.cross", "zone.enter", "zone.leave"], min_length=1)
    preSeconds: float = Field(default=4, ge=0, le=60)
    postSeconds: float = Field(default=6, ge=0, le=60)
    criteria: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=10)
    uncertainWhen: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.description.strip() or self.preSeconds + self.postSeconds <= 0:
            raise ValueError("Description and time window must not be empty")
        return self


class AnalysisParameters(StrictModel):
    modelPath: str = Field(default_factory=default_model_path, min_length=1, max_length=4096)
    poseDevice: Literal["cpu", "cuda"] = "cuda"
    poseFps: float = Field(default=10, ge=1, le=30)
    conf: float = Field(default=0.25, gt=0, lt=1)
    iou: float = Field(default=0.5, gt=0, lt=1)
    stableSeconds: float = Field(default=0.2, ge=0, le=2)
    hysteresisPx: float = Field(default=6, ge=0, le=50)
    maxGapSeconds: float = Field(default=1, ge=0.2, le=5)
    sampleFps: float = Field(default=2, ge=0.5, le=8)
    maxFrames: int = Field(default=32, ge=4, le=64, multiple_of=2)
    maxPixels: int = Field(default=196608, ge=65536, le=786432)
    maxNewTokens: int = Field(default=1024, ge=128, le=2048)
    dryRun: bool = False


class RunRequest(AnalysisParameters):
    videoId: Identifier
    pois: list[Poi] = Field(min_length=1, max_length=50)
    events: list[EventDefinition] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def references(self):
        ids = [p.id for p in self.pois]
        event_ids = [e.id for e in self.events]
        if len(set(ids)) != len(ids) or len(set(event_ids)) != len(event_ids):
            raise ValueError("POI and event IDs must be unique")
        if not any(e.enabled for e in self.events):
            raise ValueError("At least one event must be enabled")
        for event in self.events:
            if set(event.poiIds) - set(ids):
                raise ValueError(f"Unknown POI referenced by {event.id}")
        return self


class Evidence(StrictModel):
    timeSec: float = Field(ge=0)
    observation: str = Field(min_length=1, max_length=500)


class ConditionCheck(StrictModel):
    index: int = Field(ge=0, le=9)
    value: Literal["yes", "no", "unknown"]


class ModelAnswer(StrictModel):
    decision: Literal["match", "no_match", "uncertain"]
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str = Field(min_length=1, max_length=1500)
    # Optional for historical answers; new inference requires a visible-content description.
    videoDescription: str | None = Field(default=None, min_length=1, max_length=3000)
    # Optional legacy fields.
    evidence: list[Evidence] = Field(default_factory=list, max_length=8)
    conditions: list[ConditionCheck] = Field(default_factory=list, max_length=10)
    _parse_warnings: list[str] = PrivateAttr(default_factory=list)
