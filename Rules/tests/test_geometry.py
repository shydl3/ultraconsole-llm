"""几何原语单元测试：反例优先，不复述实现常量。"""
from __future__ import annotations

import math

from ultra_rules.geometry import (
    angle_from_vertical,
    bbox_bottom_center,
    crossed_directed,
    distance,
    iou_xyxy,
    point_in_polygon,
    polygon_area,
    signed_side,
)

SQUARE = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


def test_point_in_polygon_inside_and_outside():
    assert point_in_polygon((0.5, 0.5), SQUARE) is True
    assert point_in_polygon((1.5, 0.5), SQUARE) is False
    assert point_in_polygon((-0.1, 0.5), SQUARE) is False


def test_point_in_polygon_concave():
    # L 形：缺口处应在外
    L = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
    assert point_in_polygon((0.5, 0.5), L) is True
    assert point_in_polygon((1.5, 1.5), L) is False  # 缺口
    assert point_in_polygon((0.5, 1.5), L) is True


def test_point_in_polygon_degenerate():
    assert point_in_polygon((0.5, 0.5), [(0, 0), (1, 1)]) is False  # 两点非多边形
    assert point_in_polygon((0.5, 0.5), []) is False


def test_signed_side_left_right():
    seg = ((0.0, 0.0), (0.0, 1.0))  # 竖直向下 a→b
    # 右手系：x>0 在 a→b 的左侧还是右侧取决于符号约定；这里只要求两侧异号
    left = signed_side((1.0, 0.5), seg)
    right = signed_side((-1.0, 0.5), seg)
    assert left * right < 0
    assert signed_side((0.0, 0.5), seg) == 0.0


def test_crossed_directed_forward_and_backward():
    seg = ((0.5, 0.0), (0.5, 1.0))
    # 从 x<0.5 到 x>0.5 与反向，应恰好一个为 forward
    fwd = crossed_directed((0.4, 0.5), (0.6, 0.5), seg)
    bwd = crossed_directed((0.6, 0.5), (0.4, 0.5), seg)
    assert fwd != bwd
    # 不跨线不触发
    assert crossed_directed((0.4, 0.5), (0.45, 0.5), seg) is False
    # 平行移动不触发
    assert crossed_directed((0.4, 0.2), (0.4, 0.8), seg) is False


def test_crossed_directed_outside_segment_no_trigger():
    # 线段只在 y∈[0,1]；在 y=5 处横穿不应触发
    seg = ((0.5, 0.0), (0.5, 1.0))
    assert crossed_directed((0.4, 5.0), (0.6, 5.0), seg) is False


def test_distance_and_angle():
    assert math.isclose(distance((0, 0), (3, 4)), 5.0)
    # 竖直向下 = 0 度
    assert math.isclose(angle_from_vertical((0, 0), (0, 10)), 0.0, abs_tol=1e-6)
    # 水平 = 90 度
    assert math.isclose(angle_from_vertical((0, 0), (10, 0)), 90.0, abs_tol=1e-6)
    # 竖直向上 = 180 度
    assert math.isclose(angle_from_vertical((0, 0), (0, -10)), 180.0, abs_tol=1e-6)
    # 零向量不崩溃
    assert angle_from_vertical((1, 1), (1, 1)) == 0.0


def test_bbox_bottom_center():
    assert bbox_bottom_center([0, 0, 10, 20]) == (5.0, 20.0)


def test_iou_xyxy():
    assert math.isclose(iou_xyxy([0, 0, 10, 10], [0, 0, 10, 10]), 1.0)
    assert iou_xyxy([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    half = iou_xyxy([0, 0, 10, 10], [5, 0, 15, 10])
    assert 0.0 < half < 1.0


def test_polygon_area():
    assert math.isclose(polygon_area(SQUARE), 1.0)
    assert polygon_area([(0, 0), (1, 1)]) == 0.0
