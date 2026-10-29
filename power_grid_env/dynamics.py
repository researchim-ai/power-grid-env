"""Классическая модель синхронной машины, неявная трапеция.

Область: одна машина — шины бесконечной мощности. Это не общий RMS-backend
и не замена ANDES. Инициализация согласована с балансом мощности.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def release_status() -> dict[str, bool | str]:
    """Промышленный RMS-релиз не открыт. Классическая схема ANDES его не заменяет."""
    from power_grid_env.andes_rms import ensure_andes_importable

    installed = ensure_andes_importable()
    return {
        "industrial_rms_qualified": False,
        "andes_integrated": False,
        "andes_status": "installed" if installed else "not_installed",
        "analytic_smib": "regression_only",
        "pss1a": "block_diagram",
    }


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
    pm_profile: callable | None = None,
    x_profile: callable | None = None,
    event_times: list[float] | None = None,
    max_newton: int = 12,
    tolerance: float = 1e-9,
) -> dict[str, np.ndarray]:
    """Неявная трапеция. Разрывы профилей задаются event_times и не попадают внутрь шага."""
    omega_s = 2.0 * math.pi * frequency_hz
    pm_initial = pm if pm_profile is None else float(pm_profile(0.0))
    x_initial = x_pu if x_profile is None else float(x_profile(0.0))
    if delta0 is None or omega0 is None:
        delta_eq, omega_eq = equilibrium(pm_initial, e_pu, v_pu, x_initial, omega_s)
        delta0 = delta_eq if delta0 is None else delta0
        omega0 = omega_eq if omega0 is None else omega0
    time = _time_grid(t_end_s, dt_s, event_times)
    state = np.zeros((len(time), 2))
    state[0] = (delta0, omega0)
    residual0 = derivative(state[0], pm_initial, e_pu, v_pu, x_pu, inertia_s, damping, omega_s)
    for index in range(len(time) - 1):
        step_s = float(time[index + 1] - time[index])
        voltage_now = v_pu if v_profile is None else v_profile(time[index])
        voltage_next = v_pu if v_profile is None else v_profile(time[index + 1])
        power_now = pm if pm_profile is None else float(pm_profile(float(time[index])))
        power_next = pm if pm_profile is None else float(pm_profile(float(time[index + 1])))
        reactance_now = x_pu if x_profile is None else float(x_profile(float(time[index])))
        reactance_next = x_pu if x_profile is None else float(x_profile(float(time[index + 1])))

        def deriv(value, right_v=voltage_next, right_p=power_next, right_x=reactance_next):
            return derivative(value, right_p, e_pu, right_v, right_x, inertia_s, damping, omega_s)

        previous = derivative(state[index], power_now, e_pu, voltage_now, reactance_now, inertia_s, damping, omega_s)
        state[index + 1] = _implicit_trapezoid(state[index], step_s, previous, deriv, max_newton=max_newton, tolerance=tolerance)
    return {"t": time, "delta": state[:, 0], "omega": state[:, 1], "equilibrium_residual": residual0, "converged": True}


def pss1a_step(
    time_s: float,
    *,
    gain: float = 1.0,
    washout_s: float = 1.0,
    lead_s: float = 0.2,
    lag_s: float = 0.2,
    lead2_s: float = 0.2,
    lag2_s: float = 0.2,
    ceiling: float = 10.0,
    dt_s: float = 0.001,
) -> float:
    """Каскад PSS1A на единичный скачок: washout, два lead-lag и потолок выхода.

    Это структурная схема IEEE, не подключение ANDES.
    """
    if washout_s <= 0 or lag_s <= 0 or lag2_s <= 0 or lead_s < 0 or lead2_s < 0 or ceiling <= 0 or dt_s <= 0 or time_s < 0:
        raise ValueError("постоянные PSS1A и потолок должны быть согласованы")
    steps = int(round(time_s / dt_s))
    wash = 0.0
    first = 0.0
    second = 0.0
    command = 1.0

    def deriv(state: np.ndarray) -> np.ndarray:
        wash_state, lead_state, lead2_state = (float(item) for item in state)
        wash_out = command - wash_state
        lead_out, lead_rate = lead_lag_state(wash_out, lead_state, lead_s, lag_s)
        _second_out, lead2_rate = lead_lag_state(lead_out, lead2_state, lead2_s, lag2_s)
        return np.array([(command - wash_state) / washout_s, lead_rate, lead2_rate])

    state = np.zeros(3)
    output = 0.0
    for _ in range(steps):
        previous = deriv(state)
        state = _implicit_trapezoid(state, dt_s, previous, deriv)
        wash_out = command - float(state[0])
        lead_out, _rate = lead_lag_state(wash_out, float(state[1]), lead_s, lag_s)
        output, _rate2 = lead_lag_state(lead_out, float(state[2]), lead2_s, lag2_s)
    return max(-ceiling, min(ceiling, gain * output))


def ac_component(
    time_s: float,
    subtransient_a: float,
    transient_a: float,
    steady_a: float,
    subtransient_s: float,
    transient_s: float,
) -> float:
    """Действующий переменный ток: две экспоненты и установившееся значение.

    В нуле это сверхпереходный ток, на бесконечности — установившийся.
    Это не модель Парка и не коэффициент μ стандарта.
    """
    if time_s < 0 or subtransient_s <= 0 or transient_s <= 0:
        raise ValueError("время неотрицательно, постоянные машины положительны")
    if subtransient_a < transient_a or transient_a < steady_a:
        raise ValueError("сверхпереходный ток не меньше переходного, переходный не меньше установившегося")
    fast = (subtransient_a - transient_a) * math.exp(-time_s / subtransient_s)
    slow = (transient_a - steady_a) * math.exp(-time_s / transient_s)
    return fast + slow + steady_a


def field_command(vref: float, voltage: float, ke: float, emax: float) -> float:
    """Команда возбуждения с потолком. Без потолка она может быть выше emax."""
    if ke <= 0 or emax <= 0:
        raise ValueError("коэффициент возбуждения и потолок должны быть положительными")
    raw = (vref - voltage) * ke
    return max(-emax, min(emax, raw))


def field_response(initial: float, target: float, time_s: float, exciter_s: float) -> float:
    """Решение ЭДС первого порядка при постоянной команде."""
    if exciter_s <= 0 or time_s < 0:
        raise ValueError("постоянная возбудителя и время должны быть согласованы")
    return target + (initial - target) * math.exp(-time_s / exciter_s)


def drive_current(reference_a: float, time_s: float, electrical_s: float, limit_a: float) -> float:
    """Токовый контур первого порядка с ограничением. Это не заводской преобразователь частоты."""
    if electrical_s <= 0 or limit_a <= 0 or time_s < 0:
        raise ValueError("постоянная контура, предел тока и время должны быть согласованы")
    target = max(-limit_a, min(limit_a, reference_a))
    return target * (1.0 - math.exp(-time_s / electrical_s))


def drive_torque(current_a: float, torque_per_ampere: float, limit_a: float) -> float:
    if torque_per_ampere < 0 or limit_a <= 0:
        raise ValueError("постоянная момента неотрицательна, предел тока положителен")
    used = max(-limit_a, min(limit_a, current_a))
    return torque_per_ampere * used


def soft_start_fraction(time_s: float, ramp_s: float) -> float:
    """Доля напряжения линейного пуска. После рампы остаётся 1."""
    if ramp_s <= 0 or time_s < 0:
        raise ValueError("длительность рампы должна быть положительной, время — неотрицательным")
    return min(1.0, time_s / ramp_s)


def lead_lag_state(command: float, state: float, lead_s: float, lag_s: float) -> tuple[float, float]:
    """y = (T1/T2) u + (1 − T1/T2) x, dx/dt = (u − x) / T2. Скачок u не разрывает x."""
    if lag_s <= 0 or lead_s < 0:
        raise ValueError("постоянная запаздывания должна быть положительной, опережения — неотрицательной")
    derivative = (command - state) / lag_s
    output = (lead_s / lag_s) * command + (1.0 - lead_s / lag_s) * state
    return output, derivative


def tgov1_power(
    speed_pu: np.ndarray,
    step_s: float | np.ndarray,
    droop: float,
    valve_s: float,
    lead_s: float,
    lag_s: float,
    power0: float,
) -> np.ndarray:
    """Мощность TGOV1 по ряду скорости. Клапан без упора: спрос P0 − (ω−1)/R, затем lead-lag.

    Первый отсчёт — равновесие. Дальше каждый шаг берёт скорость на своём правом конце.
    """
    speed = np.asarray(speed_pu, dtype=float)
    if speed.ndim != 1 or len(speed) < 2:
        raise ValueError("ряд скорости TGOV1 должен содержать хотя бы два отсчёта")
    if droop <= 0 or valve_s <= 0 or lag_s <= 0 or lead_s < 0:
        raise ValueError("статизм и постоянные TGOV1 должны быть согласованы")
    if np.isscalar(step_s):
        steps = np.full(len(speed) - 1, float(step_s))
    else:
        steps = np.asarray(step_s, dtype=float)
    if len(steps) != len(speed) - 1 or np.any(steps <= 0):
        raise ValueError("шаги TGOV1 должны быть положительными и совпадать с рядом скорости")

    def deriv(value: np.ndarray, deviation: float) -> np.ndarray:
        valve, lead = (float(item) for item in value)
        demand = power0 - deviation / droop
        _output, lead_rate = lead_lag_state(valve, lead, lead_s, lag_s)
        return np.array([(demand - valve) / valve_s, lead_rate])

    state = np.array([power0, power0], dtype=float)
    output = np.empty(len(speed))
    output[0] = power0
    previous = deriv(state, float(speed[0] - 1.0))
    for index, step in enumerate(steps):
        deviation = float(speed[index + 1] - 1.0)

        def deriv_right(value: np.ndarray, deviation: float = deviation) -> np.ndarray:
            return deriv(value, deviation)

        state = _implicit_trapezoid(state, float(step), previous, deriv_right)
        output[index + 1] = lead_lag_state(float(state[0]), float(state[1]), lead_s, lag_s)[0]
        previous = deriv(state, deviation)
    return output


def lead_lag_step(command: float, time_s: float, lead_s: float, lag_s: float) -> float:
    """Отклик на скачок из нуля. На бесконечности выход равен входу."""
    if time_s < 0:
        raise ValueError("время отклика не может быть отрицательным")
    _output, _derivative = lead_lag_state(command, 0.0, lead_s, lag_s)
    decay = math.exp(-time_s / lag_s)
    return command * (1.0 - (1.0 - lead_s / lag_s) * decay)


def simulate_lead_lag(
    *,
    command: float,
    lead_s: float,
    lag_s: float,
    dt_s: float,
    t_end_s: float,
) -> np.ndarray:
    if dt_s <= 0 or t_end_s < 0:
        raise ValueError("шаг и горизонт lead-lag должны быть неотрицательными")
    steps = int(round(t_end_s / dt_s))
    state = np.array([0.0])
    trace = np.zeros(steps + 1)

    def deriv(value: np.ndarray) -> np.ndarray:
        _output, rate = lead_lag_state(command, float(value[0]), lead_s, lag_s)
        return np.array([rate])

    for index in range(steps):
        previous = deriv(state)
        state = _implicit_trapezoid(state, dt_s, previous, deriv)
        trace[index + 1] = float(state[0])
    return trace


def limited_power_balance(
    pref: float,
    pmax: float,
    e_pu: float,
    v_pu: float,
    x_pu: float,
    omega_s: float,
) -> dict[str, float]:
    """Предел обрезает уставку. Частота остаётся синхронной, если новый момент ниже предела передачи."""
    if pmax <= 0 or x_pu <= 0 or omega_s <= 0:
        raise ValueError("предел мощности, реактивность и скорость должны быть положительными")
    mechanical = min(pref, pmax)
    margin = abs(e_pu * v_pu / x_pu)
    if abs(mechanical) > margin:
        raise ValueError("после ограничения момент выше предела передачи")
    delta = math.asin(mechanical * x_pu / (e_pu * v_pu))
    return {"delta_rad": delta, "omega_rad_s": omega_s, "mechanical": mechanical, "limited": pref > pmax}


def simulate_governor_limit(
    *,
    pref: float,
    pmax: float,
    e_pu: float,
    v_pu: float,
    x_pu: float,
    inertia_s: float,
    governor_s: float = 0.2,
    droop: float = 0.05,
    frequency_hz: float = 50.0,
    dt_s: float = 0.01,
    t_end_s: float = 1.0,
    speed_offset: float = 0.0,
) -> dict[str, np.ndarray]:
    """Механическая уставка не выходит за pmax. Это не промышленный регулятор."""
    if pmax <= 0 or governor_s <= 0 or droop <= 0 or inertia_s <= 0:
        raise ValueError("предел, постоянная регулятора, статизм и инерция должны быть положительными")
    omega_s = 2.0 * math.pi * frequency_hz
    steps = int(round(t_end_s / dt_s))
    trace = np.zeros((steps + 1, 3))
    trace[0] = (0.0, omega_s + speed_offset, min(pref, pmax))

    def deriv(state: np.ndarray) -> np.ndarray:
        delta, omega, mech = (float(item) for item in state)
        electrical = e_pu * v_pu / x_pu * math.sin(delta)
        target = pref - (omega - omega_s) / omega_s / droop
        target = min(pmax, max(0.0, target))
        return np.array(
            [
                omega - omega_s,
                (omega_s / (2.0 * inertia_s)) * (mech - electrical),
                (target - mech) / governor_s,
            ]
        )

    state = trace[0].copy()
    for index in range(steps):
        previous = deriv(state)
        state = _implicit_trapezoid(state, dt_s, previous, deriv)
        state[2] = min(pmax, max(0.0, float(state[2])))
        trace[index + 1] = state
    return {"delta": trace[:, 0], "omega": trace[:, 1], "mechanical": trace[:, 2]}


def pss_derivative(
    state: np.ndarray,
    pm: float,
    e_pu: float,
    v_pu: float,
    x_pu: float,
    inertia_s: float,
    omega_s: float,
    gain: float,
    washout_s: float,
) -> np.ndarray:
    """Классическая машина и washout по скорости. В покое выход стабилизатора равен нулю."""
    if inertia_s <= 0 or washout_s <= 0 or x_pu <= 0:
        raise ValueError("инерция, washout и реактивность должны быть положительными")
    delta, omega, filtered = (float(item) for item in state)
    speed = omega - omega_s
    electrical = e_pu * v_pu / x_pu * math.sin(delta)
    torque = gain * (speed - filtered)
    return np.array(
        [
            speed,
            (omega_s / (2.0 * inertia_s)) * (pm - electrical - torque),
            (speed - filtered) / washout_s,
        ],
        dtype=float,
    )


def pss_state_matrix(
    delta: float,
    e_pu: float,
    v_pu: float,
    x_pu: float,
    inertia_s: float,
    omega_s: float,
    gain: float,
    washout_s: float,
) -> np.ndarray:
    """Линеаризация вокруг угла. Это не IEEE PSS1A."""
    synchronizing = e_pu * v_pu / x_pu * math.cos(delta)
    scale = omega_s / (2.0 * inertia_s)
    return np.array(
        [
            [0.0, 1.0, 0.0],
            [-scale * synchronizing, -scale * gain, scale * gain],
            [0.0, 1.0 / washout_s, -1.0 / washout_s],
        ],
        dtype=float,
    )


def simulate_pss(
    *,
    pm: float,
    e_pu: float,
    v_pu: float,
    x_pu: float,
    inertia_s: float,
    gain: float,
    washout_s: float,
    frequency_hz: float = 50.0,
    dt_s: float = 0.001,
    t_end_s: float = 0.2,
    disturbance_rad: float = 0.0,
) -> dict[str, np.ndarray]:
    omega_s = 2.0 * math.pi * frequency_hz
    delta0, _omega = equilibrium(pm, e_pu, v_pu, x_pu, omega_s)
    steps = int(round(t_end_s / dt_s))
    trace = np.zeros((steps + 1, 3))
    trace[0] = (delta0 + disturbance_rad, omega_s, 0.0)

    def deriv(value: np.ndarray) -> np.ndarray:
        return pss_derivative(value, pm, e_pu, v_pu, x_pu, inertia_s, omega_s, gain, washout_s)

    for index in range(steps):
        previous = deriv(trace[index])
        trace[index + 1] = _implicit_trapezoid(trace[index], dt_s, previous, deriv)
    return {"delta": trace[:, 0], "omega": trace[:, 1], "filter": trace[:, 2], "equilibrium_angle": np.array([delta0])}


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


def controlled_balance(
    pm: float,
    e_pu: float,
    v_pu: float,
    x_pu: float,
    *,
    voltage_scale: float = 1.0,
    ke: float = 1.0,
    frequency_hz: float = 50.0,
) -> dict[str, float]:
    """Равновесие ЭДС и статизма после изменения напряжения. Частота снова синхронная.

    Постоянное отклонение частоты появилось бы, только если новый предел момента
    не покрывает Pm. Тогда равновесия нет.
    """
    voltage = v_pu * voltage_scale
    if voltage <= 0 or x_pu <= 0:
        raise ValueError("напряжение и реактивность должны быть положительными")
    vref = v_pu + e_pu / ke
    field = (vref - voltage) * ke
    margin = field * voltage / x_pu
    if abs(pm) > abs(margin):
        raise ValueError("после изменения напряжения момент выше предела")
    delta = math.asin(pm * x_pu / (field * voltage))
    omega = 2.0 * math.pi * frequency_hz
    electrical = field * voltage / x_pu * math.sin(delta)
    return {
        "delta_rad": delta,
        "omega_rad_s": omega,
        "field": field,
        "mechanical": pm,
        "electrical": electrical,
        "power_balance": electrical - pm,
    }


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
    return {
        "delta": trace[:, 0],
        "omega": trace[:, 1],
        "field": trace[:, 2],
        "mechanical": trace[:, 3],
        "equilibrium_residual": residual,
        "converged": True,
    }


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


def simulate_induction_slip(
    *,
    terminal_v: float,
    rotor_r: float,
    reactance: float,
    load_power_w: float,
    inertia_kgm2: float,
    omega_sync: float,
    slip0: float = 1.0,
    dt_s: float = 0.001,
    t_end_s: float = 1.0,
    emf: complex | None = None,
    thevenin: complex | None = None,
) -> dict[str, np.ndarray]:
    """Разгон скольжения неявной трапецией.

    induction_torque возвращает мощность зазора на фазу. Момент — эта мощность,
    делённая на синхронную скорость, поэтому
    ds/dt = (Pнагр − Pзазора) / (J ω²).
    emf и thevenin пересчитывают напряжение на каждом шаге. Это не вытеснение тока.
    """
    if inertia_kgm2 <= 0 or omega_sync <= 0 or rotor_r <= 0 or reactance <= 0:
        raise ValueError("инерция, скорость, R и X двигателя должны быть положительными")
    if dt_s <= 0 or t_end_s < 0 or slip0 <= 0 or terminal_v < 0:
        raise ValueError("шаг, начальное скольжение и напряжение должны быть положительными")
    steps = int(round(t_end_s / dt_s))
    trace = np.zeros(steps + 1)
    state = np.array([slip0], dtype=float)
    trace[0] = slip0

    def deriv(value: np.ndarray) -> np.ndarray:
        slip = float(value[0])
        voltage = _induction_terminal_voltage(slip, terminal_v, emf, thevenin, rotor_r, reactance)
        power = induction_torque(slip, voltage, rotor_r, reactance)
        rate = (load_power_w - power) / (inertia_kgm2 * omega_sync * omega_sync)
        return np.array([rate], dtype=float)

    for index in range(steps):
        previous = deriv(state)
        state = _implicit_trapezoid(state, dt_s, previous, deriv)
        trace[index + 1] = float(state[0])
    return {"t": np.linspace(0.0, steps * dt_s, steps + 1), "slip": trace}


def sag_then_restore(
    *,
    slip0: float,
    v_nominal: float,
    v_sag: float,
    rotor_r: float,
    reactance: float,
    load_power_w: float,
    inertia_kgm2: float,
    omega_sync: float,
    sag_s: float,
    recover_s: float,
    dt_s: float,
) -> dict[str, float]:
    """Провал напряжения, затем возврат. Скольжение не сбрасывается между участками."""
    during = simulate_induction_slip(
        terminal_v=v_sag,
        rotor_r=rotor_r,
        reactance=reactance,
        load_power_w=load_power_w,
        inertia_kgm2=inertia_kgm2,
        omega_sync=omega_sync,
        slip0=slip0,
        dt_s=dt_s,
        t_end_s=sag_s,
    )
    after = simulate_induction_slip(
        terminal_v=v_nominal,
        rotor_r=rotor_r,
        reactance=reactance,
        load_power_w=load_power_w,
        inertia_kgm2=inertia_kgm2,
        omega_sync=omega_sync,
        slip0=float(during["slip"][-1]),
        dt_s=dt_s,
        t_end_s=recover_s,
    )
    return {"slip_after_sag": float(during["slip"][-1]), "slip_after_recovery": float(after["slip"][-1])}


def _induction_terminal_voltage(
    slip: float,
    terminal_v: float,
    emf: complex | None,
    thevenin: complex | None,
    rotor_r: float,
    reactance: float,
) -> float:
    if emf is None:
        return terminal_v
    if abs(slip) < 1e-12:
        return abs(emf)
    motor = rotor_r / slip + 1j * reactance
    behind = 0j if thevenin is None else thevenin
    return abs(emf * motor / (motor + behind))


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


def simulate_group_runup(
    emf: complex,
    z_line: complex,
    motors: list[dict[str, float | str]],
    *,
    minimum_v: float,
    release_slip: float,
    inertia_kgm2: float,
    omega_sync: float,
    dt_s: float,
    t_end_s: float,
) -> dict[str, Any]:
    """Самозапуск группы. Следующий двигатель подключается после разгона уже включённых.

    На каждом шаге напряжение общее. Двигатель выше опрокидывания не подключается.
    Это одноконтурные клетки с инерцией, не вытеснение тока в пазу.
    """
    if inertia_kgm2 <= 0 or omega_sync <= 0 or dt_s <= 0 or release_slip <= 0 or minimum_v <= 0:
        raise ValueError("инерция, скорость, шаг, порог скольжения и напряжение должны быть положительными")
    ordered = sorted(motors, key=lambda item: float(item["priority"]))
    slips = np.ones(len(ordered))
    online: list[int] = []
    steps = int(round(t_end_s / dt_s))
    trace = np.ones((steps + 1, len(ordered)))
    connected_at = [math.inf] * len(ordered)

    def voltage_of(active: list[int], values: np.ndarray) -> complex:
        admit = 0j
        for index in active:
            slip = float(values[index])
            if slip <= 1e-12:
                continue
            motor = ordered[index]
            admit += 1.0 / (float(motor["rotor_r"]) / slip + 1j * float(motor["reactance"]))
        if admit == 0j:
            return emf
        return emf / (1.0 + z_line * admit)

    def accept(index: int, values: np.ndarray) -> bool:
        trial = np.array(values, dtype=float, copy=True)
        trial[index] = 1.0
        voltage = abs(voltage_of(online + [index], trial))
        motor = ordered[index]
        try:
            induction_equilibrium_slip(voltage, float(motor["rotor_r"]), float(motor["reactance"]), float(motor["load_torque"]))
        except ValueError:
            return False
        return voltage >= minimum_v

    def connect(at_step: int) -> None:
        if len(online) >= len(ordered):
            return
        if online and max(float(slips[index]) for index in online) > release_slip:
            return
        candidate = len(online)
        if not accept(candidate, slips):
            return
        online.append(candidate)
        slips[candidate] = 1.0
        connected_at[candidate] = at_step * dt_s

    connect(0)
    trace[0] = slips
    state = np.array([slips[index] for index in online], dtype=float) if online else np.zeros(0)

    for step in range(steps):
        if online:
            def deriv(value: np.ndarray, active=tuple(online)) -> np.ndarray:
                local = np.array(slips, dtype=float, copy=True)
                for cursor, index in enumerate(active):
                    local[index] = float(value[cursor])
                volts = abs(voltage_of(list(active), local))
                rates = np.zeros(len(active))
                for cursor, index in enumerate(active):
                    motor = ordered[index]
                    power = induction_torque(
                        float(value[cursor]), volts, float(motor["rotor_r"]), float(motor["reactance"])
                    )
                    rates[cursor] = (float(motor["load_torque"]) - power) / (inertia_kgm2 * omega_sync * omega_sync)
                return rates

            previous = deriv(state)
            state = _implicit_trapezoid(state, dt_s, previous, deriv)
            for cursor, index in enumerate(online):
                slips[index] = float(state[cursor])
        connect(step + 1)
        if len(state) != len(online):
            state = np.array([slips[index] for index in online], dtype=float)
        trace[step + 1] = slips
    return {
        "started": [str(ordered[index]["id"]) for index in online],
        "slips": {str(ordered[index]["id"]): float(slips[index]) for index in range(len(ordered))},
        "connected_at_s": {str(ordered[index]["id"]): connected_at[index] for index in range(len(ordered))},
        "trace": trace,
        "t": np.linspace(0.0, steps * dt_s, steps + 1),
    }


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
