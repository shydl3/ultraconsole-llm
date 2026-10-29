"""COCO17 特征测试：缺点/低置信/退化尺度必须返回 None（未知）。"""
from __future__ import annotations

import math

from ultra_rules.contracts import Keypoint
from ultra_rules.features import (
    elbow_angle_deg,
    nose_below_shoulder,
    torso,
    torso_lean_deg,
    valid_kp,
    wrist_ear_ratio,
)


def _kps(overrides: dict[int, tuple[float, float, float]] | None = None, n: int = 17):
    """默认全部无效（conf=0），按 overrides 填充。"""
    pts = [Keypoint(x=0.0, y=0.0, conf=0.0) for _ in range(n)]
    for idx, (x, y, c) in (overrides or {}).items():
        pts[idx] = Keypoint(x=x, y=y, conf=c)
    return pts


def test_valid_kp_rejects_low_conf_and_zero_placeholder():
    kps = _kps({0: (10, 10, 0.9), 1: (5, 5, 0.1), 2: (0, 0, 0.9)})
    assert valid_kp(kps, 0, 0.5) == (10.0, 10.0)
    assert valid_kp(kps, 1, 0.5) is None  # 低置信
    assert valid_kp(kps, 2, 0.5) is None  # 占位零点
    assert valid_kp(kps, 3, 0.5) is None  # 默认无效


def test_torso_requires_both_ends():
    # 只有肩没有髋 → None
    kps = _kps({5: (100, 100, 0.9), 6: (140, 100, 0.9)})
    assert torso(kps, 0.5) is None
    # 肩+髋齐全 → 有尺度
    kps2 = _kps({5: (100, 100, 0.9), 6: (140, 100, 0.9), 11: (100, 200, 0.9), 12: (140, 200, 0.9)})
    t = torso(kps2, 0.5)
    assert t is not None
    s, h, L = t
    assert math.isclose(L, 100.0, rel_tol=1e-6)


def test_torso_rejects_tiny_scale():
    kps = _kps({5: (100, 100, 0.9), 6: (102, 100, 0.9), 11: (100, 102, 0.9), 12: (102, 102, 0.9)})
    assert torso(kps, 0.5) is None  # L<8


def test_torso_lean_vertical_is_zero():
    kps = _kps({5: (100, 100, 0.9), 6: (120, 100, 0.9), 11: (100, 200, 0.9), 12: (120, 200, 0.9)})
    lean = torso_lean_deg(kps, 0.5)
    assert lean is not None and math.isclose(lean, 0.0, abs_tol=1e-6)


def test_nose_below_shoulder():
    base = {5: (100, 100, 0.9), 6: (120, 100, 0.9), 11: (100, 200, 0.9), 12: (120, 200, 0.9)}
    kps_low = _kps({**base, 0: (110, 150, 0.9)})  # 鼻在肩下
    kps_high = _kps({**base, 0: (110, 50, 0.9)})  # 鼻在肩上
    kps_none = _kps(base)  # 无鼻
    assert nose_below_shoulder(kps_low, 0.5) is True
    assert nose_below_shoulder(kps_high, 0.5) is False
    assert nose_below_shoulder(kps_none, 0.5) is None


def test_wrist_ear_ratio_normalized():
    # 躯干 L=100；左腕到左耳距离 30 → ratio 0.3
    kps = _kps({
        5: (100, 100, 0.9), 6: (120, 100, 0.9),
        11: (100, 200, 0.9), 12: (120, 200, 0.9),
        3: (100, 80, 0.9),   # 左耳
        9: (100, 110, 0.9),  # 左腕，距左耳 30
    })
    r = wrist_ear_ratio(kps, 0.5, "left")
    assert r is not None and math.isclose(r, 0.3, rel_tol=1e-6)
    assert wrist_ear_ratio(kps, 0.5, "right") is None  # 右侧缺点


def test_elbow_angle_straight_vs_folded():
    straight = _kps({5: (100, 100, 0.9), 7: (100, 150, 0.9), 9: (100, 200, 0.9)})
    folded = _kps({5: (100, 100, 0.9), 7: (100, 150, 0.9), 9: (150, 150, 0.9)})
    a_s = elbow_angle_deg(straight, 0.5, "left")
    a_f = elbow_angle_deg(folded, 0.5, "left")
    assert a_s is not None and math.isclose(a_s, 180.0, abs_tol=1e-3)
    assert a_f is not None and math.isclose(a_f, 90.0, abs_tol=1e-3)
    assert elbow_angle_deg(_kps({5: (100, 100, 0.9)}), 0.5, "left") is None
