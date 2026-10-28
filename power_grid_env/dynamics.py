"""Классическая модель синхронной машины, неявная трапеция.

Область: одна машина — шины бесконечной мощности. Это не общий RMS-backend
и не замена ANDES. Инициализация согласована с балансом мощности.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def release_status() -> dict[str, bool | str]:
    """Промышленный RMS-релиз не открыт. SMIB остаётся аналитической регрессией."""
    return {"industrial_rms_qualified": False, "andes_integrated": False, "analytic_smib": "regression_only"}


def state_from_terminal_voltage(
    pm: float,
    e_pu: float,
    v_pu: float,
    x_pu: float,
    inertia_s: float,
    damping: float,
    omega_s: float,
) -> dict[str, float]:
    """Начальное состояние из напряжения режима. Шаг времени не делается."""
    delta, omega = equilibrium(pm, e_pu, v_pu, x_pu, omega_s)
    residual = derivative(np.array([delta, omega]), pm, e_pu, v_pu, x_pu, inertia_s, damping, omega_s)
    return {
        "delta_rad": delta,
        "omega_rad_s": omega,
        "residual_max": float(np.max(np.abs(residual))),
        "steps": 0.0,
    }


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
    event_times: list[float] | None = None,
    max_newton: int = 12,
    tolerance: float = 1e-9,
) -> dict[str, np.ndarray]:
    """Неявная трапеция. Разрывы v_profile задаются event_times и не попадают внутрь шага."""
    omega_s = 2.0 * math.pi * frequency_hz
    if delta0 is None or omega0 is None:
        delta_eq, omega_eq = equilibrium(pm, e_pu, v_pu, x_pu, omega_s)
        delta0 = delta_eq if delta0 is None else delta0
        omega0 = omega_eq if omega0 is None else omega0
    time = _time_grid(t_end_s, dt_s, event_times)
    state = np.zeros((len(time), 2))
    state[0] = (delta0, omega0)
    residual0 = derivative(state[0], pm, e_pu, v_pu, x_pu, inertia_s, damping, omega_s)
    for index in range(len(time) - 1):
        step_s = float(time[index + 1] - time[index])
        voltage_now = v_pu if v_profile is None else v_profile(time[index])
        voltage_next = v_pu if v_profile is None else v_profile(time[index + 1])

        def deriv(value, right=voltage_next):
            return derivative(value, pm, e_pu, right, x_pu, inertia_s, damping, omega_s)

        previous = derivative(state[index], pm, e_pu, voltage_now, x_pu, inertia_s, damping, omega_s)
        state[index + 1] = _implicit_trapezoid(state[index], step_s, previous, deriv, max_newton=max_newton, tolerance=tolerance)
    return {"t": time, "delta": state[:, 0], "omega": state[:, 1], "equilibrium_residual": residual0, "converged": True}


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
        trace[index + 1] = _implicit_trapezoid(trace[index], dt_s, previous, deriv, max_newton=8)
    return {"delta": trace[:, 0], "equilibrium_residual": residual, "converged": True}


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
        trace[index + 1] = _implicit_trapezoid(trace[index], dt_s, previous, lambda value: deriv(value, voltage), max_newton=10)
    return {"field": trace[:, 2], "mechanical": trace[:, 3], "equilibrium_residual": residual, "converged": True}


def induction_torque(slip: float, voltage: float, rotor_r: float, reactance: float) -> float:
    """Момент одноконтурной схемы замещения. Напряжение — фазное, момент — на фазу."""
    if abs(slip) < 1e-9:
        return 0.0
    resistance = rotor_r / slip
    return voltage * voltage * resistance / (resistance * resistance + reactance * reactance)


def induction_equilibrium_slip(voltage: float, rotor_r: float, reactance: float, load_torque: float) -> float:
    """Меньший положительный корень Te(s) = Tнагр. Годится ниже опрокидывания."""
    if load_torque <= 0:
        raise ValueError("нужен положительный момент нагрузки")
    # V^2 * (R/s) = T * ((R/s)^2 + X^2)
    # T R^2 + T X^2 s^2 - V^2 R s = 0
    a = load_torque * reactance * reactance
    b = -voltage * voltage * rotor_r
    c = load_torque * rotor_r * rotor_r
    discriminant = b * b - 4 * a * c
    if discriminant < 0:
        raise ValueError("момент выше опрокидывающего")
    root = (-b - math.sqrt(discriminant)) / (2 * a)
    return root


def airgap_torque(power_w: float, synchronous_rad_s: float) -> float:
    """Te = Pзазора / ωсинхр. induction_torque возвращает мощность зазора на фазу, не Н·м."""
    if synchronous_rad_s <= 0:
        raise ValueError("синхронная скорость должна быть положительной")
    return power_w / synchronous_rad_s


def coupled_induction(
    emf: complex,
    thevenin: complex,
    rotor_r: float,
    reactance: float,
    load_torque: float,
    parallel: complex | None = None,
) -> dict[str, float | complex | bool]:
    """Двигатель за сопротивлением сети. Ветвь ниже опрокидывания. parallel — соседний шунт."""
    if rotor_r <= 0 or reactance <= 0 or load_torque <= 0:
        raise ValueError("R, X и момент нагрузки должны быть положительными")
    breakdown = rotor_r / reactance

    def voltage_at(slip: float) -> complex:
        motor = rotor_r / slip + 1j * reactance
        admit = 1.0 / motor
        if parallel is not None:
            admit += 1.0 / parallel
        return emf / (1.0 + thevenin * admit)

    def mismatch(slip: float) -> float:
        return induction_torque(slip, abs(voltage_at(slip)), rotor_r, reactance) - load_torque

    if mismatch(breakdown) < 0.0:
        return {"stalled": True, "slip": breakdown, "voltage": voltage_at(breakdown), "current": 0j}
    low = 1e-6
    high = breakdown
    for _ in range(60):
        mid = 0.5 * (low + high)
        if mismatch(mid) < 0.0:
            low = mid
        else:
            high = mid
    slip = 0.5 * (low + high)
    voltage = voltage_at(slip)
    motor = rotor_r / slip + 1j * reactance
    current = voltage / motor
    return {"stalled": False, "slip": slip, "voltage": voltage, "current": current}


def feeder_restart(
    emf: complex,
    z_line: complex,
    motors: list[dict[str, float | str]],
    minimum_v: float,
) -> dict[str, Any]:
    """Пуск группы по приоритету. Напряжение пересчитывается после каждого принятого двигателя."""
    started: list[dict[str, float | str]] = []
    voltage = emf
    slips: dict[str, float] = {}
    for motor in sorted(motors, key=lambda item: float(item["priority"])):
        trial = _motors_on_feeder(emf, z_line, started + [motor])
        if trial["stalled"] or abs(trial["voltage"]) < minimum_v:
            break
        started.append(motor)
        voltage = trial["voltage"]
        slips = trial["slips"]
    return {"started": [str(item["id"]) for item in started], "voltage": voltage, "slips": slips}


def _motors_on_feeder(emf: complex, z_line: complex, motors: list[dict[str, float | str]]) -> dict[str, Any]:
    if not motors:
        return {"stalled": False, "voltage": emf, "slips": {}, "currents": {}}
    voltage = emf
    slips: dict[str, float] = {}
    currents: dict[str, complex] = {}
    for _ in range(40):
        currents = {}
        for motor in motors:
            try:
                slip = induction_equilibrium_slip(
                    abs(voltage), float(motor["rotor_r"]), float(motor["reactance"]), float(motor["load_torque"])
                )
            except ValueError:
                return {"stalled": True, "voltage": voltage, "slips": slips, "currents": {}}
            impedance = float(motor["rotor_r"]) / slip + 1j * float(motor["reactance"])
            currents[str(motor["id"])] = voltage / impedance
            slips[str(motor["id"])] = slip
        updated = emf - sum(currents.values(), 0j) * z_line
        if abs(updated - voltage) <= 1e-12 * max(1.0, abs(emf)):
            voltage = updated
            slips = {}
            currents = {}
            for motor in motors:
                slip = induction_equilibrium_slip(
                    abs(voltage), float(motor["rotor_r"]), float(motor["reactance"]), float(motor["load_torque"])
                )
                impedance = float(motor["rotor_r"]) / slip + 1j * float(motor["reactance"])
                currents[str(motor["id"])] = voltage / impedance
                slips[str(motor["id"])] = slip
            return {"stalled": False, "voltage": voltage, "slips": slips, "currents": currents}
        voltage = updated
    return {"stalled": True, "voltage": voltage, "slips": slips, "currents": currents}


def restart_sequence(motors: list[dict[str, float | str]], voltage_pu: float, minimum_pu: float) -> list[str]:
    """Пуск по приоритету. Следующий не стартует, если просадка уводит напряжение ниже порога."""
    if minimum_pu <= 0:
        raise ValueError("порог напряжения пуска должен быть положительным")
    started: list[str] = []
    if voltage_pu < minimum_pu:
        return started
    ordered = sorted(motors, key=lambda item: float(item["priority"]))
    for motor in ordered:
        if voltage_pu < minimum_pu:
            break
        started.append(str(motor["id"]))
        voltage_pu -= float(motor["drop_pu"])
    return started


def simulate_induction(
    *,
    voltage: float,
    rotor_r: float,
    reactance: float,
    load_torque: float,
    inertia: float,
    slip0: float = 0.02,
    dt_s: float = 0.002,
    t_end_s: float = 1.0,
) -> dict[str, float]:
    """Скольжение асинхронной машины. Инерция в тех же единицах, что момент и время."""
    slip = slip0
    steps = int(round(t_end_s / dt_s))
    for _ in range(steps):
        torque = induction_torque(slip, voltage, rotor_r, reactance)
        slip = slip + dt_s * (load_torque - torque) / inertia
    target = induction_equilibrium_slip(voltage, rotor_r, reactance, load_torque)
    return {"slip": slip, "equilibrium_slip": target}


def _time_grid(t_end_s: float, dt_s: float, events: list[float] | None = None) -> np.ndarray:
    """Сетка с обязательными моментами разрыва. Шаг не перешагивает событие."""
    if dt_s <= 0 or t_end_s < 0:
        raise ValueError("шаг и горизонт должны быть неотрицательными, шаг положительным")
    times = [0.0]
    cursor = 0.0
    pending = sorted(float(item) for item in (events or []) if 0.0 < float(item) < t_end_s - 1e-15)
    while cursor < t_end_s - 1e-15:
        nxt = min(t_end_s, cursor + dt_s)
        if pending and pending[0] <= nxt + 1e-15:
            nxt = pending.pop(0)
        if nxt <= cursor + 1e-15:
            break
        times.append(nxt)
        cursor = nxt
    return np.asarray(times, dtype=float)


def _trapezoid_jacobian(guess: np.ndarray, deriv, dt_s: float, eps: float = 1e-6) -> np.ndarray:
    """∂R/∂x для R = x - x0 - h/2·(f0+f). Это I - h/2·df/dx, не плюс."""
    upcoming = deriv(guess)
    matrix = np.eye(len(guess))
    for column in range(len(guess)):
        bumped = np.array(guess, dtype=float, copy=True)
        bumped[column] += eps
        matrix[:, column] -= 0.5 * dt_s * (deriv(bumped) - upcoming) / eps
    return matrix


def _implicit_trapezoid(state: np.ndarray, dt_s: float, previous: np.ndarray, deriv, *, max_newton: int = 12, tolerance: float = 1e-9) -> np.ndarray:
    """Один шаг. previous = f(x0). deriv(x) = f(x) на правом конце, уже с напряжением этого конца."""
    guess = np.array(state, dtype=float, copy=True) + dt_s * previous
    residual = guess - state
    for _ in range(max_newton):
        upcoming = deriv(guess)
        residual = guess - state - 0.5 * dt_s * (previous + upcoming)
        if np.linalg.norm(residual) <= tolerance:
            return guess
        jacobian = _trapezoid_jacobian(guess, deriv, dt_s)
        step = np.linalg.solve(jacobian, residual)
        guess = guess - step
    upcoming = deriv(guess)
    residual = guess - state - 0.5 * dt_s * (previous + upcoming)
    if np.linalg.norm(residual) <= tolerance:
        return guess
    raise ValueError(f"шаг трапеции не сошёлся, норма остатка {np.linalg.norm(residual):.3e}")
