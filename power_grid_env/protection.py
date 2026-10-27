"""Упрощённые защиты для квазистационарного шага.

Характеристики IEC 60255-151:2009, таблица A.1 (кривые A/B/C):
стандартная инверсная k=0.14, α=0.02; очень инверсная k=13.5, α=1;
чрезвычайно инверсная k=80, α=2. Формула t = TMS · k / ((I/Is)^α − 1), c=0.
Это не модель быстродействующей релейной защиты и не distance/differential.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

IEC_CURVES = {
    "standard_inverse": (0.14, 0.02),
    "very_inverse": (13.5, 1.0),
    "extremely_inverse": (80.0, 2.0),
}


def iec_operate_time(current_a: float, pickup_a: float, curve: str, time_multiplier: float) -> float:
    if current_a <= pickup_a:
        return math.inf
    if curve == "definite":
        return time_multiplier
    try:
        k_factor, alpha = IEC_CURVES[curve]
    except KeyError as exc:
        raise ValueError(
            f"кривая {curve} не входит в реализованный набор IEC 60255-151 таблица A.1"
        ) from exc
    ratio = current_a / pickup_a
    return time_multiplier * k_factor / (ratio**alpha - 1.0)


def directional_allows(voltage: complex, current: complex, forward: bool = True) -> bool:
    """Грубая направленность: знак активной мощности в фазе напряжения.

    forward=True пропускает ток, отдающий положительную P из узла в линию,
    то есть Re(V · conj(I)) > 0, где I входит в контролируемый элемент.
    """
    if abs(voltage) < 1e-6 or abs(current) < 1e-9:
        return False
    power = (voltage * current.conjugate()).real
    return power > 0 if forward else power < 0


@dataclass
class RelayState:
    relay_id: str
    kind: Literal["overcurrent", "undervoltage", "overvoltage"]
    equipment_id: str
    pickup: float
    curve: str = "definite"
    time_multiplier_s: float = 1.0
    breaker_delay_s: float = 0.0
    reset_s: float = 1.0
    accumulator_s: float = 0.0
    timing: bool = False
    tripped: bool = False
    shots: int = 0
    max_shots: int | None = None
    reclose_delay_s: float | None = None
    lockout: bool = False
    pending_close_at: float | None = None
    directional: bool = False
    measured_equipment_id: str | None = None
    measured_node_id: str | None = None
    notes: list[str] = field(default_factory=list)

    def integrate(self, signal: float, dt_s: float, voltage: complex | None = None, current: complex | None = None) -> str | None:
        if self.directional and voltage is not None and current is not None:
            if not directional_allows(voltage, current, forward=True):
                signal = 0.0
        return advance_relay(self, signal, dt_s)


def linear_reset(accumulator_s: float, dt_s: float, reset_s: float) -> float:
    """Линейный сброс аккумулятора за reset_s. Это не кривая возврата IEC."""
    if reset_s <= 0:
        return 0.0
    return max(0.0, accumulator_s - dt_s / reset_s)


def advance_relay(state: RelayState, signal: float, dt_s: float) -> str | None:
    """Интегратор без двусмысленного сброса: ниже уставки аккумулятор спадает линейно."""
    if state.lockout:
        return None
    if state.kind == "overcurrent":
        active = signal > state.pickup
        operate = iec_operate_time(signal, state.pickup, state.curve, state.time_multiplier_s) if active else math.inf
    elif state.kind == "undervoltage":
        active = signal < state.pickup
        operate = state.time_multiplier_s
    else:
        active = signal > state.pickup
        operate = state.time_multiplier_s
    if not active:
        if state.reset_s <= 0:
            state.accumulator_s = 0.0
        else:
            span = operate if state.kind == "overcurrent" and state.curve != "definite" else state.time_multiplier_s
            # Линейный сброс полной выдержки за reset_s.
            full = 1.0 if state.kind == "overcurrent" and state.curve != "definite" else state.time_multiplier_s
            state.accumulator_s = max(0.0, state.accumulator_s - full * dt_s / state.reset_s)
        state.timing = False
        return None
    if state.kind == "overcurrent" and state.curve != "definite":
        state.accumulator_s += dt_s / operate
        state.timing = True
        if state.accumulator_s >= 1.0:
            state.accumulator_s = 0.0
            state.timing = False
            state.tripped = True
            state.shots += 1
            if state.max_shots is not None and state.shots >= state.max_shots:
                state.lockout = True
            return "trip"
        return None
    state.accumulator_s += dt_s
    state.timing = True
    if state.accumulator_s + 1e-12 >= state.time_multiplier_s + state.breaker_delay_s:
        state.accumulator_s = 0.0
        state.timing = False
        state.tripped = True
        state.shots += 1
        if state.max_shots is not None and state.shots >= state.max_shots:
            state.lockout = True
        return "trip"
    return None
