"""Частотная схема последовательного RLC.

Формула импеданса и узловая матрица записаны раздельно. Спектр сюда приходит
уже объявленным: функция не подставляет типовой спектр завода.
"""

from __future__ import annotations

import math

import numpy as np

from power_grid_env.controls import resonance_hz, series_rlc_impedance


def fourier_fundamental(samples: list[float] | np.ndarray, dt_s: float, frequency_hz: float) -> complex | None:
    """Полный период даёт действующий фазор основной. Короткое окно ещё не готово."""
    if dt_s <= 0 or frequency_hz <= 0:
        raise ValueError("шаг и частота фильтра должны быть положительными")
    count = int(round(1.0 / (frequency_hz * dt_s)))
    values = np.asarray(samples, dtype=float)
    if len(values) < count:
        return None
    window = values[-count:]
    angle = -2.0 * math.pi * np.arange(count) / count
    peak = 2.0 / count * np.sum(window * np.exp(1j * angle))
    return complex(peak / math.sqrt(2.0))


def bipolar_pwm_fundamental(modulation: float, dc_v: float, carrier_ratio: int, samples_per_period: int) -> float:
    """Амплитуда первой гармоники двухполярной ШИМ. Линейный закон — m·Vdc/2.

    Перемодуляция не считается по этому закону. Это спектр одного напряжения, не привод.
    """
    if not 0.0 <= modulation <= 1.0 or dc_v < 0 or carrier_ratio < 3 or samples_per_period < 16:
        raise ValueError("модуляция 0..1, несущая и число отсчётов должны быть достаточными")
    count = carrier_ratio * samples_per_period
    time = np.linspace(0.0, 1.0 / 50.0, count, endpoint=False)
    reference = modulation * np.sin(2.0 * math.pi * 50.0 * time)
    phase = np.mod(time * carrier_ratio * 50.0, 1.0)
    triangle = 4.0 * np.abs(phase - 0.5) - 1.0
    wave = np.where(reference >= triangle, 0.5 * dc_v, -0.5 * dc_v)
    spectrum = np.fft.rfft(wave) / count
    return float(2.0 * abs(spectrum[1]))


def six_step_orders(maximum: int) -> list[tuple[int, float]]:
    """Амплитуды шестиступенчатого спектра: порядки 6k±1, ток 1/n. Это не ШИМ."""
    if maximum < 1:
        raise ValueError("верхний порядок спектра должен быть не меньше 1")
    orders = [(1, 1.0)]
    order = 5
    while order <= maximum:
        if order % 6 in {1, 5}:
            orders.append((order, 1.0 / order))
        order += 2
    return orders


def six_step_thd() -> float:
    """Замкнутое значение sqrt(π²/9 − 1) для бесконечного шестиступенчатого ряда."""
    return math.sqrt(math.pi**2 / 9.0 - 1.0)


def thd_fundamental(orders: list[tuple[int, float]]) -> float:
    """THD относительно первой гармоники. Знаменатель — ток порядка 1, не RMS всего спектра."""
    currents = {order: amp for order, amp in orders}
    if any(order < 1 or amp < 0 for order, amp in orders):
        raise ValueError("порядок гармоники не меньше 1, ток неотрицателен")
    fundamental = currents.get(1, 0.0)
    if fundamental <= 0.0:
        raise ValueError("THD требует ненулевой ток первой гармоники")
    rest = math.sqrt(sum(amp * amp for order, amp in currents.items() if order >= 2))
    return rest / fundamental


def thd_rms(orders: list[tuple[int, float]]) -> float:
    """Тот же числитель, знаменатель — RMS всего объявленного спектра."""
    if any(order < 1 or amp < 0 for order, amp in orders):
        raise ValueError("порядок гармоники не меньше 1, ток неотрицателен")
    total = math.sqrt(sum(amp * amp for _order, amp in orders))
    if total <= 0.0:
        raise ValueError("спектр не содержит тока")
    rest = math.sqrt(sum(amp * amp for order, amp in orders if order >= 2))
    return rest / total


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
