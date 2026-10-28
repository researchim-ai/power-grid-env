"""Эквивалент двигателя как постоянный Z и сверка с фазным расчётом.

Скольжение берётся из схемы за сопротивлением сети. Импеданс R/s + jX
переводится в мощность при номинальном напряжении. Это не пуск с вытеснением тока.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from power_grid_env.dynamics import coupled_induction, feeder_restart

VOLTAGE_PU = 1e-5


def three_phase_power(impedance: complex, voltage_ln_v: float) -> tuple[float, float]:
    """P и Q трёх фаз, которым соответствует заданный импеданс при номинальном фазном напряжении."""
    if voltage_ln_v <= 0 or abs(impedance) == 0:
        raise ValueError("напряжение и импеданс двигателя должны быть ненулевыми")
    phase = voltage_ln_v**2 / np.conj(impedance)
    return float(3.0 * phase.real), float(3.0 * phase.imag)


def qualify_motor_on_feeder() -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.checks import kcl_report
    from power_grid_env.topology import compile_topology

    voltage_ln = 10_000.0 / math.sqrt(3.0)
    source = 0.05 + 0.2j
    line = 0.4 + 0.8j
    thevenin = source + line
    rotor_r = 15.0
    reactance = 80.0
    torque = 8_000.0
    motor = coupled_induction(voltage_ln + 0.0j, thevenin, rotor_r, reactance, torque)
    if motor["stalled"]:
        return {"verdict": "fail", "reasons": ["stalled"], "evidence": "motor_impedance"}
    impedance = rotor_r / float(motor["slip"]) + 1j * reactance
    active, reactive = three_phase_power(impedance, voltage_ln)
    builder = NetBuilder("motor-z", "Двигатель", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", source.real, source.imag)
    builder.line_z1("src", "ld", line, line_id="line")
    builder.load("ld", active, reactive, model="constant_z", load_id="motor")
    network = builder.build()
    network.sources[0].r0_ohm = source.real
    network.sources[0].x0_ohm = source.imag
    solved = solve_network(network)
    bus = compile_topology(network).node_to_bus["ld"]
    actual = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    expected = motor["voltage"]
    voltage_error = abs(abs(actual) - abs(expected)) / voltage_ln
    angle_error = abs(float(np.angle(actual / expected)))
    second = {
        "id": "m2",
        "priority": 2,
        "rotor_r": rotor_r,
        "reactance": reactance,
        "load_torque": torque,
    }
    first = {**second, "id": "m1", "priority": 1}
    group = feeder_restart(voltage_ln + 0.0j, thevenin, [first, second], minimum_v=voltage_ln * 0.8)
    reasons = []
    if solved.status != "converged":
        reasons.append("status")
    if voltage_error > VOLTAGE_PU:
        reasons.append("voltage")
    if angle_error > 1e-4:
        reasons.append("angle")
    if kcl_report(network, solved)["mismatch_pu"] > 1e-8:
        reasons.append("kcl")
    if group["started"] != ["m1", "m2"]:
        reasons.append("group")
    pair_error = _pair_voltage_error(voltage_ln, thevenin, group, rotor_r, reactance, torque)
    if pair_error > VOLTAGE_PU:
        reasons.append("two_motors")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "evidence": "phase_solver",
        "voltage_error_pu": voltage_error,
        "angle_error_rad": angle_error,
        "pair_voltage_error_pu": pair_error,
        "slip": motor["slip"],
        "status": solved.status,
    }


def _pair_voltage_error(voltage_ln, thevenin, group, rotor_r, reactance, torque) -> float:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    powers = []
    for name in ("m1", "m2"):
        impedance = rotor_r / group["slips"][name] + 1j * reactance
        powers.append(three_phase_power(impedance, voltage_ln))
    builder = NetBuilder("two-motors", "Два двигателя", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", thevenin.real, thevenin.imag)
    builder.load("src", powers[0][0], powers[0][1], model="constant_z", load_id="m1")
    builder.load("src", powers[1][0], powers[1][1], model="constant_z", load_id="m2")
    network = builder.build()
    network.sources[0].r0_ohm = thevenin.real
    network.sources[0].x0_ohm = thevenin.imag
    solved = solve_network(network)
    if solved.status != "converged":
        return 1.0
    bus = compile_topology(network).node_to_bus["src"]
    actual = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    return abs(abs(actual) - abs(group["voltage"])) / voltage_ln
