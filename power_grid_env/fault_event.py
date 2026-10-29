"""Одно событие: КЗ с вкладом двигателя, отключение и срыв момента.

Сеть считает два источника: сеть и ЭДС двигателя за сопротивлением заторможенного
ротора. Формула IEC для глухого КЗ на выводах считается отдельно и с этим током
не смешивается. Натурным измерением событие не является.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from power_grid_env.dynamics import induction_equilibrium_slip
from power_grid_env.iec60909 import induction_motor_terminal_current, voltage_factor
from power_grid_env.protection import clearing_times, critical_stays_supplied, trip_from_secondary

VOLTAGE_PU = 1e-5
ANGLE_RAD = 1e-4
CURRENT_RELATIVE = 1e-5
ROTOR_R = 15.0
ROTOR_X = 80.0
LOCKED = 1.5 + 6.0j
SOURCE_Z = 0.05 + 0.2j
LINE_Z = 0.3 + 0.6j
FAULT_Z = 1.0 + 0.0j


def _network():
    from power_grid_env.build import NetBuilder
    from power_grid_env.model import StudyFault

    builder = NetBuilder("fault-event", "КЗ с двигателем", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", SOURCE_Z.real, SOURCE_Z.imag, source_id="grid")
    builder.line_z1("src", "ld", LINE_Z, line_id="line")
    builder.source("ld", LOCKED.real, LOCKED.imag, source_id="motor", is_slack=False)
    network = builder.build()
    for source in network.sources:
        source.r0_ohm = source.r_ohm
        source.x0_ohm = source.x_ohm
    network.study_faults.append(
        StudyFault(id="fault", bus_id="ld", kind="3ph", phases=["A", "B", "C"], r_ohm=FAULT_Z.real)
    )
    return network


def _bus_voltage(vln: float, with_motor: bool) -> complex:
    thevenin = SOURCE_Z + LINE_Z
    admit = 1.0 / thevenin + 1.0 / FAULT_Z
    inject = vln / thevenin
    if with_motor:
        admit += 1.0 / LOCKED
        inject += vln / LOCKED
    return inject / admit


def reference(vln: float) -> dict[str, complex | float]:
    voltage = _bus_voltage(vln, True)
    without = _bus_voltage(vln, False)
    motor_current = (vln - voltage) / LOCKED
    fault_current = voltage / FAULT_Z
    grid_current = (vln - voltage) / (SOURCE_Z + LINE_Z)
    return {
        "voltage": voltage,
        "fault_current": fault_current,
        "motor_current": motor_current,
        "grid_current": grid_current,
        "fault_without_motor": without / FAULT_Z,
        "balance": grid_current + motor_current - fault_current,
    }


def qualify() -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.checks import kcl_report
    from power_grid_env.topology import compile_topology

    network = _network()
    solved = solve_network(network)
    vln = 10_000.0 / math.sqrt(3.0)
    expected = reference(vln)
    topology = compile_topology(network)
    bus = topology.node_to_bus["ld"]
    actual = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    fault = next(item.current for item in solved.flows if item.equipment_id == "fault" and item.phase == "A")
    motor = next(item.current for item in solved.flows if item.equipment_id == "motor" and item.phase == "A")
    voltage_error = abs(abs(actual) - abs(expected["voltage"])) / vln
    angle_error = abs(float(np.angle(actual / expected["voltage"])))
    fault_error = abs(fault - expected["fault_current"]) / max(1.0, abs(expected["fault_current"]))
    # Ток в элемент источника противоположен току из ЭДС в сеть.
    motor_error = abs(motor - (-expected["motor_current"])) / max(1.0, abs(expected["motor_current"]))
    reasons = []
    if solved.status != "converged":
        reasons.append("status")
    if abs(expected["balance"]) > 1e-6:
        reasons.append("formula_balance")
    if voltage_error > VOLTAGE_PU:
        reasons.append("voltage")
    if angle_error > ANGLE_RAD:
        reasons.append("angle")
    if fault_error > CURRENT_RELATIVE or motor_error > CURRENT_RELATIVE:
        reasons.append("current")
    if abs(expected["fault_current"]) <= abs(expected["fault_without_motor"]):
        reasons.append("motor_did_not_feed")
    if kcl_report(network, solved)["mismatch_pu"] > 1e-8:
        reasons.append("kcl")
    iec = induction_motor_terminal_current(10_000.0, voltage_factor(10_000.0, maximum=True), LOCKED)
    iec_gap = abs(abs(iec) - abs(expected["motor_current"])) / abs(expected["motor_current"])
    if iec_gap < 0.02:
        reasons.append("iec_collapsed_into_network")
    fault_amp = abs(expected["fault_current"])
    order = clearing_times(fault_amp, [("near", 100.0, "definite", 0.1), ("far", 100.0, "definite", 0.4)])
    if not (order[0][0] == "near" and order[0][1] < order[1][1]):
        reasons.append("clearing")
    measurement = trip_from_secondary(fault_amp, 2000.0, 5.0, 2.5, available=True)
    blind = trip_from_secondary(fault_amp, 2000.0, 5.0, 2.5, available=False)
    if not measurement["trip"] or blind["trip"]:
        reasons.append("ct")
    supplied = critical_stays_supplied("near", {"near": {"faulted"}, "far": {"critical", "faulted"}}, "critical")
    if not supplied:
        reasons.append("critical")
    prefault_peak = vln**2 / (2.0 * ROTOR_X)
    fault_peak = abs(expected["voltage"]) ** 2 / (2.0 * ROTOR_X)
    torque = 0.5 * (prefault_peak + fault_peak)
    try:
        induction_equilibrium_slip(vln, ROTOR_R, ROTOR_X, torque)
        held = True
    except ValueError:
        held = False
    try:
        induction_equilibrium_slip(abs(expected["voltage"]), ROTOR_R, ROTOR_X, torque)
        stalled = False
    except ValueError:
        stalled = True
    if not held or not stalled:
        reasons.append("stall")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "evidence": "two_source_mna",
        "not_iec_60909_network": True,
        "not_a_field_measurement": True,
        "field_status": "waiting_for_data",
        "voltage_error_pu": voltage_error,
        "fault_current_error": fault_error,
        "motor_current_error": motor_error,
        "iec_gap": iec_gap,
        "fault_current_a": fault_amp,
        "status": solved.status,
    }
