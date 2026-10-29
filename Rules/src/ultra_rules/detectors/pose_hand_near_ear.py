"""R03 持续手靠耳（pose.hand_near_ear）。

判据：同侧腕-耳距离 / L 小于进入阈值，且肘部抬起/弯曲（肘角 < 140°），
持续 `enter_sec` 秒。左右侧独立评估，任一侧命中即触发。不命名"打电话"，
仅输出"手靠耳样姿态线索"。
"""
from __future__ import annotations

from typing import Any

from ..contracts import CameraProfile, Event, FrameObservation, PersonObservation, RulesConfig
from ..features import elbow_angle_deg, wrist_ear_ratio
from ..temporal import TemporalParams, ThreeValuedStateMachine


class HandNearEarDetector:
    rule_id = "pose.hand_near_ear"
    rule_version = "1"

    def __init__(self, cfg: RulesConfig, profile: CameraProfile, params: dict[str, Any] | None = None) -> None:
        self.cfg = cfg
        self.profile = profile
        p = params or {}
        self.enter_ratio: float = float(p.get("enterRatio", 0.35))
        self.exit_ratio: float = float(p.get("exitRatio", 0.50))
        self.elbow_max_deg: float = float(p.get("elbowMaxDeg", 140.0))
        self.enter_sec: float = float(p.get("enterSec", 3.0))
        self.exit_sec: float = float(p.get("exitSec", 1.0))
        self.min_conf: float = float(cfg.thresholds.keypoint_conf)
        self._sm: dict[tuple[int, str], ThreeValuedStateMachine] = {}

    def reset(self) -> None:
        self._sm.clear()

    # ------------------------------------------------------------------
    def _side_value(self, p: PersonObservation, side: str) -> tuple[bool | None, dict[str, float]]:
        r = wrist_ear_ratio(p.keypoints, self.min_conf, side)
        ang = elbow_angle_deg(p.keypoints, self.min_conf, side)
        metrics: dict[str, float] = {}
        if r is not None:
            metrics[f"wristEarRatio_{side}"] = r
        if ang is not None:
            metrics[f"elbowDeg_{side}"] = ang
        if r is None:
            return None, metrics
        # 滞回：进入用 enter_ratio，已在活跃时用 exit_ratio
        hit_dist = r <= self.exit_ratio
        elbow_ok = (ang is None) or (ang <= self.elbow_max_deg)
        return bool(hit_dist and elbow_ok), metrics

    # ------------------------------------------------------------------
    def update(self, frame: FrameObservation) -> list[Event]:
        out: list[Event] = []
        for p in frame.persons:
            if p.track_id is None:
                continue
            for side in ("left", "right"):
                key = (p.track_id, side)
                sm = self._sm.get(key)
                if sm is None:
                    sm = ThreeValuedStateMachine(
                        TemporalParams(
                            enter_sec=self.enter_sec,
                            exit_sec=self.exit_sec,
                            min_valid_samples=3,
                            min_coverage=0.6,
                            max_gap_sec=1.0,
                            unknown_grace_sec=self.cfg.thresholds.unknown_grace_sec,
                        )
                    )
                    self._sm[key] = sm
                value, metrics = self._side_value(p, side)
                seg = sm.update(frame.media_time_sec, value, metrics)
                if seg is None:
                    continue
                out.append(
                    Event(
                        event_id=f"{frame.video_id}-{self.rule_id}-{p.track_id}-{side}-{int(seg.start_sec * 1000)}",
                        rule_id=self.rule_id,
                        rule_version=self.rule_version,
                        run_id=frame.run_id,
                        video_id=frame.video_id,
                        camera_profile_id=frame.camera_profile_id,
                        subject_ids=[f"track:{p.track_id}"],
                        start_sec=seg.start_sec,
                        trigger_sec=seg.trigger_sec,
                        end_sec=seg.end_sec,
                        status="clipped_end" if seg.status == "clipped_end" else ("interrupted" if seg.status == "interrupted" else "ended"),
                        evidence_level="visual",
                        reasons=[f"side={side}", f"wrist_ear_ratio<={self.exit_ratio}", f"duration={seg.duration_sec:.1f}s"],
                        metrics=dict(seg.metrics),
                        quality={"coverage": seg.coverage},
                        clip_flags={"endClipped": seg.status == "clipped_end"},
                    )
                )
        return out

    def finalize(self, t_end: float, run_id: str, video_id: str, profile_id: str | None) -> list[Event]:
        out: list[Event] = []
        for (tid, side), sm in self._sm.items():
            seg = sm.finalize(t_end)
            if seg is None:
                continue
            out.append(
                Event(
                    event_id=f"{video_id}-{self.rule_id}-{tid}-{side}-{int(seg.start_sec * 1000)}",
                    rule_id=self.rule_id,
                    rule_version=self.rule_version,
                    run_id=run_id,
                    video_id=video_id,
                    camera_profile_id=profile_id,
                    subject_ids=[f"track:{tid}"],
                    start_sec=seg.start_sec,
                    trigger_sec=seg.trigger_sec,
                    end_sec=seg.end_sec,
                    status="clipped_end",
                    evidence_level="visual",
                    reasons=[f"side={side}", "video_ended_while_active"],
                    metrics=dict(seg.metrics),
                    quality={"coverage": seg.coverage},
                    clip_flags={"endClipped": True},
                )
            )
        return out
