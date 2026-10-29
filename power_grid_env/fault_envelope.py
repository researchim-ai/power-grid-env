"""Огибающая тока КЗ в цепи RL. Это не полный электромагнитный переходный процесс.

Мгновенный ток при включении синусоиды на нулевом токе:
i(t) = Im sin(ωt − φ) + Im sin(φ) exp(−t/τ),
φ = arctg(X/R), τ = X/(ωR), Im = √2·|E|/|Z|.
Неявная трапеция интегрирует ту же цепь отдельно от формулы.
"""

from __future__ import annotations

import math

import numpy as np


def closed_form(time_s: float, voltage_rms: float, resistance: float, reactance: float, frequency_hz: float) -> float:
    if time_s < 0 or resistance <= 0 or reactance <= 0 or frequency_hz <= 0 or voltage_rms <= 0:
        raise ValueError("время, R, X, частота и напряжение должны быть положительными")
    omega = 2.0 * math.pi * frequency_hz
    impedance = math.hypot(resistance, reactance)
    phi = math.atan2(reactance, resistance)
    peak = math.sqrt(2.0) * voltage_rms / impedance
    tau = reactance / (omega * resistance)
    alternating = peak * math.sin(omega * time_s - phi)
    offset = peak * math.sin(phi) * math.exp(-time_s / tau)
    return alternating + offset


def waveform_kappa(
    resistance: float,
    reactance: float,
    *,
    frequency_hz: float = 50.0,
    voltage_rms: float = 1.0,
    samples: int = 20001,
) -> float:
    """Максимум огибающей за период, делённый на √2 I. Это сверка с κ, не коэффициент m."""
    period = 1.0 / frequency_hz
    times = np.linspace(0.0, period, samples)
    peak = max(abs(closed_form(float(time_s), voltage_rms, resistance, reactance, frequency_hz)) for time_s in times)
    alternating = math.sqrt(2.0) * voltage_rms / math.hypot(resistance, reactance)
    return peak / alternating


def stepped_current(
    time_s: float,
    switch_s: float,
    first_rms: float,
    second_rms: float,
    resistance: float,
    reactance: float,
    frequency_hz: float,
) -> float:
    """Ток RL при скачке действующего напряжения. В момент скачка ток непрерывен."""
    if switch_s < 0 or time_s < 0 or resistance <= 0 or reactance <= 0 or frequency_hz <= 0:
        raise ValueError("время, R, X и частота должны быть согласованы")
    if time_s <= switch_s:
        return closed_form(time_s, first_rms, resistance, reactance, frequency_hz)
    carried = closed_form(switch_s, first_rms, resistance, reactance, frequency_hz)
    omega = 2.0 * math.pi * frequency_hz
    impedance = math.hypot(resistance, reactance)
    phi = math.atan2(reactance, resistance)
    peak = math.sqrt(2.0) * second_rms / impedance
    tau = reactance / (omega * resistance)
    particular = peak * math.sin(omega * time_s - phi)
    offset = carried - peak * math.sin(omega * switch_s - phi)
    return particular + offset * math.exp(-(time_s - switch_s) / tau)


def steady_current(time_s: float, voltage_rms: float, resistance: float, reactance: float, frequency_hz: float) -> float:
    """Периодический ток без постоянной. Это действующий режим, не включение с нуля."""
    if time_s < 0 or resistance <= 0 or reactance <= 0 or frequency_hz <= 0 or voltage_rms <= 0:
        raise ValueError("время, R, X, частота и напряжение должны быть положительными")
    omega = 2.0 * math.pi * frequency_hz
    phi = math.atan2(reactance, resistance)
    peak = math.sqrt(2.0) * voltage_rms / math.hypot(resistance, reactance)
    return peak * math.sin(omega * time_s - phi)


def trapezoid(
    time_s: float,
    step_s: float,
    voltage_rms: float,
    resistance: float,
    reactance: float,
    frequency_hz: float,
    current0: float = 0.0,
) -> float:
    """Скалярная неявная трапеция di/dt = (√2 E sin(ωt) − R i) / L."""
    if step_s <= 0:
        raise ValueError("шаг должен быть положительным")
    omega = 2.0 * math.pi * frequency_hz
    inductance = reactance / omega
    current = current0
    elapsed = 0.0

    def voltage(stamp: float) -> float:
        return math.sqrt(2.0) * voltage_rms * math.sin(omega * stamp)

    def slope(stamp: float, value: float) -> float:
        return (voltage(stamp) - resistance * value) / inductance

    while elapsed + 1e-15 < time_s:
        step = min(step_s, time_s - elapsed)
        previous = slope(elapsed, current)
        # i1 = i0 + h/2 (f0 + (v1 - R i1)/L)
        upcoming_voltage = voltage(elapsed + step)
        factor = 1.0 + 0.5 * step * resistance / inductance
        current = (current + 0.5 * step * previous + 0.5 * step * upcoming_voltage / inductance) / factor
        elapsed += step
    return current


def stepped_trapezoid(
    time_s: float,
    step_s: float,
    switch_s: float,
    first_rms: float,
    second_rms: float,
    resistance: float,
    reactance: float,
    frequency_hz: float,
) -> float:
    """Та же трапеция, напряжение меняется в switch_s и не разрывает ток."""
    if step_s <= 0:
        raise ValueError("шаг должен быть положительным")
    omega = 2.0 * math.pi * frequency_hz
    inductance = reactance / omega
    current = 0.0
    elapsed = 0.0

    def voltage(stamp: float) -> float:
        amplitude = first_rms if stamp < switch_s else second_rms
        return math.sqrt(2.0) * amplitude * math.sin(omega * stamp)

    def slope(stamp: float, value: float) -> float:
        return (voltage(stamp) - resistance * value) / inductance

    while elapsed + 1e-15 < time_s:
        step = min(step_s, time_s - elapsed)
        if elapsed < switch_s < elapsed + step:
            step = switch_s - elapsed
        previous = slope(elapsed, current)
        upcoming_voltage = voltage(elapsed + step)
        factor = 1.0 + 0.5 * step * resistance / inductance
        current = (current + 0.5 * step * previous + 0.5 * step * upcoming_voltage / inductance) / factor
        elapsed += step
    return current


def boundary_midpoint_error(step_s: float, frequency_hz: float, mode: str) -> float:
    """Ошибка середины шага: удержание левого отсчёта или прямая между концами."""
    if step_s <= 0 or frequency_hz <= 0 or mode not in {"hold", "linear"}:
        raise ValueError("шаг, частота и способ передачи границы должны быть согласованы")
    omega = 2.0 * math.pi * frequency_hz
    count = int(round(1.0 / (frequency_hz * step_s)))
    if count < 8:
        raise ValueError("шаг границы слишком крупный для периода")
    worst = 0.0
    for index in range(count):
        left = index * step_s
        right = left + step_s
        truth = math.sin(omega * (left + right) / 2.0)
        if mode == "hold":
            sample = math.sin(omega * left)
        else:
            sample = 0.5 * (math.sin(omega * left) + math.sin(omega * right))
        worst = max(worst, abs(sample - truth))
    return worst


def energy_imbalance(
    duration_s: float,
    step_s: float,
    voltage_rms: float,
    resistance: float,
    reactance: float,
    frequency_hz: float,
) -> float:
    """Вход минус тепло минус прирост магнитной энергии. Старт взят из периодического тока."""
    if duration_s <= 0 or step_s <= 0:
        raise ValueError("окно и шаг энергии должны быть положительными")
    omega = 2.0 * math.pi * frequency_hz
    inductance = reactance / omega
    current = steady_current(0.0, voltage_rms, resistance, reactance, frequency_hz)
    entered = 0.0
    lost = 0.0
    elapsed = 0.0
    while elapsed + 1e-15 < duration_s:
        step = min(step_s, duration_s - elapsed)
        voltage_left = math.sqrt(2.0) * voltage_rms * math.sin(omega * elapsed)
        voltage_right = math.sqrt(2.0) * voltage_rms * math.sin(omega * (elapsed + step))
        nxt = _rl_step(current, elapsed, step, voltage_rms, resistance, reactance, frequency_hz)
        average_current = 0.5 * (current + nxt)
        average_voltage = 0.5 * (voltage_left + voltage_right)
        entered += average_voltage * average_current * step
        lost += resistance * average_current**2 * step
        current = nxt
        elapsed += step
    magnetic = 0.5 * inductance * (current**2 - steady_current(0.0, voltage_rms, resistance, reactance, frequency_hz) ** 2)
    return entered - lost - magnetic


def accept_or_restore(current: float, time_s: float, step_s: float, voltage_rms: float, resistance: float, reactance: float, frequency_hz: float, *, accept: bool) -> tuple[float, bool]:
    """Отказ шага возвращает прежний ток. Принятый шаг совпадает с трапецией."""
    proposed = _rl_step(current, time_s, step_s, voltage_rms, resistance, reactance, frequency_hz)
    if not accept or not math.isfinite(proposed):
        return current, False
    return proposed, True


def _rl_step(current: float, time_s: float, step_s: float, voltage_rms: float, resistance: float, reactance: float, frequency_hz: float) -> float:
    omega = 2.0 * math.pi * frequency_hz
    inductance = reactance / omega
    voltage_left = math.sqrt(2.0) * voltage_rms * math.sin(omega * time_s)
    voltage_right = math.sqrt(2.0) * voltage_rms * math.sin(omega * (time_s + step_s))
    previous = (voltage_left - resistance * current) / inductance
    factor = 1.0 + 0.5 * step_s * resistance / inductance
    return (current + 0.5 * step_s * previous + 0.5 * step_s * voltage_right / inductance) / factor


def thermal_rms(duration_s: float, voltage_rms: float, resistance: float, reactance: float, frequency_hz: float) -> float:
    """Корень из среднего квадрата тока за duration_s. Переменный член не затухает.

    Это интеграл уже проверенной огибающей, не коэффициент m из IEC 60909.
    """
    if duration_s <= 0:
        raise ValueError("окно теплового тока должно быть положительным")
    omega = 2.0 * math.pi * frequency_hz
    impedance = math.hypot(resistance, reactance)
    phi = math.atan2(reactance, resistance)
    peak = math.sqrt(2.0) * voltage_rms / impedance
    gamma = omega * resistance / reactance
    alternating = peak
    offset = peak * math.sin(phi)
    sine_integral = (
        duration_s / 2.0
        - math.sin(2.0 * omega * duration_s - 2.0 * phi) / (4.0 * omega)
        + math.sin(-2.0 * phi) / (4.0 * omega)
    )
    exponential_integral = (1.0 - math.exp(-2.0 * gamma * duration_s)) / (2.0 * gamma)
    decay = -gamma
    denominator = decay * decay + omega * omega

    def antiderivative(stamp: float) -> float:
        return math.exp(decay * stamp) * (
            decay * math.sin(omega * stamp - phi) - omega * math.cos(omega * stamp - phi)
        ) / denominator

    cross = 2.0 * alternating * offset * (antiderivative(duration_s) - antiderivative(0.0))
    mean_square = (alternating * alternating * sine_integral + offset * offset * exponential_integral + cross) / duration_s
    if mean_square < 0 and mean_square > -1e-8:
        mean_square = 0.0
    return math.sqrt(mean_square)


def fuse_i2t(duration_s: float, voltage_rms: float, resistance: float, reactance: float, frequency_hz: float) -> float:
    """Интеграл i²dt за окно. Плавкая вставка сравнивается с этим числом, не с мгновенным током."""
    rms = thermal_rms(duration_s, voltage_rms, resistance, reactance, frequency_hz)
    return rms * rms * duration_s


def fuse_melts(i2t_a2_s: float, rating_a2_s: float) -> bool:
    if rating_a2_s <= 0:
        raise ValueError("номинал плавкой вставки должен быть положительным")
    return i2t_a2_s >= rating_a2_s


def step_ratio(voltage_rms: float = 5773.5, resistance: float = 0.35, reactance: float = 0.8) -> float:
    """Во сколько раз ошибка на 0.05 с меньше при шаге вдвое. Для порядка 2 ожидается около 4."""
    target = 0.05
    exact = closed_form(target, voltage_rms, resistance, reactance, 50.0)
    coarse = trapezoid(target, 2.0e-4, voltage_rms, resistance, reactance, 50.0)
    fine = trapezoid(target, 1.0e-4, voltage_rms, resistance, reactance, 50.0)
    return abs(coarse - exact) / max(1e-15, abs(fine - exact))
