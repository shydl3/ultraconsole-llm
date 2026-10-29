"""R04 定向受限越界。

对每条有向线段（entry/exit/restricted），基于足点（缺失退化为 bbox 底边
中心）判断轨迹是否从 a→b 的右侧穿越到左侧。有限线段 + 滞回：只有当上一次
稳定侧与当前稳定侧不同时才计数，避免在边界附近抖动重复触发。
"""
from __future__ import annotations

from typing import Any

from ..contracts import CameraProfile, Event, FrameObservation, LineConfig, RulesConfig
from ..features import foot_point
from ..geometry import Point, signed_side, to_pixel


class _SubjectState:
    __slots__ = ("last_side", "last_stable_side", "last_t", "last_point", "crossings")

    def __init__(self) -> None:
        self.last_side: float | None = None
        self.last_stable_side: int | None = None
        self.last_t: float | None = None
        self.last_point: Point | None = None
        self.crossings: int = 0


class AccessCrossingDetector:
    rule_id = "access.crossing"
    rule_version = "1"

    def __init__(self, cfg: RulesConfig, profile: CameraProfile, params: dict[str, Any] | None = None) -> None:
        self.cfg = cfg
        self.profile = profile
        p = params or {}
        self.line_kinds: set[str] = set(p.get("lineKinds", ["entry", "exit", "restricted"]))
        self.hysteresis_px: float = float(p.get("hysteresisPx", 6.0))
        self.min_conf: float = float(cfg.thresholds.keypoint_conf)
        self.max_gap_sec: float = float(p.get("maxGapSec", 1.5))
        self._state: dict[tuple[str, int], _SubjectState] = {}
        self._pending: list[Event] = []

    # ------------------------------------------------------------------
    @property
    def lines(self) -> list[LineConfig]:
        return [ln for ln in self.profile.lines if ln.kind in self.line_kinds]

    def reset(self) -> None:
        self._state.clear()

    # ------------------------------------------------------------------
    def update(self, frame: FrameObservation) -> list[Event]:
        out: list[Event] = []
        if not self.lines:
            return out
        for ln in self.lines:
            a = to_pixel((ln.a[0], ln.a[1]), frame.width, frame.height)
            b = to_pixel((ln.b[0], ln.b[1]), frame.width, frame.height)
            seg = (a, b)
            for p in frame.persons:
                if p.track_id is None:
                    continue
                key = (ln.id, p.track_id)
                st = self._state.setdefault(key, _SubjectState())
                pt = foot_point(p.keypoints, self.min_conf, p.bbox)
                side_raw = signed_side(pt, seg)
                # 稳定侧：距离超过滞回带才更新
                stable: int | None = None
                if side_raw > self.hysteresis_px:
                    stable = 1
                elif side_raw < -self.hysteresis_px:
                    stable = -1
                if st.last_t is not None and (frame.media_time_sec - st.last_t) > self.max_gap_sec:
                    # 长间隔 → 不判定穿越，只更新稳定侧
                    if stable is not None:
                        st.last_stable_side = stable
                    st.last_t = frame.media_time_sec
                    st.last_point = pt
                    continue
                if stable is not None:
                    if st.last_stable_side is not None and stable != st.last_stable_side:
                        # 从 -1 → +1 表示按 a→b 的正方向穿越
                        direction = "forward" if (st.last_stable_side == -1 and stable == 1) else "backward"
                        ev = Event(
                            event_id=f"{frame.video_id}-{self.rule_id}-{ln.id}-{p.track_id}-{int(frame.media_time_sec * 1000)}",
                            rule_id=self.rule_id,
                            rule_version=self.rule_version,
                            run_id=frame.run_id,
                            video_id=frame.video_id,
                            camera_profile_id=frame.camera_profile_id,
                            subject_ids=[f"track:{p.track_id}"],
                            zone_id=ln.id,
                            start_sec=st.last_t if st.last_t is not None else frame.media_time_sec,
                            trigger_sec=frame.media_time_sec,
                            end_sec=frame.media_time_sec,
                            status="ended",
                            evidence_level="visual",
                            reasons=[f"line={ln.id}", f"kind={ln.kind}", f"direction={direction}"],
                            metrics={
                                "sidePrev": st.last_stable_side,
                                "sideCur": stable,
                                "direction": direction,
                            },
                            quality={"bboxConf": p.bbox_conf},
                            clip_flags={},
                        )
                        out.append(ev)
                        st.crossings += 1
                    st.last_stable_side = stable
                st.last_t = frame.media_time_sec
                st.last_point = pt
        return out

    def finalize(self, *args, **kwargs) -> list[Event]:  # noqa: D401
        return []
