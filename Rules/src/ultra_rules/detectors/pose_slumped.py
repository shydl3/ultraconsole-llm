"""R02 持续低头/趴伏线索（pose.slumped）。

判据：躯干相对竖直方向倾角大 OR 鼻低于肩中点，加低活动量（躯干中心速度
小），持续 `enter_sec` 秒。COCO17 不能测闭眼；不做"睡觉"命名，只输出
"持续低头/趴伏样姿态线索"。
"""
from __future__ import annotations

from typing import Any

from ..contracts import CameraProfile, Event, FrameObservation, PersonObservation, RulesConfig
from ..features import body_center, nose_below_shoulder, torso_lean_deg
from ..geometry import distance
from ..temporal import EMASmoother, TemporalParams, ThreeValuedStateMachine


class SlumpedDetector:
    rule_id = "pose.slumped"
    rule_version = "1"

    def __init__(self, cfg: RulesConfig, profile: CameraProfile, params: dict[str, Any] | None = None) -> None:
        self.cfg = cfg
        self.profile = profile
        p = params or {}
        self.lean_deg: float = float(p.get("leanDeg", 35.0))
        self.speed_ratio: float = float(p.get("speedRatioL", 0.15))  # 中心速度 / L
        self.enter_sec: float = float(p.get("enterSec", 15.0))
        self.exit_sec: float = float(p.get("exitSec", 2.0))
        self.min_conf: float = float(cfg.thresholds.keypoint_conf)
        self._sm: dict[int, ThreeValuedStateMachine] = {}
        self._ema: dict[int, tuple[EMASmoother, EMASmoother, float | None]] = {}

    def reset(self) -> None:
        self._sm.clear()
        self._ema.clear()

    # ------------------------------------------------------------------
    def _evaluate(self, p: PersonObservation, t: float) -> tuple[bool | None, dict[str, float | None]]:
        lean = torso_lean_deg(p.keypoints, self.min_conf)
        nose_low = nose_below_shoulder(p.keypoints, self.min_conf)
        center = body_center(p.keypoints, self.min_conf, p.bbox)
        metrics: dict[str, float | None] = {"leanDeg": lean, "noseBelowShoulder": (None if nose_low is None else float(nose_low))}

        # 低活动量：躯干中心速度（EMA 平滑）
        speed_ratio: float | None = None
        if center is not None:
            prev = self._ema.get(p.track_id or -1)
            if prev is None:
                self._ema[p.track_id or -1] = (EMASmoother(0.4), EMASmoother(0.4), t)
                prev = self._ema[p.track_id or -1]
            ex, ey, last_t = prev
            vx = ex.update(t, center[0])
            vy = ey.update(t, center[1])
            dt = max(1e-3, t - (last_t or t))
            if vx is not None and vy is not None and last_t is not None:
                # 归一化到躯干尺度：使用 bbox 高度做代理
                L = max(20.0, p.bbox[3] - p.bbox[1])
                speed_ratio = distance((ex.value or 0, ey.value or 0), (vx, vy)) / dt / L
            self._ema[p.track_id or -1] = (ex, ey, t)
        metrics["speedRatioL"] = speed_ratio

        if lean is None and nose_low is None:
            return None, metrics
        pose_hit = (lean is not None and lean >= self.lean_deg) or (nose_low is True)
        low_motion = (speed_ratio is None) or (speed_ratio <= self.speed_ratio)
        return bool(pose_hit and low_motion), metrics

    # ------------------------------------------------------------------
    def update(self, frame: FrameObservation) -> list[Event]:
        out: list[Event] = []
        for p in frame.persons:
            if p.track_id is None:
                continue
            sm = self._sm.get(p.track_id)
            if sm is None:
                sm = ThreeValuedStateMachine(
                    TemporalParams(
                        enter_sec=self.enter_sec,
                        exit_sec=self.exit_sec,
                        min_valid_samples=int(self.cfg.thresholds.min_valid_samples),
                        min_coverage=self.cfg.thresholds.min_coverage,
                        max_gap_sec=1.5,
                        unknown_grace_sec=self.cfg.thresholds.unknown_grace_sec,
                    )
                )
                self._sm[p.track_id] = sm
            value, metrics = self._evaluate(p, frame.media_time_sec)
            seg = sm.update(frame.media_time_sec, value, {k: (v if v is not None else -1.0) for k, v in metrics.items()})
            if seg is None:
                continue
            out.append(
                Event(
                    event_id=f"{frame.video_id}-{self.rule_id}-{p.track_id}-{int(seg.start_sec * 1000)}",
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
                    reasons=[
                        f"lean>={self.lean_deg}deg OR nose_below_shoulder",
                        f"duration={seg.duration_sec:.1f}s",
                    ],
                    metrics=dict(seg.metrics),
                    quality={"coverage": seg.coverage},
                    clip_flags={"endClipped": seg.status == "clipped_end"},
                )
            )
        return out

    def finalize(self, t_end: float, run_id: str, video_id: str, profile_id: str | None) -> list[Event]:
        out: list[Event] = []
        for tid, sm in self._sm.items():
            seg = sm.finalize(t_end)
            if seg is None:
                continue
            out.append(
                Event(
                    event_id=f"{video_id}-{self.rule_id}-{tid}-{int(seg.start_sec * 1000)}",
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
                    reasons=["video_ended_while_active"],
                    metrics=dict(seg.metrics),
                    quality={"coverage": seg.coverage},
                    clip_flags={"endClipped": True},
                )
            )
        return out
