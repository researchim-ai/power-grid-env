"""Минимальный EMT: аналитические RL/RLC и отдельная коммутационная модель.

Усреднённая модель и switching-модель объявлены раздельно. Это не универсальный
EMT-симулятор и не ко-симуляция HELICS.
"""

from __future__ import annotations

import math

import numpy as np


def rl_exact(voltage: float, resistance: float, inductance: float, time_s: float) -> float:
    if resistance <= 0 or inductance <= 0:
        raise ValueError("R и L должны быть положительными")
    return voltage / resistance * (1.0 - math.exp(-time_s * resistance / inductance))


def rl_trapezoidal(voltage: float, resistance: float, inductance: float, dt_s: float, steps: int, i0: float = 0.0) -> np.ndarray:
    current = np.zeros(steps + 1)
    current[0] = i0
    factor = 1.0 + dt_s * resistance / (2.0 * inductance)
    for index in range(steps):
        previous_derivative = (voltage - resistance * current[index]) / inductance
        # i1 = i0 + dt/2 * (f0 + (v - R i1)/L)
        current[index + 1] = (current[index] + 0.5 * dt_s * previous_derivative + 0.5 * dt_s * voltage / inductance) / factor
    return current


def rlc_exact_underdamped(voltage: float, resistance: float, inductance: float, capacitance: float, time_s: np.ndarray) -> np.ndarray:
    omega0 = 1.0 / math.sqrt(inductance * capacitance)
    alpha = resistance / (2.0 * inductance)
    if alpha >= omega0:
        raise ValueError("формула только для колебательного режима")
    omega_d = math.sqrt(omega0**2 - alpha**2)
    # i(0)=0, vc(0)=0, скачок напряжения. i(t) = (V/ωd L) e^{-αt} sin(ωd t)
    return (voltage / (omega_d * inductance)) * np.exp(-alpha * time_s) * np.sin(omega_d * time_s)


def rlc_trapezoidal(voltage: float, resistance: float, inductance: float, capacitance: float, dt_s: float, steps: int) -> np.ndarray:
    """Состояния [i, v_c]. Неявная трапеция линейной схемы."""
    state = np.zeros(2)
    current = np.zeros(steps + 1)
    conductance = np.array(
        [
            [-resistance / inductance, -1.0 / inductance],
            [1.0 / capacitance, 0.0],
        ]
    )
    source = np.array([voltage / inductance, 0.0])
    identity = np.eye(2)
    matrix = identity - 0.5 * dt_s * conductance
    for index in range(steps):
        rhs = state + 0.5 * dt_s * (conductance @ state + 2 * source)
        state = np.linalg.solve(matrix, rhs)
        current[index + 1] = state[0]
    return current


def averaged_buck_current(duty: float, vdc: float, resistance: float) -> float:
    return duty * vdc / resistance


def switching_buck_average(
    duty: float,
    vdc: float,
    resistance: float,
    inductance: float,
    dt_s: float,
    cycles: int,
    switching_hz: float,
) -> float:
    """Идеальный полумост: v = Vdc в течение duty, иначе 0. Возвращает средний ток последних 20%."""
    period = 1.0 / switching_hz
    steps_per_period = max(2, int(round(period / dt_s)))
    total = cycles * steps_per_period
    current = 0.0
    samples = []
    for index in range(total):
        phase = (index % steps_per_period) / steps_per_period
        voltage = vdc if phase < duty else 0.0
        # обратный Эйлер, устойчивый для жёсткой RL
        current = (current + dt_s * voltage / inductance) / (1.0 + dt_s * resistance / inductance)
        samples.append(current)
    tail = samples[int(0.8 * len(samples)) :]
    return float(sum(tail) / len(tail))


def cosim_rl_energy(
    vrms: float,
    resistance: float,
    inductance: float,
    frequency_hz: float,
    dt_emt: float,
    dt_couple: float,
    duration_s: float,
) -> dict[str, float]:
    """Фазор обновляется раз в dt_couple и на шаг запаздывает. EMT интегрирует v(t)."""
    omega = 2.0 * math.pi * frequency_hz
    steps = int(round(duration_s / dt_emt))
    current = 0.0
    applied_rms = vrms
    pending_rms = vrms
    energy_in = 0.0
    energy_r = 0.0
    last_couple = 0.0
    for index in range(steps):
        time_s = index * dt_emt
        if time_s - last_couple >= dt_couple - 1e-15:
            applied_rms = pending_rms
            pending_rms = vrms
            last_couple = time_s
        voltage = math.sqrt(2.0) * applied_rms * math.sin(omega * time_s)
        next_current = (current + dt_emt * voltage / inductance) / (1.0 + dt_emt * resistance / inductance)
        energy_in += voltage * 0.5 * (current + next_current) * dt_emt
        energy_r += resistance * (0.5 * (current + next_current)) ** 2 * dt_emt
        current = next_current
    magnetic = 0.5 * inductance * current**2
    return {
        "energy_in_j": energy_in,
        "energy_loss_j": energy_r,
        "magnetic_j": magnetic,
        "imbalance_j": energy_in - energy_r - magnetic,
    }
