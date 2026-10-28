"""Частотная схема последовательного RLC.

Формула импеданса и узловая матрица записаны раздельно. Спектр сюда приходит
уже объявленным: функция не подставляет типовой спектр завода.
"""

from __future__ import annotations

import math

import numpy as np

from power_grid_env.controls import resonance_hz, series_rlc_impedance


def forced_current(voltage: complex, resistance: float, inductance: float, capacitance: float, frequency_hz: float) -> complex:
    return voltage / series_rlc_impedance(resistance, inductance, capacitance, frequency_hz)


def nodal_current(voltage: complex, resistance: float, inductance: float, capacitance: float, frequency_hz: float) -> complex:
    """Два узла: источник, затем R, L и C на землю. Ток источника — неизвестное не требуется."""
    if frequency_hz <= 0 or inductance <= 0 or capacitance <= 0:
        raise ValueError("частота, L и C должны быть положительными")
    omega = 2.0 * math.pi * frequency_hz
    z_l = 1j * omega * inductance
    y_c = 1j * omega * capacitance
    y_r = 1.0 / resistance if resistance != 0 else None
    if y_r is None:
        raise ValueError("последовательное R равно нулю, узел не выделен")
    # Узлы: после R (1) и после L (2). Источник известен.
    admit = np.zeros((2, 2), dtype=np.complex128)
    inject = np.zeros(2, dtype=np.complex128)
    admit[0, 0] += y_r + 1.0 / z_l
    admit[0, 1] -= 1.0 / z_l
    admit[1, 0] -= 1.0 / z_l
    admit[1, 1] += 1.0 / z_l + y_c
    inject[0] += voltage * y_r
    solved = np.linalg.solve(admit, inject)
    return (voltage - solved[0]) * y_r


def resonance_moves_with_capacitance(inductance: float, capacitance: float) -> float:
    """Удвоение C снижает частоту в sqrt(2). Это проверка чувствительности, не подгонка."""
    base = resonance_hz(inductance, capacitance)
    doubled = resonance_hz(inductance, 2.0 * capacitance)
    return base / doubled
