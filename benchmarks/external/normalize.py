"""Один общий сдвиг угла на остров. Фазы по отдельности не подгоняются."""

from __future__ import annotations

import math


def align_angle(reference_rad: float, actual_rad: float) -> float:
    return _wrap(actual_rad - reference_rad)


def _wrap(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi
