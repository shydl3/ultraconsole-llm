"""三值时间状态机：idle → candidate → active → ended/interrupted。

原子判断输出 `True/False/None`，`None` 表示"未知"（缺点、低置信、遮挡）。
状态机只在相邻两次观测都有效且间隔不超过 `max_gap_sec` 时累计受支持的
证据时间；未知不累计，也不当作正常恢复；短缺口保留候选（降低覆盖率），
长缺口触发 interrupted。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Tri = bool | None
State = Literal["idle", "candidate", "active", "ended", "interrupted"]


@dataclass
class TemporalParams:
    enter_sec: float = 3.0
    exit_sec: float = 1.0
    min_valid_samples: int = 5
    min_coverage: float = 0.6
    max_gap_sec: float = 1.0
    unknown_grace_sec: float = 0.5
    merge_gap_sec: float = 2.0


@dataclass
class TemporalSegment:
    start_sec: float
    trigger_sec: float
    end_sec: float
    valid_samples: int
    total_samples: int
    unknown_samples: int
    status: Literal["ended", "interrupted", "clipped_end"] = "ended"
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def coverage(self) -> float:
        return self.valid_samples / self.total_samples if self.total_samples else 0.0

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


class ThreeValuedStateMachine:
    """按主体（track_id / zone_id）分别实例化。"""

    def __init__(self, params: TemporalParams, *, clip_start: bool = False) -> None:
        self.p = params
        self.state: State = "idle"
        self._seg_start: float | None = None
        self._seg_trigger: float | None = None
        self._last_t: float | None = None
        self._last_valid_t: float | None = None
        self._supported_sec: float = 0.0
        self._negative_sec: float = 0.0
        self._valid_n: int = 0
        self._total_n: int = 0
        self._unknown_n: int = 0
        self._clip_start = clip_start
        self._unknown_run_start: float | None = None
        self.finished: list[TemporalSegment] = []

    # ------------------------------------------------------------------
    def update(self, t: float, value: Tri, extra_metrics: dict[str, float] | None = None) -> TemporalSegment | None:
        """喂入一次观测；返回本步刚结束的事件（如果有）。"""
        self._total_n += 1
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        gap_ok = self._last_t is None or dt <= self.p.max_gap_sec
        self._last_t = t

        # 首次观测若为 True，视为片段起始即已处于活跃（clipped_start）
        if self._seg_start is None and value is True and self._clip_start and self._total_n == 1:
            self._seg_start = t
            self._seg_trigger = t
            self.state = "active"

        if value is None:
            self._unknown_n += 1
            if self._unknown_run_start is None:
                self._unknown_run_start = t
            unknown_dur = t - self._unknown_run_start
            if self.state in ("candidate", "active") and unknown_dur > self.p.unknown_grace_sec:
                # 长未知 → interrupted，事件被截断
                return self._close(t, status="interrupted", extra=extra_metrics)
            # 短未知不累计正/负证据时间
            return None

        # 有确定观测
        self._unknown_run_start = None
        pending: TemporalSegment | None = None
        if not gap_ok:
            # 间隔过大 → 现有段中断，重新起段
            if self.state in ("candidate", "active"):
                pending = self._close(t, status="interrupted", extra=extra_metrics)
            self._reset()

        if value is True:
            self._valid_n += 1
            # 受支持证据时间 = 与上一次"有效"观测的间隔（含可容忍的短未知缺口），
            # 超过 max_gap+grace 的长缺口不累计。
            if self._last_valid_t is not None:
                gap = t - self._last_valid_t
                if gap <= self.p.max_gap_sec + self.p.unknown_grace_sec:
                    self._supported_sec += max(0.0, gap)
            self._last_valid_t = t
            if self.state == "idle":
                self.state = "candidate"
                self._seg_start = t
                self._seg_trigger = None
                self._supported_sec = 0.0
            elif self.state == "candidate":
                if self._supported_sec >= self.p.enter_sec and self._valid_n >= self.p.min_valid_samples:
                    self.state = "active"
                    self._seg_trigger = t
                    return pending
            # active 状态无动作
            return pending
        else:  # value is False
            self._last_valid_t = t
            if self.state == "active":
                self._negative_sec += dt
                if self._negative_sec >= self.p.exit_sec:
                    return self._close(t, status="ended", extra=extra_metrics)
            elif self.state == "candidate":
                # 未达进入条件即回落，重置候选
                self._reset()
            else:
                self._negative_sec = 0.0
        return pending

    # ------------------------------------------------------------------
    def finalize(self, t_end: float) -> TemporalSegment | None:
        """视频结束时若仍处于 candidate/active，输出 clipped_end 段。"""
        if self.state in ("candidate", "active") and self._seg_start is not None:
            return self._close(t_end, status="clipped_end")
        return None

    # ------------------------------------------------------------------
    def _close(self, t: float, *, status: str, extra: dict[str, float] | None = None) -> TemporalSegment | None:
        if self._seg_start is None or self.state not in ("candidate", "active"):
            self._reset()
            return None
        # candidate 未达触发时间 → 视为噪声，不产出事件
        if self.state == "candidate":
            self._reset()
            return None
        seg = TemporalSegment(
            start_sec=self._seg_start,
            trigger_sec=self._seg_trigger if self._seg_trigger is not None else t,
            end_sec=t,
            valid_samples=self._valid_n,
            total_samples=self._total_n,
            unknown_samples=self._unknown_n,
            status=status,  # type: ignore[arg-type]
            metrics=dict(extra or {}),
        )
        # 覆盖率不达标 → 标记但仍然产出，让上层决定是否降级为 unknown
        seg.metrics.setdefault("coverage", seg.coverage)
        seg.metrics.setdefault("supported_sec", self._supported_sec)
        self.finished.append(seg)
        self._reset()
        return seg

    def _reset(self) -> None:
        self.state = "idle"
        self._seg_start = None
        self._seg_trigger = None
        self._supported_sec = 0.0
        self._negative_sec = 0.0
        self._valid_n = 0
        self._total_n = 0
        self._unknown_n = 0
        self._unknown_run_start = None


class EMASmoother:
    """按时间常数的指数平滑。alpha = 1 - exp(-dt / tau)。缺点不插值。"""

    def __init__(self, tau_sec: float = 0.4) -> None:
        self.tau = max(1e-3, tau_sec)
        self.value: float | None = None
        self._last_t: float | None = None

    def update(self, t: float, sample: float | None) -> float | None:
        if sample is None:
            return self.value
        if self.value is None or self._last_t is None:
            self.value = float(sample)
        else:
            dt = max(0.0, t - self._last_t)
            alpha = 1.0 - pow(2.718281828459045, -dt / self.tau)
            self.value = (1 - alpha) * self.value + alpha * float(sample)
        self._last_t = t
        return self.value
