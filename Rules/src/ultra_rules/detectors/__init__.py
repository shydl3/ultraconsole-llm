"""原子规则 detectors。"""
from __future__ import annotations

from .access_crossing import AccessCrossingDetector
from .post_vacancy import PostVacancyDetector
from .pose_hand_near_ear import HandNearEarDetector
from .pose_slumped import SlumpedDetector

__all__ = [
    "AccessCrossingDetector",
    "PostVacancyDetector",
    "SlumpedDetector",
    "HandNearEarDetector",
]
