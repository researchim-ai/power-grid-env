"""Однофазное звено RLC: источник, последовательная ветвь, ёмкость и нагрузка.

Фазор узла записан отдельно от неявной трапеции. Это не EMT всей сети.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from power_grid_env.harmonic import fourier_fundamental


def phasor_node(voltage_rms: float, resistance: float, inductance: float, capacitance: float, load_ohm: float, frequency_hz: float) -> complex:
    """Действующий фазор напряжения узла. Источник имеет нулевой угол и синусную запись."""
    _check(voltage_rms, resistance, inductance, capacitance, load_ohm, frequency_hz)
    omega = 2.0 * math.pi * frequency_hz
    series = resistance + 1.0j * omega * inductance
    shunt = 1.0 / load_ohm + 1.0j * omega * capacitance
    return voltage_rms / (1.0 + series * shunt)


def emulate(
    voltage_rms: float,
    resistance: float,
    inductance: float,
    capacitance: float,
    load_ohm: float,
    frequency_hz: float,
    step_s: float,
    duration_s: float,
) -> dict[str, np.ndarray]:
    """Состояния [ток ветви, напряжение узла]. Напряжение источника √2 E sin(ωt)."""
    _check(voltage_rms, resistance, inductance, capacitance, load_ohm, frequency_hz)
    if step_s <= 0 or duration_s <= 0:
        raise ValueError("шаг и окно звена должны быть положительными")
    steps = int(round(duration_s / step_s))
    omega = 2.0 * math.pi * frequency_hz
    matrix = np.array(
        [
            [-resistance / inductance, -1.0 / inductance],
            [1.0 / capacitance, -1.0 / (load_ohm * capacitance)],
        ]
    )
    left = np.eye(2) - 0.5 * step_s * matrix
    state = np.zeros(2)
    current = np.zeros(steps + 1)
    voltage = np.zeros(steps + 1)
    entered = 0.0
    series_loss = 0.0
    load_loss = 0.0
    for index in range(steps):
        time_s = index * step_s
        source_left = math.sqrt(2.0) * voltage_rms * math.sin(omega * time_s)
        source_right = math.sqrt(2.0) * voltage_rms * math.sin(omega * (time_s + step_s))
        drive = np.array([0.5 * step_s * (source_left + source_right) / inductance, 0.0])
        rhs = state + 0.5 * step_s * (matrix @ state) + drive
        nxt = np.linalg.solve(left, rhs)
        average_current = 0.5 * (state[0] + nxt[0])
        average_voltage = 0.5 * (state[1] + nxt[1])
        entered += 0.5 * (source_left + source_right) * average_current * step_s
        series_loss += resistance * average_current**2 * step_s
        load_loss += average_voltage**2 / load_ohm * step_s
        state = nxt
        current[index + 1] = state[0]
        voltage[index + 1] = state[1]
    magnetic = 0.5 * inductance * state[0] ** 2
    electric = 0.5 * capacitance * state[1] ** 2
    return {
        "current": current,
        "voltage": voltage,
        "energy_imbalance_j": entered - series_loss - load_loss - magnetic - electric,
    }


def qualify_rlc_link() -> dict[str, Any]:
    """Последний период трапеции сходится к фазору узла. Это одно звено, не сеть завода."""
    voltage = 1000.0
    resistance = 1.0
    inductance = 0.02
    capacitance = 50.0e-6
    load = 40.0
    frequency = 50.0
    expected = phasor_node(voltage, resistance, inductance, capacitance, load, frequency)
    coarse = _fundamental(voltage, resistance, inductance, capacitance, load, frequency, 2.0e-4)
    fine = _fundamental(voltage, resistance, inductance, capacitance, load, frequency, 1.0e-4)
    coarse_error = abs(abs(coarse) - abs(expected)) / abs(expected)
    fine_error = abs(abs(fine) - abs(expected)) / abs(expected)
    energy = emulate(voltage, resistance, inductance, capacitance, load, frequency, 1.0e-4, 0.4)
    reasons = []
    if fine_error > 1e-4:
        reasons.append("voltage")
    if not fine_error < coarse_error or coarse_error / fine_error < 3.0 or coarse_error / fine_error > 5.0:
        reasons.append("step")
    if abs(float(energy["energy_imbalance_j"])) > 1e-6:
        reasons.append("energy")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "voltage_error": fine_error,
        "step_ratio": coarse_error / fine_error if fine_error else math.inf,
        "energy_imbalance_j": float(energy["energy_imbalance_j"]),
        "full_network_emt": False,
    }


def _fundamental(
    voltage: float,
    resistance: float,
    inductance: float,
    capacitance: float,
    load: float,
    frequency: float,
    step_s: float,
) -> complex:
    series = emulate(voltage, resistance, inductance, capacitance, load, frequency, step_s, 0.4)
    found = fourier_fundamental(series["voltage"], step_s, frequency)
    if found is None:
        raise RuntimeError("окно звена короче периода")
    return found


def _check(voltage: float, resistance: float, inductance: float, capacitance: float, load: float, frequency: float) -> None:
    if voltage <= 0 or resistance <= 0 or inductance <= 0 or capacitance <= 0 or load <= 0 or frequency <= 0:
        raise ValueError("напряжение, R, L, C, нагрузка и частота звена должны быть положительными")
