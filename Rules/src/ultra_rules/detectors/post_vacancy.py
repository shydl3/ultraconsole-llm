"""R01 岗位区域持续空缺。

以 zone（岗位区域）为主体，不依赖单个 track_id：区域内连续 `enter_sec` 秒
没有可信人员占用即触发。若配置了 `guard_zone_id`，仅角色为 guard 的人员
算作占用；否则任何 person 都算占用。

无法可靠看到区域（画面异常/长时间无任何观测）时输出 unknown，不产出事件。
"""
from __future__ import annotations

from typing import Any

from ..contracts import CameraProfile, Event, FrameObservation, RulesConfig
from ..geometry import point_in_polygon, polygon_to_pixel, bbox_bottom_center, bbox_center
from ..temporal import TemporalParams, ThreeValuedStateMachine


class PostVacancyDetector:
    rule_id = "post.vacant"
    rule_version = "1"

    def __init__(self, cfg: RulesConfig, profile: CameraProfile, params: dict[str, Any] | None = None) -> None:
        self.cfg = cfg
        self.profile = profile
        p = params or {}
        self.zone_id: str = p.get("zoneId") or profile.guard_zone_id or ""
        self.require_guard_role: bool = bool(p.get("requireGuardRole", False))
        self.enter_sec: float = float(p.get("enterSec", 10.0))
        self.exit_sec: float = float(p.get("exitSec", 2.0))
        self.max_gap_sec: float = float(p.get("maxGapSec", 1.5))
        self.min_conf: float = float(cfg.thresholds.keypoint_conf)
        self._zone = next((z for z in profile.zones if z.id == self.zone_id), None)
        self._sm: ThreeValuedStateMachine | None = None
        self._occupied_sec = 0.0
        self._unknown_sec = 0.0

    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._zone is not None and len(self._zone.points) >= 3

    def reset(self) -> None:
        self._sm = ThreeValuedStateMachine(
            TemporalParams(
                enter_sec=self.enter_sec,
                exit_sec=self.exit_sec,
                min_valid_samples=3,
                min_coverage=0.5,
                max_gap_sec=self.max_gap_sec,
                unknown_grace_sec=self.cfg.thresholds.unknown_grace_sec,
            )
        )
        self._occupied_sec = 0.0
        self._unknown_sec = 0.0

    # ------------------------------------------------------------------
    def update(self, frame: FrameObservation) -> Event | None:
        if not self.enabled:
            return None
        if self._sm is None:
            self.reset()
        assert self._sm is not None and self._zone is not None
        poly = polygon_to_pixel([(p[0], p[1]) for p in self._zone.points], frame.width, frame.height)

        # 帧级质量：无任何 person 观测视为 unknown（可能画面异常/未解码到人）
        usable_persons = [p for p in frame.persons if p.bbox_conf >= 0.3]
        if not frame.persons:
            value: bool | None = None
            reason = "no_person_observation"
        else:
            inside = 0
            for p in usable_persons:
                if self.require_guard_role and p.role != "guard":
                    continue
                foot = bbox_bottom_center(p.bbox)
                ctr = bbox_center(p.bbox)
                if point_in_polygon(foot, poly) or point_in_polygon(ctr, poly):
                    inside += 1
            value = inside == 0
            reason = f"inside_count={inside}"

        seg = self._sm.update(frame.media_time_sec, value, {"reason": reason})
        if seg is None:
            return None
        return Event(
            event_id=f"{frame.video_id}-{self.rule_id}-{int(seg.start_sec * 1000)}",
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            run_id=frame.run_id,
            video_id=frame.video_id,
            camera_profile_id=frame.camera_profile_id,
            subject_ids=[self.zone_id],
            zone_id=self.zone_id,
            start_sec=seg.start_sec,
            trigger_sec=seg.trigger_sec,
            end_sec=seg.end_sec,
            status="clipped_end" if seg.status == "clipped_end" else ("interrupted" if seg.status == "interrupted" else "ended"),
            evidence_level="visual",
            reasons=[f"zone={self.zone_id}", f"empty_for={seg.duration_sec:.1f}s", reason],
            metrics=dict(seg.metrics),
            quality={"coverage": seg.coverage, "unknown_samples": seg.unknown_samples},
            clip_flags={"startClipped": False, "endClipped": seg.status == "clipped_end"},
        )

    def finalize(self, t_end: float, run_id: str, video_id: str, profile_id: str | None) -> Event | None:
        if self._sm is None:
            return None
        seg = self._sm.finalize(t_end)
        if seg is None:
            return None
        return Event(
            event_id=f"{video_id}-{self.rule_id}-{int(seg.start_sec * 1000)}",
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            run_id=run_id,
            video_id=video_id,
            camera_profile_id=profile_id,
            subject_ids=[self.zone_id],
            zone_id=self.zone_id,
            start_sec=seg.start_sec,
            trigger_sec=seg.trigger_sec,
            end_sec=seg.end_sec,
            status="clipped_end",
            evidence_level="visual",
            reasons=[f"zone={self.zone_id}", "video_ended_while_active"],
            metrics=dict(seg.metrics),
            quality={"coverage": seg.coverage},
            clip_flags={"startClipped": False, "endClipped": True},
        )
