"""Узкий subset IEC 60909-0 для одного источника без двигателей.

c берётся из таблицы напряжения стандарта, а не из режима сети.
Есть I''k для трёх фаз, фазы на землю и двух фаз, пик через κ и Ib через μ.
Вклад асинхронного двигателя здесь — ток в глухое КЗ на его выводах, c·Un/(√3·ZM).
Он не равен току ветви в сети с падением напряжения. Нет сетки и теплового эквивалента.
"""

from __future__ import annotations

import math


def voltage_factor(nominal_ll_v: float, *, maximum: bool) -> float:
    """IEC 60909-0, таблица c. Выше 1 кВ: 1.10 / 1.00. До 1 кВ включительно: 1.05 / 0.95."""
    if nominal_ll_v <= 0:
        raise ValueError("номинальное напряжение для c должно быть положительным")
    if nominal_ll_v <= 1000.0:
        return 1.05 if maximum else 0.95
    return 1.10 if maximum else 1.00


def initial_symmetrical_three_phase(nominal_ll_v: float, c: float, impedance: complex) -> complex:
    """I''k = c Un / (sqrt(3) Zk). Zk — эквивалент прямой последовательности, Ом."""
    if c <= 0 or abs(impedance) == 0:
        raise ValueError("c и сопротивление КЗ должны быть ненулевыми")
    return c * nominal_ll_v / (math.sqrt(3.0) * impedance)


def initial_symmetrical_single_line_to_ground(
    nominal_ll_v: float,
    c: float,
    z1: complex,
    z2: complex,
    z0: complex,
) -> complex:
    """Фазный ток. I''k1 = c Un / (sqrt(3) (Z1+Z2+Z0)), Ia = 3 I''k1."""
    if c <= 0:
        raise ValueError("c должен быть положительным")
    loop = z1 + z2 + z0
    if abs(loop) == 0:
        raise ValueError("контур нулевой последовательности вырожден")
    return 3.0 * c * nominal_ll_v / (math.sqrt(3.0) * loop)


def initial_symmetrical_line_to_line(nominal_ll_v: float, c: float, z1: complex, z2: complex) -> complex:
    """I''k2 = c Un / (Z1 + Z2) при нулевом сопротивлении дуги.

    При Z1 = Z2 модуль равен (√3/2) трёхфазного тока.
    """
    if c <= 0:
        raise ValueError("c должен быть положительным")
    loop = z1 + z2
    if abs(loop) == 0:
        raise ValueError("контур междуфазного КЗ вырожден")
    return c * nominal_ll_v / loop


def induction_motor_terminal_current(nominal_ll_v: float, c: float, locked_rotor: complex) -> complex:
    """Ток двигателя в глухое КЗ на его выводах: c·Un / (√3·ZM).

    ZM — сопротивление заторможенного ротора. Это не ток ветви в сети,
    где напряжение на выводах уже просело.
    """
    if c <= 0 or nominal_ll_v <= 0 or abs(locked_rotor) == 0:
        raise ValueError("c, напряжение и ZM двигателя должны быть ненулевыми")
    return c * nominal_ll_v / (math.sqrt(3.0) * locked_rotor)


def peak_factor(resistance_over_reactance: float) -> float:
    """κ = 1.02 + 0.98 exp(−3 R/X). Применимо при R/X от 0 до больших значений, не для сетки."""
    if resistance_over_reactance < 0:
        raise ValueError("R/X не может быть отрицательным")
    return 1.02 + 0.98 * math.exp(-3.0 * resistance_over_reactance)


def peak_current(initial_symmetrical: complex, kappa: float) -> float:
    if kappa <= 0:
        raise ValueError("κ должен быть положительным")
    return kappa * math.sqrt(2.0) * abs(initial_symmetrical)


# IEC 60909-0, коэффициент затухания периодической составляющей синхронной машины.
# Другие выдержки не интерполируются.
_AC_DECREMENT = {
    0.02: (0.84, 0.26, 0.26),
    0.05: (0.71, 0.51, 0.30),
    0.10: (0.62, 0.72, 0.32),
    0.25: (0.56, 0.94, 0.38),
}


def ac_decrement_factor(short_circuit_over_rated: float, minimum_time_s: float) -> float:
    """μ для Ib синхронной машины. Результат не больше 1."""
    if short_circuit_over_rated <= 0:
        raise ValueError("отношение I''k/Ir должно быть положительным")
    if minimum_time_s < 0.25 and minimum_time_s not in _AC_DECREMENT:
        raise ValueError("tmin допускается 0.02, 0.05, 0.10 с или не меньше 0.25 с")
    key = 0.25 if minimum_time_s >= 0.25 else minimum_time_s
    offset, amplitude, decay = _AC_DECREMENT[key]
    return min(1.0, offset + amplitude * math.exp(-decay * short_circuit_over_rated))


def breaking_current(
    initial_symmetrical: complex,
    *,
    near_synchronous: bool,
    short_circuit_over_rated: float | None = None,
    minimum_time_s: float = 0.10,
) -> tuple[complex, float]:
    """Ib и μ. Вдали от синхронной машины μ = 1 и Ib = I''k."""
    if not near_synchronous:
        return initial_symmetrical, 1.0
    if short_circuit_over_rated is None:
        raise ValueError("для ближней синхронной машины нужно I''k/Ir")
    factor = ac_decrement_factor(short_circuit_over_rated, minimum_time_s)
    return factor * initial_symmetrical, factor
