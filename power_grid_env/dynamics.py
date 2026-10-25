"""Классическая модель синхронной машины, неявная трапеция.

Область: одна машина — шины бесконечной мощности. Это не общий RMS-backend
и не замена ANDES. Инициализация согласована с балансом мощности.
"""

from __future__ import annotations

import math

import numpy as np


def equilibrium(pm: float, e_pu: float, v_pu: float, x_pu: float, omega_s: float) -> tuple[float, float]:
    argument = pm * x_pu / (e_pu * v_pu)
    if abs(argument) > 1:
        raise ValueError("нет равновесия: Pm выше предела передачи")
    return math.asin(argument), omega_s


def electrical_power(delta: float, e_pu: float, v_pu: float, x_pu: float) -> float:
    return e_pu * v_pu / x_pu * math.sin(delta)


def derivative(state: np.ndarray, pm: float, e_pu: float, v_pu: float, x_pu: float, inertia_s: float, damping: float, omega_s: float) -> np.ndarray:
    delta, omega = state
    pe = electrical_power(float(delta), e_pu, v_pu, x_pu)
    domega = (omega_s / (2.0 * inertia_s)) * (pm - pe - damping * (omega - omega_s) / omega_s)
    return np.array([omega - omega_s, domega], dtype=float)


def natural_frequency(delta0: float, e_pu: float, v_pu: float, x_pu: float, inertia_s: float, omega_s: float) -> float:
    synchronizing = e_pu * v_pu / x_pu * math.cos(delta0)
    return math.sqrt(omega_s * synchronizing / (2.0 * inertia_s))


def simulate_smib(
    *,
    pm: float,
    e_pu: float,
    v_pu: float,
    x_pu: float,
    inertia_s: float,
    damping: float = 0.0,
    frequency_hz: float = 50.0,
    dt_s: float = 0.01,
    t_end_s: float = 2.0,
    delta0: float | None = None,
    omega0: float | None = None,
    v_profile: callable | None = None,
) -> dict[str, np.ndarray]:
    """Неявная трапеция. v_profile(t) позволяет воспроизвести провал напряжения."""
    omega_s = 2.0 * math.pi * frequency_hz
    if delta0 is None or omega0 is None:
        delta_eq, omega_eq = equilibrium(pm, e_pu, v_pu, x_pu, omega_s)
        delta0 = delta_eq if delta0 is None else delta0
        omega0 = omega_eq if omega0 is None else omega0
    steps = int(round(t_end_s / dt_s))
    time = np.linspace(0.0, steps * dt_s, steps + 1)
    state = np.zeros((steps + 1, 2))
    state[0] = (delta0, omega0)
    residual0 = derivative(state[0], pm, e_pu, v_pu, x_pu, inertia_s, damping, omega_s)
    for index in range(steps):
        voltage_now = v_pu if v_profile is None else v_profile(time[index])
        voltage_next = v_pu if v_profile is None else v_profile(time[index + 1])
        previous = derivative(state[index], pm, e_pu, voltage_now, x_pu, inertia_s, damping, omega_s)
        guess = state[index] + dt_s * previous
        for _ in range(12):
            upcoming = derivative(guess, pm, e_pu, voltage_next, x_pu, inertia_s, damping, omega_s)
            mismatch = guess - state[index] - 0.5 * dt_s * (previous + upcoming)
            jacobian = _numerical_jacobian(guess, pm, e_pu, voltage_next, x_pu, inertia_s, damping, omega_s, dt_s)
            step = np.linalg.solve(jacobian, mismatch)
            guess = guess - step
            if np.linalg.norm(step) < 1e-10:
                break
        state[index + 1] = guess
    return {"t": time, "delta": state[:, 0], "omega": state[:, 1], "equilibrium_residual": residual0}


def _numerical_jacobian(state, pm, e_pu, v_pu, x_pu, inertia, damping, omega_s, dt) -> np.ndarray:
    base = derivative(state, pm, e_pu, v_pu, x_pu, inertia, damping, omega_s)
    matrix = np.eye(2)
    for index in range(2):
        bumped = state.copy()
        bumped[index] += 1e-6
        changed = derivative(bumped, pm, e_pu, v_pu, x_pu, inertia, damping, omega_s)
        matrix[:, index] += 0.5 * dt * (changed - base) / 1e-6
    return matrix
