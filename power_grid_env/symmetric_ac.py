"""Симметричный AC power flow Ньютона.

Только для сети, которая уже задана как однолинейный эквивалент прямой
последовательности. Несимметричный фидер этим модулем не заменяется.
"""

from __future__ import annotations

import numpy as np


def injections(y_bus: np.ndarray, voltage: np.ndarray, angle: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    phasor = voltage * np.exp(1j * angle)
    current = y_bus @ phasor
    power = phasor * np.conjugate(current)
    return power.real, power.imag


def newton_power_flow(
    y_bus: np.ndarray,
    p_spec: np.ndarray,
    q_spec: np.ndarray,
    *,
    slack: int = 0,
    v_slack: float = 1.0,
    tolerance: float = 1e-8,
    max_iterations: int = 40,
) -> dict[str, object]:
    """PQ-узлы кроме опоры. p_spec и q_spec — инъекции, нагрузка отрицательна.

    Допуск относительный: невязка делится на наибольшую заданную мощность.
    """
    size = len(p_spec)
    voltage = np.full(size, v_slack)
    angle = np.zeros(size)
    voltage[slack] = v_slack
    unknowns = [index for index in range(size) if index != slack]
    scale = max(1.0, float(np.max(np.abs(p_spec))), float(np.max(np.abs(q_spec))))
    converged = False
    iterations = 0
    mismatch = np.zeros(2 * len(unknowns))
    for iterations in range(1, max_iterations + 1):
        active, reactive = injections(y_bus, voltage, angle)
        mismatch = np.concatenate(
            [
                np.array([p_spec[index] - active[index] for index in unknowns]),
                np.array([q_spec[index] - reactive[index] for index in unknowns]),
            ]
        )
        if np.max(np.abs(mismatch)) <= tolerance * scale:
            converged = True
            break
        jacobian = _jacobian(y_bus, voltage, angle, unknowns)
        step = np.linalg.solve(jacobian, mismatch)
        count = len(unknowns)
        for position, index in enumerate(unknowns):
            angle[index] += step[position]
            voltage[index] += step[count + position]
    if not converged:
        active, reactive = injections(y_bus, voltage, angle)
        mismatch = np.concatenate(
            [
                np.array([p_spec[index] - active[index] for index in unknowns]),
                np.array([q_spec[index] - reactive[index] for index in unknowns]),
            ]
        )
        converged = bool(np.max(np.abs(mismatch)) <= tolerance * scale)
    return {
        "converged": converged,
        "iterations": iterations,
        "voltage": voltage,
        "angle": angle,
        "mismatch": float(np.max(np.abs(mismatch))),
    }


def _jacobian(y_bus: np.ndarray, voltage: np.ndarray, angle: np.ndarray, unknowns: list[int]) -> np.ndarray:
    count = len(unknowns)
    matrix = np.zeros((2 * count, 2 * count))
    base_p, base_q = injections(y_bus, voltage, angle)
    for column, index in enumerate(unknowns):
        angle_step = 1e-6
        bumped = angle.copy()
        bumped[index] += angle_step
        active, reactive = injections(y_bus, voltage, bumped)
        for row, bus in enumerate(unknowns):
            matrix[row, column] = (active[bus] - base_p[bus]) / angle_step
            matrix[count + row, column] = (reactive[bus] - base_q[bus]) / angle_step
        voltage_step = max(abs(voltage[index]), 1.0) * 1e-6
        bumped_v = voltage.copy()
        bumped_v[index] += voltage_step
        active, reactive = injections(y_bus, bumped_v, angle)
        for row, bus in enumerate(unknowns):
            matrix[row, count + column] = (active[bus] - base_p[bus]) / voltage_step
            matrix[count + row, count + column] = (reactive[bus] - base_q[bus]) / voltage_step
    return matrix


def series_ybus(impedance: complex) -> np.ndarray:
    admittance = 1.0 / impedance
    return np.array([[admittance, -admittance], [-admittance, admittance]], dtype=np.complex128)
