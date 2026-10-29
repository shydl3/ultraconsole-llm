"""通行/拦截工作流（P3 核心）。

对每个 track_id 建立一次 passage，聚合"接近 → 检查区停留 → 互动 → 通过/
返回"过程，输出结构化事件：

- `passage.checked`：观察到检查样动作（保安近身 + 停留）后放行。
- `passage.no_check_observed`：完整通过（穿越 exit 线）但整个可见区间内
  没有观察到与保安的互动。**不等同于**"未拦截违规"，只表示视觉上没有证据。
- `passage.returned`：进入检查区后折返（反向穿越 entry 线）。
- `passage.incomplete`：视频结束时仍处于中间状态，或轨迹长时缺失。

角色识别：
- `guard_zone_id`（或 kind='post' 的 zone）内累计驻留 >= `guard_min_dwell_sec`
  的 track 标记为 guard，evidence=dwell；不做跨文件身份合并。
- 没有 guard 时，仍可产出 `no_guard_observed` 状态供上层判断。

互动判据（保守）：guard 的任一腕关键点落在 subject 的 bbox 内（外扩
`wrist_pad_px`），或两者 bbox IoU >= `iou_min`，累计 >= `interact_min_sec`。
仅表示"空间接近"，不声称发生身体接触或手持探测器检查。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..contracts import CameraProfile, Event, FrameObservation, PersonObservation, RulesConfig, ZoneConfig
from ..features import valid_kp, L_WRIST, R_WRIST
from ..geometry import bbox_bottom_center, bbox_center, iou_xyxy, point_in_polygon, polygon_to_pixel, signed_side


def _kp_in_bbox(pt: tuple[float, float], bbox: list[float], pad: float) -> bool:
    x1, y1, x2, y2 = bbox
    return (x1 - pad) <= pt[0] <= (x2 + pad) and (y1 - pad) <= pt[1] <= (y2 + pad)


@dataclass
class _PassageState:
    track_id: int
    first_seen: float
    last_seen: float
    approach_enter_t: float | None = None
    check_enter_t: float | None = None
    check_exit_t: float | None = None
    exit_forward_t: float | None = None
    entry_backward_t: float | None = None
    interact_start_t: float | None = None
    interact_total_sec: float = 0.0
    interact_guard_ids: set[int] = field(default_factory=set)
    dwell_by_zone: dict[str, float] = field(default_factory=dict)
    samples_in_check: int = 0
    max_iou_with_guard: float = 0.0
    ended_reason: str | None = None
    last_point: tuple[float, float] | None = None
    guard_flag: bool = False

    def is_guard(self, min_dwell: float, guard_zone_id: str | None) -> bool:
        if not guard_zone_id:
            return False
        return self.dwell_by_zone.get(guard_zone_id, 0.0) >= min_dwell


class PassageWorkflow:
    rule_id = "passage.check"
    rule_version = "1"

    def __init__(self, cfg: RulesConfig, profile: CameraProfile, params: dict[str, Any] | None = None) -> None:
        self.cfg = cfg
        self.profile = profile
        p = params or {}
        self.wrist_pad_px: float = float(p.get("wristPadPx", 24.0))
        self.iou_min: float = float(p.get("iouMin", 0.08))
        self.interact_min_sec: float = float(p.get("interactMinSec", 0.6))
        self.check_dwell_sec: float = float(p.get("checkDwellSec", 1.0))
        self.max_gap_sec: float = float(p.get("maxGapSec", 2.0))
        self.guard_min_span_sec: float = float(p.get("guardMinSpanSec", 8.0))
        self.min_conf: float = float(cfg.thresholds.keypoint_conf)
        self._approach = self._find_zone("approach")
        self._check = self._find_zone("check")
        self._post = self._find_zone("post") or (
            next((z for z in profile.zones if z.id == profile.guard_zone_id), None) if profile.guard_zone_id else None
        )
        self._guard_zone_id = (self._post.id if self._post else None) or profile.guard_zone_id
        self._guard_min_dwell = float(profile.guard_min_dwell_sec or 3.0)
        self._state: dict[int, _PassageState] = {}
        self._closed: list[tuple[_PassageState, str]] = []  # (state, outcome)

    def _find_zone(self, kind: str) -> ZoneConfig | None:
        return next((z for z in self.profile.zones if z.kind == kind), None)

    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._check is not None

    def reset(self) -> None:
        self._state.clear()
        self._closed.clear()

    # ------------------------------------------------------------------
    def update(self, frame: FrameObservation) -> None:
        if not self.enabled:
            return
        w, h = frame.width, frame.height
        check_poly = polygon_to_pixel([(p[0], p[1]) for p in self._check.points], w, h) if self._check else []
        approach_poly = (
            polygon_to_pixel([(p[0], p[1]) for p in self._approach.points], w, h) if self._approach else []
        )
        post_poly = polygon_to_pixel([(p[0], p[1]) for p in self._post.points], w, h) if self._post else []

        # 1) 更新每个 track 的驻留时长与状态
        current_ids: set[int] = set()
        for p in frame.persons:
            if p.track_id is None:
                continue
            current_ids.add(p.track_id)
            st = self._state.get(p.track_id)
            if st is None:
                st = _PassageState(track_id=p.track_id, first_seen=frame.media_time_sec, last_seen=frame.media_time_sec)
                self._state[p.track_id] = st
            else:
                dt = max(0.0, frame.media_time_sec - st.last_seen)
                if dt > self.max_gap_sec and st.ended_reason is None:
                    # 长时缺失：不立即关闭，等 finalize 或再次出现
                    pass
                st.last_seen = frame.media_time_sec

            foot = bbox_bottom_center(p.bbox)
            ctr = bbox_center(p.bbox)
            if post_poly and (point_in_polygon(foot, post_poly) or point_in_polygon(ctr, post_poly)):
                prev_t = st.dwell_by_zone.get("_last_post_t")
                dt_frame = frame.media_time_sec - (prev_t if prev_t is not None else frame.media_time_sec)
                if prev_t is not None and 0 <= dt_frame <= self.max_gap_sec:
                    st.dwell_by_zone[self._guard_zone_id or "post"] = st.dwell_by_zone.get(
                        self._guard_zone_id or "post", 0.0
                    ) + dt_frame
                st.dwell_by_zone["_last_post_t"] = frame.media_time_sec

            if approach_poly and st.approach_enter_t is None:
                if point_in_polygon(foot, approach_poly) or point_in_polygon(ctr, approach_poly):
                    st.approach_enter_t = frame.media_time_sec

            in_check = bool(check_poly) and (point_in_polygon(foot, check_poly) or point_in_polygon(ctr, check_poly))
            if in_check:
                if st.check_enter_t is None:
                    st.check_enter_t = frame.media_time_sec
                st.samples_in_check += 1
                st.check_exit_t = frame.media_time_sec

        # 2) 判定互动（guard ↔ entrant）
        guards = [p for p in frame.persons if p.track_id is not None and self._is_guard(p.track_id)]
        entrants = [p for p in frame.persons if p.track_id is not None and not self._is_guard(p.track_id)]
        dt_frame = 0.0
        for g in guards:
            for e in entrants:
                st = self._state.get(e.track_id or -1)
                if st is None:
                    continue
                # 计算互动
                iou = iou_xyxy(g.bbox, e.bbox)
                st.max_iou_with_guard = max(st.max_iou_with_guard, iou)
                wrist_touch = False
                gkps = g.keypoints
                for idx in (L_WRIST, R_WRIST):
                    pt = valid_kp(gkps, idx, self.min_conf)
                    if pt is not None and _kp_in_bbox(pt, e.bbox, self.wrist_pad_px):
                        wrist_touch = True
                        break
                interacting = wrist_touch or iou >= self.iou_min
                if interacting:
                    st.interact_guard_ids.add(int(g.track_id or -1))
                    if st.interact_start_t is None:
                        st.interact_start_t = frame.media_time_sec
                    # 用最近一次时间差累计（避免依赖固定 fps）
                    if st.last_seen is not None:
                        # 使用上一帧到当前帧的间隔近似
                        pass
                # 每帧累计（近似按帧率）
                if interacting:
                    st.interact_total_sec += max(0.0, min(self.max_gap_sec, 1.0 / max(1.0, self.cfg.thresholds.sample_fps)))

        # 3) 检测通过 / 折返（借助 profile 中的 entry/exit 线；如果没有线，则用 check 区离开判定）
        for tid in list(current_ids):
            st = self._state.get(tid)
            if st is None or st.ended_reason is not None:
                continue
            p = next((x for x in frame.persons if x.track_id == tid), None)
            if p is None:
                continue
            if self._is_guard(tid):
                continue
            foot = bbox_bottom_center(p.bbox)
            prev = st.last_point
            # 使用有向线段：exit 线 forward = 通过；entry 线 backward = 折返
            if prev is not None:
                for ln in self.profile.lines:
                    a = (ln.a[0] * frame.width, ln.a[1] * frame.height)
                    b = (ln.b[0] * frame.width, ln.b[1] * frame.height)
                    seg = (a, b)
                    prev_side = signed_side(prev, seg)
                    cur_side = signed_side(foot, seg)
                    if ln.kind == "exit":
                        if prev_side < 0 <= cur_side:
                            st.exit_forward_t = frame.media_time_sec
                            st.ended_reason = "exited_forward"
                    elif ln.kind == "entry":
                        if prev_side > 0 >= cur_side:
                            st.entry_backward_t = frame.media_time_sec
                            st.ended_reason = "returned"
            st.last_point = foot

    # ------------------------------------------------------------------
    def _is_guard(self, track_id: int) -> bool:
        st = self._state.get(track_id)
        if st is None:
            return False
        return bool(getattr(st, "guard_flag", False)) or st.is_guard(self._guard_min_dwell, self._guard_zone_id or "post")

    def _assign_roles(self, t_end: float) -> None:
        """用"在场跨度"识别常驻保安：跨度 >= max(guard_min_span, 0.4*t_end) 视为 guard。

        保安整段在场，通行者短暂出现；该启发式与 post 区驻留互为补充。
        """
        spans = {tid: (st.last_seen - st.first_seen) for tid, st in self._state.items()}
        if not spans:
            return
        threshold = max(self.guard_min_span_sec, 0.4 * max(1.0, t_end))
        for tid, span in spans.items():
            st = self._state[tid]
            if span >= threshold:
                st.guard_flag = True

    # ------------------------------------------------------------------
    def finalize(self, run_id: str, video_id: str, profile_id: str | None, t_end: float) -> list[Event]:
        """视频结束，把所有 passage 转成事件。"""
        self._assign_roles(t_end)
        events: list[Event] = []
        for tid, st in self._state.items():
            if self._is_guard(tid):
                continue  # guard 自身不作为 passage 主体
            outcome = self._classify(st, t_end)
            ev = self._make_event(run_id, video_id, profile_id, st, outcome, t_end)
            if ev is not None:
                events.append(ev)
        return events

    # ------------------------------------------------------------------
    def _classify(self, st: _PassageState, t_end: float) -> str:
        interacted = st.interact_total_sec >= self.interact_min_sec and bool(st.interact_guard_ids)
        stayed_long = st.check_enter_t is not None and st.check_exit_t is not None and (
            st.check_exit_t - st.check_enter_t >= self.check_dwell_sec
        )
        # 通过优先：完整穿越 exit 线后，再看是否有检查证据
        if st.ended_reason == "exited_forward":
            if interacted or stayed_long:
                return "checked"
            return "passed_without_observed_check"
        if st.ended_reason == "returned":
            if interacted or stayed_long:
                return "returned_after_check"
            return "returned"
        # 未穿越任何线：视频结束仍在场内或从未进入检查区
        if st.check_enter_t is None:
            return "never_entered_check"
        if st.last_seen < t_end - self.max_gap_sec:
            return "track_lost"
        return "incomplete_at_end"

    def _make_event(
        self,
        run_id: str,
        video_id: str,
        profile_id: str | None,
        st: _PassageState,
        outcome: str,
        t_end: float,
    ) -> Event | None:
        # 过滤噪声：从未进入 approach/check 且未产生任何互动
        if outcome == "never_entered_check" and st.interact_total_sec <= 0 and st.approach_enter_t is None:
            return None
        start = st.approach_enter_t or st.check_enter_t or st.first_seen
        end = st.exit_forward_t or st.entry_backward_t or st.check_exit_t or st.last_seen or t_end
        rule_id = f"passage.{outcome}"
        evidence_level: str = "workflow"
        reasons = [
            f"outcome={outcome}",
            f"check_enter={st.check_enter_t}",
            f"interact_total_sec={st.interact_total_sec:.2f}",
            f"interact_guards={sorted(st.interact_guard_ids)}",
            f"max_iou_with_guard={st.max_iou_with_guard:.2f}",
        ]
        status: str = "ended"
        clip_flags = {"startClipped": False, "endClipped": False}
        if outcome in ("incomplete_at_end", "track_lost"):
            status = "interrupted" if outcome == "track_lost" else "clipped_end"
            clip_flags["endClipped"] = outcome == "incomplete_at_end"
        return Event(
            event_id=f"{video_id}-{self.rule_id}-{st.track_id}-{int(start * 1000)}",
            rule_id=rule_id,
            rule_version=self.rule_version,
            run_id=run_id,
            video_id=video_id,
            camera_profile_id=profile_id,
            subject_ids=[f"track:{st.track_id}"],
            zone_id=self._check.id if self._check else None,
            start_sec=float(start),
            trigger_sec=float(st.check_enter_t or start),
            end_sec=float(end),
            status=status,  # type: ignore[arg-type]
            evidence_level=evidence_level,  # type: ignore[arg-type]
            reasons=reasons,
            metrics={
                "firstSeen": st.first_seen,
                "lastSeen": st.last_seen,
                "approachEnter": st.approach_enter_t,
                "checkEnter": st.check_enter_t,
                "checkExit": st.check_exit_t,
                "exitForward": st.exit_forward_t,
                "entryBackward": st.entry_backward_t,
                "interactStart": st.interact_start_t,
                "interactTotalSec": st.interact_total_sec,
                "interactGuards": sorted(st.interact_guard_ids),
                "maxIouWithGuard": st.max_iou_with_guard,
                "samplesInCheck": st.samples_in_check,
                "guardDwell": st.dwell_by_zone.get(self._guard_zone_id or "post", 0.0),
            },
            quality={"guardIdentified": bool(self._guard_zone_id)},
            clip_flags=clip_flags,
        )
