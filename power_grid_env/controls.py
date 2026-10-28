"""Дискретные законы регулятора и явные тепловые формулы.

РПН двигает одну ступень и не выходит за предел. Конденсатор имеет гистерезис.
Тепло считается отдельно от стационарного расчёта: R(T) туда не подставляется.
"""

from __future__ import annotations

import math


def ltc_next_tap(
    measured_pu: float,
    *,
    target_pu: float,
    deadband_pu: float,
    tap_pu: float,
    step_pu: float,
    tap_min_pu: float,
    tap_max_pu: float,
) -> float:
    if step_pu <= 0 or tap_max_pu < tap_min_pu:
        raise ValueError("ступень РПН и пределы заданы неверно")
    if abs(measured_pu - target_pu) <= deadband_pu:
        return tap_pu
    step = step_pu if measured_pu < target_pu else -step_pu
    return min(tap_max_pu, max(tap_min_pu, tap_pu + step))


def capacitor_next_steps(
    voltage_pu: float,
    steps_in: int,
    steps: int,
    *,
    close_below_pu: float,
    open_above_pu: float,
) -> int:
    if steps < 1 or not 0 <= steps_in <= steps:
        raise ValueError("положение батареи вне числа ступеней")
    if open_above_pu <= close_below_pu:
        raise ValueError("гистерезис конденсатора вырожден")
    if voltage_pu < close_below_pu and steps_in < steps:
        return steps_in + 1
    if voltage_pu > open_above_pu and steps_in > 0:
        return steps_in - 1
    return steps_in


def temperature_constant_r(
    time_s: float,
    temperature_c: float,
    ambient_c: float,
    current_a: float,
    resistance_ohm: float,
    thermal_ohm: float,
    thermal_joule_per_c: float,
) -> float:
    """T(t) при постоянном R. T∞ = Tокр + I² R Rθ, τ = Rθ Cθ."""
    if time_s < 0 or thermal_ohm <= 0 or thermal_joule_per_c <= 0 or resistance_ohm < 0:
        raise ValueError("тепловые параметры или время заданы неверно")
    rise = current_a**2 * resistance_ohm * thermal_ohm
    tau = thermal_ohm * thermal_joule_per_c
    return ambient_c + rise + (temperature_c - ambient_c - rise) * math.exp(-time_s / tau)


def steady_temperature(
    current_a: float,
    resistance_ref_ohm: float,
    alpha_per_c: float,
    reference_c: float,
    ambient_c: float,
    thermal_ohm: float,
) -> float:
    """Стационар I² R(T) = (T − Tокр) / Rθ. При тепловом разгоне решения нет."""
    if thermal_ohm <= 0 or resistance_ref_ohm < 0:
        raise ValueError("тепловое сопротивление или R заданы неверно")
    gain = current_a**2 * resistance_ref_ohm * thermal_ohm
    denominator = 1.0 - gain * alpha_per_c
    if denominator <= 0:
        raise ValueError("тепловой разгон: стационарной температуры нет")
    return (gain * (1.0 - alpha_per_c * reference_c) + ambient_c) / denominator


def integrate_temperature(
    time_s: float,
    step_s: float,
    temperature_c: float,
    ambient_c: float,
    current_a: float,
    resistance_ref_ohm: float,
    alpha_per_c: float,
    reference_c: float,
    thermal_ohm: float,
    thermal_joule_per_c: float,
) -> float:
    """Неявный Эйлер. При α=0 сходится к закрытой формуле с порядком шага."""
    if step_s <= 0 or thermal_joule_per_c <= 0:
        raise ValueError("шаг и теплоёмкость должны быть положительными")
    temperature = temperature_c
    heating = current_a**2 * resistance_ref_ohm
    elapsed = 0.0
    while elapsed + 1e-12 < time_s:
        increment = min(step_s, time_s - elapsed)
        scale = increment / thermal_joule_per_c
        coefficient = 1.0 - scale * heating * alpha_per_c + scale / thermal_ohm
        if coefficient <= 0:
            raise ValueError("шаг тепла неустойчив")
        right = temperature + scale * heating * (1.0 - alpha_per_c * reference_c) + scale * ambient_c / thermal_ohm
        temperature = right / coefficient
        elapsed += increment
    return temperature


def series_rlc_impedance(resistance: float, inductance: float, capacitance: float, frequency_hz: float) -> complex:
    if inductance <= 0 or capacitance <= 0 or frequency_hz <= 0:
        raise ValueError("L, C и частота резонанса должны быть положительными")
    omega = 2.0 * math.pi * frequency_hz
    return resistance + 1j * (omega * inductance - 1.0 / (omega * capacitance))


def resonance_hz(inductance: float, capacitance: float) -> float:
    if inductance <= 0 or capacitance <= 0:
        raise ValueError("L и C должны быть положительными")
    return 1.0 / (2.0 * math.pi * math.sqrt(inductance * capacitance))


def triplen_neutral(phase_current: complex) -> complex:
    """Три синфазных тока тройной гармоники складываются в нейтрали."""
    return 3.0 * phase_current
