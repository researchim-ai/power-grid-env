"""Вклад ветвей, отключение источника, volt-var, холодный пуск, фазор и провал напряжения.

Каждая сверка отделена от соседней. Натурным измерением результат не становится.
"""

from __future__ import annotations

import math
from typing import Any

LN = 10_000.0 / math.sqrt(3.0)


def split_currents(emf: complex, z_source: complex, z_left: complex, z_right: complex, z_fault: complex) -> tuple[complex, complex, complex]:
    """Две линии к одному КЗ. Токи обратно пропорциональны сопротивлениям и в сумме дают ток повреждения."""
    admit = 1.0 / z_left + 1.0 / z_right
    remote = emf / (1.0 + (z_source + 1.0 / admit) / z_fault)
    local = remote + (remote / z_fault) / admit
    left = (local - remote) / z_left
    right = (local - remote) / z_right
    return left, right, remote / z_fault


def parallel_voltage(emf: complex, z_one: complex, z_two: complex | None, z_load: complex) -> complex:
    admit = 1.0 / z_one + 1.0 / z_load
    injected = emf / z_one
    if z_two is not None:
        admit += 1.0 / z_two
        injected += emf / z_two
    return injected / admit


def qualify_branch_split() -> dict[str, Any]:
    z_source = 0.05 + 0.2j
    z_left = 0.4 + 0.8j
    z_right = 0.2 + 1.2j
    z_fault = 0.5 + 0.0j
    expected_left, expected_right, expected_fault = split_currents(LN, z_source, z_left, z_right, z_fault)
    measured = _line_currents(z_source, z_left, z_right, z_fault.real)
    reasons = []
    if measured[1] != "converged":
        reasons.append("status")
    errors = [
        abs(measured[0]["left"] - expected_left) / abs(expected_left),
        abs(measured[0]["right"] - expected_right) / abs(expected_right),
        abs(measured[0]["left"] + measured[0]["right"] - expected_fault) / abs(expected_fault),
    ]
    if max(errors) > 1e-5:
        reasons.append("current")
    if abs(abs(expected_left) - abs(expected_right)) / abs(expected_right) < 0.05:
        reasons.append("identical")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "current_error": max(errors),
        "left_a": abs(expected_left),
        "right_a": abs(expected_right),
    }


def qualify_source_outage() -> dict[str, Any]:
    z_slack = 0.3 + 0.6j
    z_extra = 0.5 + 1.0j
    z_load = 20.0 + 2.0j
    both = parallel_voltage(LN, z_slack, z_extra, z_load)
    alone = parallel_voltage(LN, z_slack, None, z_load)
    expected_both = abs((LN - both) / z_slack)
    expected_alone = abs((LN - alone) / z_slack)
    measured_both = _slack_current(z_slack, z_extra, z_load, keep_extra=True)
    measured_alone = _slack_current(z_slack, z_extra, z_load, keep_extra=False)
    reasons = []
    if measured_both[1] != "converged" or measured_alone[1] != "converged":
        reasons.append("status")
    errors = [
        abs(measured_both[0] - expected_both) / expected_both,
        abs(measured_alone[0] - expected_alone) / expected_alone,
    ]
    if max(errors) > 1e-4:
        reasons.append("current")
    if abs(expected_both - expected_alone) / expected_alone < 0.02:
        reasons.append("unchanged")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "current_error": max(errors),
        "with_extra_a": expected_both,
        "alone_a": expected_alone,
    }


def qualify_volt_var() -> dict[str, Any]:
    from power_grid_env.der import interpolate_curve

    rated = 100_000.0
    curve = [(0.90, 0.50), (0.99, -0.25), (1.01, -0.25), (1.10, -0.50)]
    expected = rated * interpolate_curve(curve, 1.0)
    measured = _volt_var_power(rated, curve)
    reasons = []
    if measured[1] != "converged":
        reasons.append("status")
    error = abs(-measured[0] - expected) / abs(expected)
    if error > 1e-3:
        reasons.append("reactive")
    if interpolate_curve(curve, 0.9) <= 0.0:
        reasons.append("curve")
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "reactive_error": error, "expected_var": expected}


def qualify_cold_load() -> dict[str, Any]:
    from power_grid_env.der import cold_load_multiplier

    nominal = 40_000.0
    peak = 2.5
    tau = 60.0
    at_zero = cold_load_multiplier(0.0, peak, tau)
    at_tau = cold_load_multiplier(tau, peak, tau)
    measured = _bus_power(nominal * at_zero)
    reasons = []
    if abs(at_zero - peak) > 1e-12 or abs(at_tau - (1.0 + (peak - 1.0) / math.e)) > 1e-12:
        reasons.append("law")
    if measured[1] != "converged":
        reasons.append("status")
    if measured[2] > 1e-4:
        reasons.append("power")
    try:
        cold_load_multiplier(0.0, 0.5, tau)
        reasons.append("peak")
    except ValueError:
        pass
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "power_error": measured[2], "at_tau": at_tau}


def qualify_phasor_waveform() -> dict[str, Any]:
    from power_grid_env.fault_envelope import closed_form

    resistance, reactance = 1.0, 2.0
    impedance = math.hypot(resistance, reactance)
    phasor = LN / impedance
    frequency = 50.0
    samples = 4001
    start = 0.2
    period = 1.0 / frequency
    times = [start + period * item / (samples - 1) for item in range(samples)]
    currents = [closed_form(time_s, LN, resistance, reactance, frequency) for time_s in times]
    rms = math.sqrt(sum(value * value for value in currents) / len(currents))
    voltage = [math.sqrt(2.0) * LN * math.sin(2.0 * math.pi * frequency * time_s) for time_s in times]
    average = sum(left * right for left, right in zip(voltage, currents, strict=True)) / len(currents)
    expected_power = phasor * phasor * resistance
    solved = _impedance_current(resistance, reactance)
    reasons = []
    if solved[1] != "converged":
        reasons.append("status")
    rms_error = abs(rms - phasor) / phasor
    power_error = abs(average - expected_power) / expected_power
    network_error = abs(solved[0] - phasor) / phasor
    if rms_error > 1e-4 or power_error > 1e-3 or network_error > 1e-4:
        reasons.append("handoff")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "rms_error": rms_error,
        "power_error": power_error,
        "network_error": network_error,
    }


def qualify_volt_watt() -> dict[str, Any]:
    from power_grid_env.der import interpolate_curve

    requested = 50_000.0
    curve = [(0.90, 1.0), (0.99, 0.4), (1.01, 0.4), (1.10, 0.0)]
    expected = requested * interpolate_curve(curve, 1.0)
    measured = _volt_watt_power(requested, curve)
    reasons = []
    if measured[1] != "converged":
        reasons.append("status")
    error = abs(measured[0] - expected) / expected
    if error > 1e-3:
        reasons.append("active")
    if measured[0] >= requested * 0.8:
        reasons.append("unchanged")
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "power_error": error, "expected_w": expected}


def qualify_priority() -> dict[str, Any]:
    from power_grid_env.der import apparent_limit

    active, reactive = apparent_limit(120_000.0, 80_000.0, 100_000.0, "P")
    swapped_p, swapped_q = apparent_limit(80_000.0, 120_000.0, 100_000.0, "Q")
    kept = apparent_limit(30_000.0, 40_000.0, 100_000.0, "P")
    measured = _priority_power(active, reactive)
    reasons = []
    if (active, reactive) != (100_000.0, 0.0) or (swapped_p, swapped_q) != (0.0, 100_000.0):
        reasons.append("circle")
    if kept != (30_000.0, 40_000.0):
        reasons.append("inside")
    if measured[1] != "converged":
        reasons.append("status")
    if measured[2] > 1e-4:
        reasons.append("power")
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "power_error": measured[2]}


def qualify_undervoltage() -> dict[str, Any]:
    from power_grid_env.protection import RelayState, advance_relay

    measured = _source_pu(0.8)
    low = RelayState("uv", "undervoltage", "load", 0.9, "definite", 0.2, breaker_delay_s=0.05)
    healthy = RelayState("uv-ok", "undervoltage", "load", 0.5, "definite", 0.2, breaker_delay_s=0.05)
    early = RelayState("uv-early", "undervoltage", "load", 0.9, "definite", 0.2, breaker_delay_s=0.05)
    tripped = False
    held = False
    early_trip = False
    for step in range(5):
        if advance_relay(low, measured[0], 0.05) == "trip":
            tripped = True
        if advance_relay(healthy, measured[0], 0.05) == "trip":
            held = True
        if step < 4 and advance_relay(early, measured[0], 0.05) == "trip":
            early_trip = True
    reasons = []
    if measured[1] != "converged":
        reasons.append("status")
    if abs(measured[0] - 0.8) > 1e-4:
        reasons.append("voltage")
    if not tripped or held or early_trip:
        reasons.append("relay")
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "voltage_pu": measured[0]}


def _line_currents(z_source: complex, z_left: complex, z_right: complex, fault_r: float) -> tuple[dict[str, complex], str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.model import StudyFault
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("split", "Ветви", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", z_source.real, z_source.imag)
    builder.line_z1("src", "ld", z_left, line_id="left")
    builder.line_z1("src", "ld", z_right, line_id="right")
    network = builder.build()
    network.sources[0].r0_ohm = z_source.real
    network.sources[0].x0_ohm = z_source.imag
    network.study_faults.append(StudyFault(id="fault", bus_id="ld", kind="3ph", phases=["A", "B", "C"], r_ohm=fault_r))
    solved = solve_network(network)
    currents = {"left": 0j, "right": 0j}
    if solved.status != "converged":
        return currents, solved.status
    bus = compile_topology(network).node_to_bus["src"]
    for name in ("left", "right"):
        currents[name] = next(
            item.current
            for item in solved.flows
            if item.equipment_id == name and item.bus_id == bus and item.phase == "A" and item.terminal_index == 0
        )
    return currents, solved.status


def _slack_current(z_slack: complex, z_extra: complex, z_load: complex, *, keep_extra: bool) -> tuple[float, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    phase = LN**2 / z_load.conjugate()
    active = float(3.0 * phase.real)
    reactive = float(3.0 * phase.imag)
    builder = NetBuilder("srcn1", "Источник", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", z_slack.real, z_slack.imag, source_id="slack")
    if keep_extra:
        builder.source("src", z_extra.real, z_extra.imag, source_id="extra", is_slack=False)
    builder.load("src", active, reactive, model="constant_z", load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status
    current = abs(next(item.current for item in solved.flows if item.equipment_id == "slack" and item.phase == "A"))
    return current, solved.status


def _volt_var_power(rated: float, curve: list[tuple[float, float]]) -> tuple[float, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    builder = NetBuilder("vv", "VoltVar", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 0.001, 0.001)
    builder.inverter("src", s_rated_va=rated, p_w=10_000.0, q_var=0.0, volt_var=curve, inverter_id="pv")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status
    reactive = sum(item.q_into_var for item in solved.flows if item.equipment_id == "pv" and item.phase in "ABC")
    return reactive, solved.status


def _volt_watt_power(requested_w: float, curve: list[tuple[float, float]]) -> tuple[float, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    builder = NetBuilder("vw", "VoltWatt", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 0.001, 0.001)
    builder.inverter("src", s_rated_va=100_000.0, p_w=requested_w, q_var=0.0, volt_watt=curve, inverter_id="pv")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status
    active = -sum(item.p_into_w for item in solved.flows if item.equipment_id == "pv" and item.phase in "ABC")
    return active, solved.status


def _priority_power(active_w: float, reactive_var: float) -> tuple[float, str, float]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    builder = NetBuilder("pq", "Приоритет", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 0.01, 0.02)
    builder.load("src", active_w, reactive_var, load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return 0.0, solved.status, 1.0
    measured = -sum(item.p_into_w for item in solved.flows if item.equipment_id == "source" and item.phase in "ABC")
    return measured, solved.status, abs(measured - active_w) / max(active_w, 1.0)


def _bus_power(active_w: float) -> tuple[float, str, float]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    builder = NetBuilder("cold", "Холодный", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 0.01, 0.02)
    builder.load("src", active_w, 0.0, load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return 0.0, solved.status, 1.0
    measured = -sum(item.p_into_w for item in solved.flows if item.equipment_id == "source" and item.phase in "ABC")
    return measured, solved.status, abs(measured - active_w) / active_w


def _impedance_current(resistance: float, reactance: float) -> tuple[float, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    impedance = resistance + 1j * reactance
    power = 3.0 * (LN**2 / impedance.conjugate())
    builder = NetBuilder("wave", "Огибающая", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 1.0e-6, 1.0e-6)
    builder.load("src", float(power.real), float(power.imag), model="constant_z", load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status
    current = abs(next(item.current for item in solved.flows if item.equipment_id == "load" and item.phase == "A"))
    return current, solved.status


def _source_pu(scale: float) -> tuple[float, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("uv", "Напряжение", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 0.001, 0.001, ll_v=scale * 10_000.0)
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status
    bus = compile_topology(network).node_to_bus["src"]
    voltage = next(item for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    return abs(voltage.complex) / voltage.v_base_ln_v, solved.status
