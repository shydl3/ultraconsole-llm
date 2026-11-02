"""Polygon containment for semantic POI tracking."""
from __future__ import annotations

from typing import Sequence

Point = tuple[float, float]
Polygon = Sequence[Point]


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
