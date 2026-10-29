"""三值时间状态机测试：覆盖进入/退出/未知/中断/截断。"""
from __future__ import annotations

from ultra_rules.temporal import EMASmoother, TemporalParams, ThreeValuedStateMachine


def _params(**kw) -> TemporalParams:
    base = dict(enter_sec=1.0, exit_sec=0.5, min_valid_samples=2, min_coverage=0.5, max_gap_sec=1.0, unknown_grace_sec=0.5)
    base.update(kw)
    return TemporalParams(**base)


def feed(sm, seq):
    out = []
    for t, v in seq:
        r = sm.update(t, v)
        if r is not None:
            out.append(r)
    return out


def test_sustained_true_then_false_emits_one_event():
    sm = ThreeValuedStateMachine(_params())
    seq = [(0.0, True), (0.5, True), (1.0, True), (1.5, True), (2.0, False), (2.6, False)]
    evs = feed(sm, seq)
    assert len(evs) == 1
    e = evs[0]
    assert e.status == "ended"
    assert e.start_sec == 0.0
    assert e.end_sec == 2.0  # 退出阈值在首个 False 处即满足


def test_short_burst_no_event():
    sm = ThreeValuedStateMachine(_params())
    evs = feed(sm, [(0.0, True), (0.2, True), (0.4, False)])
    assert evs == []


def test_unknown_does_not_count_as_support_or_recovery():
    sm = ThreeValuedStateMachine(_params())
    # True, 短 unknown, True 仍应累计（短缺口保留候选）
    evs = feed(sm, [(0.0, True), (0.3, None), (0.6, True), (1.2, True), (2.0, False), (2.6, False)])
    assert len(evs) == 1


def test_long_unknown_interrupts():
    sm = ThreeValuedStateMachine(_params(unknown_grace_sec=0.5))
    evs = feed(sm, [(0.0, True), (0.5, True), (1.0, True), (1.5, None), (3.0, None)])
    # 长 unknown 触发 interrupted
    assert len(evs) == 1
    assert evs[0].status == "interrupted"


def test_big_gap_interrupts_and_resets():
    sm = ThreeValuedStateMachine(_params(max_gap_sec=1.0))
    evs = feed(sm, [(0.0, True), (0.5, True), (1.0, True), (5.0, True), (6.0, True), (7.0, False), (7.6, False)])
    # 第一段因 gap 中断；第二段重新起段并在退出时结束
    statuses = [e.status for e in evs]
    assert "interrupted" in statuses


def test_finalize_clipped_end():
    sm = ThreeValuedStateMachine(_params())
    feed(sm, [(0.0, True), (0.5, True), (1.0, True), (1.5, True)])
    seg = sm.finalize(2.0)
    assert seg is not None
    assert seg.status == "clipped_end"


def test_clip_start_first_true_active():
    sm = ThreeValuedStateMachine(_params(), clip_start=True)
    evs = feed(sm, [(0.0, True), (0.5, True), (1.0, True), (2.0, False), (2.6, False)])
    assert len(evs) == 1
    assert evs[0].start_sec == 0.0


def test_ema_smoother_ignores_none():
    s = EMASmoother(tau_sec=0.4)
    assert s.update(0.0, 10.0) == 10.0
    assert s.update(0.5, None) == 10.0  # None 不改变
    v = s.update(1.0, 0.0)
    assert v is not None and 0.0 < v < 10.0
