"""Двухклеточная схема вытеснения тока.

Внутренняя клетка с большим R и малым X добавляет момент при s=1 и почти
выходит из работы на малом скольжении. Это не расчёт поля в пазу.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def cage_impedance(slip: float, resistance: float, reactance: float) -> complex:
    if slip <= 0 or resistance <= 0 or reactance <= 0:
        raise ValueError("скольжение, R и X клетки должны быть положительными")
    return resistance / slip + 1j * reactance


def double_cage(
    voltage: complex,
    slip: float,
    r1: float,
    x1: float,
    r2: float,
    x2: float,
    r3: float,
    x3: float,
) -> dict[str, complex | float]:
    """Мощность зазора на фазу. Ветви клеток и эквивалент параллели считаются раздельно."""
    if r1 < 0 or x1 < 0:
        raise ValueError("сопротивление статора не может быть отрицательным")
    outer = cage_impedance(slip, r2, x2)
    inner = cage_impedance(slip, r3, x3)
    admit = 1.0 / outer + 1.0 / inner
    stator = r1 + 1j * x1
    internal = voltage / (1.0 + stator * admit)
    current_outer = internal / outer
    current_inner = internal / inner
    branch_power = abs(current_outer) ** 2 * (r2 / slip) + abs(current_inner) ** 2 * (r3 / slip)
    parallel = 1.0 / admit
    current = voltage / (stator + parallel)
    formula_power = abs(current) ** 2 * parallel.real
    return {
        "current": current,
        "current_outer": current_outer,
        "current_inner": current_inner,
        "branch_power_w": branch_power,
        "formula_power_w": formula_power,
        "power_error": abs(branch_power - formula_power) / max(branch_power, 1e-12),
    }


def single_cage_power(voltage: complex, slip: float, r1: float, x1: float, r2: float, x2: float) -> float:
    """Одна клетка, отдельно от параллели двух клеток."""
    rotor = cage_impedance(slip, r2, x2)
    current = voltage / (r1 + 1j * x1 + rotor)
    return abs(current) ** 2 * (r2 / slip)


def qualify() -> dict[str, Any]:
    voltage = 230.0 + 0j
    r1, x1 = 0.05, 0.2
    r2, x2 = 0.03, 0.3
    r3, x3 = 0.2, 0.05
    started = double_cage(voltage, 1.0, r1, x1, r2, x2, r3, x3)
    running = double_cage(voltage, 0.05, r1, x1, r2, x2, r3, x3)
    outer_only = single_cage_power(voltage, 1.0, r1, x1, r2, x2)
    inner_share = abs(running["current_inner"]) ** 2 * (r3 / 0.05) / float(running["branch_power_w"])
    reasons = []
    if float(started["power_error"]) > 1e-9 or float(running["power_error"]) > 1e-9:
        reasons.append("branches")
    if float(started["branch_power_w"]) <= outer_only * 1.5:
        reasons.append("starting")
    if inner_share >= 0.5:
        reasons.append("running")
    try:
        cage_impedance(0.0, r2, x2)
        reasons.append("slip")
    except ValueError:
        pass
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "power_error": max(float(started["power_error"]), float(running["power_error"])),
        "starting_ratio": float(started["branch_power_w"]) / outer_only,
        "inner_share": inner_share,
        "slot_field": False,
    }
