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


def simulate_two_machine(
    *,
    pm: float,
    emf: float,
    reactance: float,
    inertia_s: float,
    frequency_hz: float = 50.0,
    dt_s: float = 0.01,
    t_end_s: float = 0.2,
    disturbance_rad: float = 0.0,
) -> dict[str, np.ndarray]:
    """Две классические машины на общей реактивности. Pm второй равна −Pm первой."""
    omega_s = 2.0 * math.pi * frequency_hz
    angle = math.asin(pm * reactance / (emf * emf))
    state = np.array([angle / 2.0 + disturbance_rad, omega_s, -angle / 2.0, omega_s], dtype=float)

    def deriv(value: np.ndarray) -> np.ndarray:
        electrical = emf * emf / reactance * math.sin(value[0] - value[2])
        d_omega_1 = (omega_s / (2.0 * inertia_s)) * (pm - electrical)
        d_omega_2 = (omega_s / (2.0 * inertia_s)) * (-pm + electrical)
        return np.array([value[1] - omega_s, d_omega_1, value[3] - omega_s, d_omega_2])

    steps = int(round(t_end_s / dt_s))
    trace = np.zeros((steps + 1, 4))
    trace[0] = state
    residual = deriv(state)
    for index in range(steps):
        previous = deriv(trace[index])
        guess = trace[index] + dt_s * previous
        for _ in range(8):
            upcoming = deriv(guess)
            mismatch = guess - trace[index] - 0.5 * dt_s * (previous + upcoming)
            jacobian = np.eye(4)
            for column in range(4):
                bumped = guess.copy()
                bumped[column] += 1e-6
                jacobian[:, column] += 0.5 * dt_s * (deriv(bumped) - upcoming) / 1e-6
            guess = guess - np.linalg.solve(jacobian, mismatch)
        trace[index + 1] = guess
    return {"delta": trace[:, 0], "equilibrium_residual": residual}


def simulate_smib_with_controls(
    *,
    pm: float,
    e_pu: float,
    v_pu: float,
    x_pu: float,
    inertia_s: float,
    exciter_s: float = 0.2,
    governor_s: float = 0.5,
    droop: float = 0.05,
    ke: float = 1.0,
    frequency_hz: float = 50.0,
    dt_s: float = 0.01,
    t_end_s: float = 0.2,
    voltage_scale: float = 1.0,
) -> dict[str, np.ndarray]:
    """Классическая машина, ЭДС первого порядка и статизм мощности.

    Это учебный контур SMIB, не модель AVR/PSS промышленного генератора.
    """
    omega_s = 2.0 * math.pi * frequency_hz
    vref = v_pu + e_pu / ke
    pref = pm
    delta0 = math.asin(pm * x_pu / (e_pu * v_pu))
    state0 = np.array([delta0, omega_s, e_pu, pm], dtype=float)

    def deriv(state: np.ndarray, voltage: float) -> np.ndarray:
        delta, omega, field, mech = (float(item) for item in state)
        electrical = field * voltage / x_pu * math.sin(delta)
        return np.array(
            [
                omega - omega_s,
                (omega_s / (2.0 * inertia_s)) * (mech - electrical),
                ((vref - voltage) * ke - field) / exciter_s,
                (pref - (omega - omega_s) / omega_s / droop - mech) / governor_s,
            ]
        )

    steps = int(round(t_end_s / dt_s))
    trace = np.zeros((steps + 1, 4))
    trace[0] = state0
    residual = deriv(state0, v_pu)
    for index in range(steps):
        voltage = v_pu * voltage_scale
        previous = deriv(trace[index], voltage if index else v_pu)
        guess = trace[index] + dt_s * previous
        for _ in range(10):
            upcoming = deriv(guess, voltage)
            mismatch = guess - trace[index] - 0.5 * dt_s * (previous + upcoming)
            jacobian = np.eye(4)
            for column in range(4):
                bumped = guess.copy()
                bumped[column] += 1e-6
                jacobian[:, column] += 0.5 * dt_s * (deriv(bumped, voltage) - upcoming) / 1e-6
            guess = guess - np.linalg.solve(jacobian, mismatch)
        trace[index + 1] = guess
    return {"field": trace[:, 2], "mechanical": trace[:, 3], "equilibrium_residual": residual}


def _numerical_jacobian(state, pm, e_pu, v_pu, x_pu, inertia, damping, omega_s, dt) -> np.ndarray:
    base = derivative(state, pm, e_pu, v_pu, x_pu, inertia, damping, omega_s)
    matrix = np.eye(2)
    for index in range(2):
        bumped = state.copy()
        bumped[index] += 1e-6
        changed = derivative(bumped, pm, e_pu, v_pu, x_pu, inertia, damping, omega_s)
        matrix[:, index] += 0.5 * dt * (changed - base) / 1e-6
    return matrix
