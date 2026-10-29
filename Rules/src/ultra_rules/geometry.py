"""几何原语：点在多边形、有向线段穿越、距离、角度。

坐标策略：区域/线段配置使用归一化 [0, 1]，实际计算时按帧宽高恢复像素。
角度计算使用等比例像素空间（避免宽高不等造成的角度失真），做法是在归一化
坐标下计算，然后按 (w, h) 恢复到"方形像素"再算角度。
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence


Point = tuple[float, float]
Segment = tuple[Point, Point]
Polygon = Sequence[Point]


def to_pixel(pt: Point, width: int, height: int) -> Point:
    return (pt[0] * width, pt[1] * height)


def polygon_to_pixel(poly: Polygon, width: int, height: int) -> list[Point]:
    return [to_pixel(p, width, height) for p in poly]


def point_in_polygon(pt: Point, poly: Polygon) -> bool:
    """射线法，poly 为像素或归一化坐标均可（保持同一空间）。"""
    n = len(poly)
    if n < 3:
        return False
    x, y = pt
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _orient(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    """闭区间线段相交，用于判断轨迹段是否穿越有向边界。"""
    d1 = _orient(q1, q2, p1)
    d2 = _orient(q1, q2, p2)
    d3 = _orient(p1, p2, q1)
    d4 = _orient(p1, p2, q2)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    return False


def signed_side(pt: Point, seg: Segment) -> float:
    """点相对有向线段 a→b 的符号侧。>0 在左侧，<0 在右侧，≈0 在线上。"""
    (ax, ay), (bx, by) = seg
    return (bx - ax) * (pt[1] - ay) - (by - ay) * (pt[0] - ax)


def crossed_directed(prev: Point, cur: Point, seg: Segment) -> bool:
    """判断 prev→cur 是否沿 a→b 的正方向穿越（即从右侧到左侧）。"""
    if not segments_intersect(prev, cur, seg[0], seg[1]):
        return False
    s_prev = signed_side(prev, seg)
    s_cur = signed_side(cur, seg)
    # 负 → 正 表示按右手系从 a→b 的右侧穿到左侧
    return s_prev < 0 <= s_cur or s_prev <= 0 < s_cur


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def midpoint(a: Point, b: Point) -> Point:
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)


def bbox_bottom_center(bbox: Sequence[float]) -> Point:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) * 0.5, y2)


def bbox_center(bbox: Sequence[float]) -> Point:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) * 0.5, (y1 + y2) * 0.5)


def angle_from_vertical(a: Point, b: Point) -> float:
    """向量 a→b 与"向下竖直方向"的夹角（度）。0° 表示 b 在 a 正下方。"""
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    if dx == 0 and dy == 0:
        return 0.0
    # 竖直向下参考向量 (0, 1)
    cos_theta = dy / math.hypot(dx, dy)
    cos_theta = max(-1.0, min(1.0, cos_theta))
    return math.degrees(math.acos(cos_theta))


def iou_xyxy(a: Sequence[float], b: Sequence[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def polygon_area(poly: Polygon) -> float:
    n = len(poly)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def points_in_polygon(pts: Iterable[Point], poly: Polygon) -> int:
    return sum(1 for p in pts if point_in_polygon(p, poly))
