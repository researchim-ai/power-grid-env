"""Ограничения инвертора, накопителя и cold-load pickup.

Эти функции задают исполненную мощность до вызова backend. Усечённая
реактивная мощность не превращается в активную, а SoC интегрируется по
фактически исполненной мощности.
"""

from __future__ import annotations

import math


def capability_q(active_w: float, s_rated_va: float, intercept_var: float, slope_var_per_w: float) -> float:
    """Реактивный потолок — меньший из круга S и полевой прямой Q = a − b P."""
    if s_rated_va <= 0:
        raise ValueError("Sном должна быть положительной")
    if abs(active_w) > s_rated_va:
        return 0.0
    circle = math.sqrt(s_rated_va**2 - active_w**2)
    field = intercept_var - slope_var_per_w * active_w
    return min(circle, field)


def apparent_limit(active_w: float, reactive_var: float, s_rated_va: float, priority: str) -> tuple[float, float]:
    """Обрезка по кругу S. priority='P' сохраняет P, priority='Q' сохраняет Q."""
    if s_rated_va <= 0:
        raise ValueError("Sном должна быть положительной")
    if priority == "P":
        active = max(-s_rated_va, min(s_rated_va, active_w))
        room = math.sqrt(max(0.0, s_rated_va**2 - active**2))
        reactive = max(-room, min(room, reactive_var))
        return active, reactive
    if priority == "Q":
        reactive = max(-s_rated_va, min(s_rated_va, reactive_var))
        room = math.sqrt(max(0.0, s_rated_va**2 - reactive**2))
        active = max(-room, min(room, active_w))
        return active, reactive
    raise ValueError(f"неизвестный приоритет {priority}")


def interpolate_curve(curve: list[tuple[float, float]], x_value: float) -> float:
    if not curve:
        raise ValueError("пустая характеристика")
    points = sorted(curve, key=lambda item: item[0])
    if x_value <= points[0][0]:
        return points[0][1]
    if x_value >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x_value <= x1:
            if x1 == x0:
                return y1
            weight = (x_value - x0) / (x1 - x0)
            return y0 + weight * (y1 - y0)
    return points[-1][1]


def apply_ramp(previous_w: float, requested_w: float, ramp_w_per_s: float | None, dt_s: float) -> float:
    if ramp_w_per_s is None:
        return requested_w
    if ramp_w_per_s < 0 or dt_s < 0:
        raise ValueError("ramp и dt не могут быть отрицательными")
    delta = max(-ramp_w_per_s * dt_s, min(ramp_w_per_s * dt_s, requested_w - previous_w))
    return previous_w + delta


def clip_storage_power(
    requested_w: float,
    soc: float,
    soc_min: float,
    soc_max: float,
    energy_j: float,
    eta_charge: float,
    eta_discharge: float,
    dt_s: float,
    s_rated_va: float,
) -> float:
    """Положительная мощность — разряд в сеть. Энергия не создаётся сверх SoC."""
    requested_w = max(-s_rated_va, min(s_rated_va, requested_w))
    if dt_s < 0:
        raise ValueError("dt отрицателен")
    if requested_w >= 0:
        available_j = max(0.0, (soc - soc_min) * energy_j)
        max_w = available_j * eta_discharge / dt_s if dt_s > 0 else requested_w
        return min(requested_w, max_w)
    room_j = max(0.0, (soc_max - soc) * energy_j)
    max_charge_w = room_j / (eta_charge * dt_s) if dt_s > 0 else -requested_w
    return max(requested_w, -max_charge_w)


def update_soc(
    soc: float,
    executed_discharge_w: float,
    energy_j: float,
    eta_charge: float,
    eta_discharge: float,
    dt_s: float,
    soc_min: float,
    soc_max: float,
) -> float:
    """executed_discharge_w > 0 отдаёт энергию в сеть и уменьшает SoC.

    При разряде из батареи уходит P/η, при заряде в батарею входит |P|·η.
    """
    if energy_j <= 0:
        raise ValueError("энергия накопителя должна быть положительной")
    if executed_discharge_w >= 0:
        delta = executed_discharge_w * dt_s / (eta_discharge * energy_j)
        updated = soc - delta
    else:
        delta = (-executed_discharge_w) * dt_s * eta_charge / energy_j
        updated = soc + delta
    if updated < soc_min - 1e-9 or updated > soc_max + 1e-9:
        raise ValueError("исполненная мощность вывела SoC за предел: энергия не сходится")
    return min(soc_max, max(soc_min, updated))


def qualify_round_trip() -> dict[str, float | bool | str | list[str]]:
    """Круг разряд-заряд возвращает SoC. Из сети приходит больше, чем ушло."""
    energy_j = 1_000_000.0
    eta_charge = 0.9
    eta_discharge = 0.8
    dt_s = 10.0
    discharge_w = 1000.0
    soc0 = 0.5
    soc_after_discharge = update_soc(soc0, discharge_w, energy_j, eta_charge, eta_discharge, dt_s, 0.0, 1.0)
    left_j = discharge_w * dt_s / eta_discharge
    charge_w = left_j / (eta_charge * dt_s)
    soc_after_charge = update_soc(soc_after_discharge, -charge_w, energy_j, eta_charge, eta_discharge, dt_s, 0.0, 1.0)
    clipped = clip_storage_power(
        1.0e9, 0.2, 0.1, 1.0, energy_j, eta_charge, eta_discharge, dt_s, 1.0e9
    )
    available_w = (0.2 - 0.1) * energy_j * eta_discharge / dt_s
    reasons: list[str] = []
    if abs(soc_after_charge - soc0) > 1e-12:
        reasons.append("soc")
    if charge_w * dt_s <= discharge_w * dt_s:
        reasons.append("loss")
    if abs(clipped - available_w) > 1e-9:
        reasons.append("clip")
    try:
        update_soc(0.2, 1.0e9, energy_j, eta_charge, eta_discharge, dt_s, 0.1, 1.0)
        reasons.append("overflow")
    except ValueError:
        pass
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "charge_w": charge_w,
        "clipped_w": clipped,
    }


def cold_load_multiplier(elapsed_s: float, peak: float, tau_s: float) -> float:
    """P(t) / Pном = 1 + (k − 1) exp(−t / τ) после восстановления напряжения."""
    if tau_s <= 0 or peak < 1 or elapsed_s < 0:
        raise ValueError("некорректные параметры cold-load pickup")
    return 1.0 + (peak - 1.0) * math.exp(-elapsed_s / tau_s)
