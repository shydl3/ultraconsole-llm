"""COCO17 关键点特征：躯干尺度、倾角、腕耳距离、肘角等。

所有距离除以躯干尺度 L = ||S - H||（肩中点到髋中点），L 太小或缺点时返回
None（未知），不用平均值兜底，避免掩盖单点缺失。
"""
from __future__ import annotations

import math
from typing import Sequence

from .contracts import Keypoint
from .geometry import Point, angle_from_vertical, distance, midpoint

# COCO17 索引常量
NOSE = 0
L_EYE, R_EYE = 1, 2
L_EAR, R_EAR = 3, 4
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16


def valid_kp(kps: Sequence[Keypoint], idx: int, min_conf: float) -> Point | None:
    """返回有效关键点像素坐标；置信度不足或坐标非有限返回 None。"""
    if idx < 0 or idx >= len(kps):
        return None
    kp = kps[idx]
    if kp.conf is None or kp.conf < min_conf:
        return None
    if not (math.isfinite(kp.x) and math.isfinite(kp.y)):
        return None
    # 排除疑似占位零点（Ultralytics 对未检出点填 (0, 0)）
    if kp.x == 0.0 and kp.y == 0.0:
        return None
    return (float(kp.x), float(kp.y))


def torso(kps: Sequence[Keypoint], min_conf: float) -> tuple[Point, Point, float] | None:
    """返回 (肩中点 S, 髋中点 H, 躯干尺度 L)。任一端点无效返回 None。"""
    ls = valid_kp(kps, L_SHOULDER, min_conf)
    rs = valid_kp(kps, R_SHOULDER, min_conf)
    lh = valid_kp(kps, L_HIP, min_conf)
    rh = valid_kp(kps, R_HIP, min_conf)
    if ls is None and rs is None:
        return None
    if lh is None and rh is None:
        return None
    s = ls if rs is None else (rs if ls is None else midpoint(ls, rs))
    h = lh if rh is None else (rh if lh is None else midpoint(lh, rh))
    L = distance(s, h)
    if L < 8.0:  # 躯干过小视为不可靠
        return None
    return s, h, L


def torso_lean_deg(kps: Sequence[Keypoint], min_conf: float) -> float | None:
    """躯干相对竖直方向的倾角（度）。0 = 完全竖直。"""
    t = torso(kps, min_conf)
    if t is None:
        return None
    s, h, _ = t
    # 竖直方向以 S→H 应"向下"为参考
    return angle_from_vertical(s, h)


def nose_below_shoulder(kps: Sequence[Keypoint], min_conf: float) -> bool | None:
    """鼻是否低于肩中点（y 更大）。缺点返回 None。"""
    t = torso(kps, min_conf)
    n = valid_kp(kps, NOSE, min_conf)
    if t is None or n is None:
        return None
    s, _, _ = t
    return n[1] > s[1]


def wrist_ear_ratio(kps: Sequence[Keypoint], min_conf: float, side: str) -> float | None:
    """同侧腕-耳距离 / L。side ∈ {'left', 'right'}；缺点返回 None。"""
    t = torso(kps, min_conf)
    if t is None:
        return None
    _, _, L = t
    if side == "left":
        w = valid_kp(kps, L_WRIST, min_conf)
        e = valid_kp(kps, L_EAR, min_conf)
    else:
        w = valid_kp(kps, R_WRIST, min_conf)
        e = valid_kp(kps, R_EAR, min_conf)
    if w is None or e is None:
        return None
    return distance(w, e) / L


def elbow_angle_deg(kps: Sequence[Keypoint], min_conf: float, side: str) -> float | None:
    """肘关节角度（度）：肩-肘-腕。0 = 完全折叠，180 = 伸直。"""
    if side == "left":
        s = valid_kp(kps, L_SHOULDER, min_conf)
        e = valid_kp(kps, L_ELBOW, min_conf)
        w = valid_kp(kps, L_WRIST, min_conf)
    else:
        s = valid_kp(kps, R_SHOULDER, min_conf)
        e = valid_kp(kps, R_ELBOW, min_conf)
        w = valid_kp(kps, R_WRIST, min_conf)
    if s is None or e is None or w is None:
        return None
    v1 = (s[0] - e[0], s[1] - e[1])
    v2 = (w[0] - e[0], w[1] - e[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return None
    cos_theta = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    cos_theta = max(-1.0, min(1.0, cos_theta))
    return math.degrees(math.acos(cos_theta))


def foot_point(kps: Sequence[Keypoint], min_conf: float, bbox: Sequence[float]) -> Point:
    """优先取两踝中点；缺失则退化为 bbox 底边中心。"""
    la = valid_kp(kps, L_ANKLE, min_conf)
    ra = valid_kp(kps, R_ANKLE, min_conf)
    if la is not None and ra is not None:
        return midpoint(la, ra)
    if la is not None:
        return la
    if ra is not None:
        return ra
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) * 0.5, y2)


def body_center(kps: Sequence[Keypoint], min_conf: float, bbox: Sequence[float]) -> Point | None:
    """躯干中心（用于人员距离/速度）。缺点退化到 bbox 中心。"""
    t = torso(kps, min_conf)
    if t is not None:
        s, h, _ = t
        return midpoint(s, h)
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) * 0.5, (y1 + y2) * 0.5)
