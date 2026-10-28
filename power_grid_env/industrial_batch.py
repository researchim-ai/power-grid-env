"""Десять отдельных проверок. Совпадение с OpenDSS здесь — режим повреждённой сети,
а не методика IEC 60909. Короткое окно энергии не является горизонтом 7 или 30 суток.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from power_grid_env.controls import (
    capacitor_next_steps,
    integrate_temperature,
    ltc_next_tap,
    resonance_hz,
    series_rlc_impedance,
    steady_temperature,
    temperature_constant_r,
    triplen_neutral,
)
from power_grid_env.conventions import ll_to_ln
from power_grid_env.protection import RelayState, directional_allows, iec_operate_time
from power_grid_env.reference import line_to_line_current, llg_phase_currents, thevenin_fault_current

VOLTAGE_PU = 1e-5
ANGLE_RAD = 1e-4
CURRENT_RELATIVE = 1e-5
POWER_RELATIVE = 1e-5
POWER_FLOOR_W = 1e-3


def _wrap(value: float) -> float:
    return float(np.angle(np.exp(1j * value)))


def _verdict(reasons: list[str], **payload: Any) -> dict[str, Any]:
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, **payload}


def _ltc_network(target: float, deadband: float, tap: float, tap_max: float):
    from power_grid_env.build import NetBuilder
    from power_grid_env.model import LTCSpec

    builder = NetBuilder("ltc-law", "РПН", 20.0)
    builder.node("hv", 20.0)
    builder.node("lv", 0.4)
    builder.source("hv", 0.05, 0.2)
    builder.transformer(
        "hv",
        "lv",
        vector_group="Dyn11",
        kva=160,
        hv_kv=20.0,
        lv_kv=0.4,
        x_pct=4.0,
        r_pct=1.0,
        hv_ground="not_applicable",
        lv_ground="solid",
        transformer_id="t",
    )
    builder.load("lv", 20_000.0, 5_000.0, load_id="load")
    network = builder.build()
    network.transformers[0].windings[1].tap_pu = tap
    network.transformers[0].ltc = LTCSpec(
        controlled_winding=1,
        v_target_pu=target,
        deadband_pu=deadband,
        tap_min_pu=0.9,
        tap_max_pu=tap_max,
        tap_step_pu=0.01,
        delay_s=5.0,
    )
    return network


def qualify_ltc() -> dict[str, Any]:
    """Одна ступень после выдержки, тишина внутри зоны и упор в предел."""
    from power_grid_env.scenarios import Scenario
    from power_grid_env.simulation import Engine

    scenario = Scenario(id="ltc-law", horizon_s=30, settle_s=600, control_interval_s=30)
    cases = {}
    for name, target, deadband, tap, tap_max in (
        ("raise", 1.2, 0.01, 1.0, 1.1),
        ("hold", 1.0, 0.5, 1.0, 1.1),
        ("limit", 1.2, 0.01, 1.1, 1.1),
    ):
        engine = Engine(_ltc_network(target, deadband, tap, tap_max), scenario)
        engine.reset()
        measured = engine._controlled_voltage_pu(engine.net.transformers[0])
        engine.advance_to(4.0)
        before = engine.net.transformers[0].windings[1].tap_pu
        engine.advance_to(5.0)
        after = engine.net.transformers[0].windings[1].tap_pu
        expected = ltc_next_tap(
            measured,
            target_pu=target,
            deadband_pu=deadband,
            tap_pu=tap,
            step_pu=0.01,
            tap_min_pu=0.9,
            tap_max_pu=tap_max,
        )
        cases[name] = {"before": before, "after": after, "expected": expected, "measured": measured}
    reasons = []
    if cases["raise"]["before"] != 1.0 or abs(cases["raise"]["after"] - cases["raise"]["expected"]) > 1e-12:
        reasons.append("raise")
    if abs(cases["raise"]["measured"] - 1.2) <= 0.01:
        reasons.append("raise_not_outside")
    if cases["hold"]["after"] != 1.0 or cases["hold"]["expected"] != 1.0:
        reasons.append("hold")
    if cases["limit"]["after"] != 1.1 or cases["limit"]["expected"] != 1.1:
        reasons.append("limit")
    return _verdict(reasons, evidence="discrete_law", cases={key: item["after"] for key, item in cases.items()})


def qualify_capacitor() -> dict[str, Any]:
    """Оба положения батареи против узловой схемы. Гистерезис не дребезжит внутри окна."""
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("cap", "Конденсатор", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.05, 0.2)
    builder.line_z1("src", "ld", 1.0 + 2.0j, line_id="line")
    builder.load("ld", 100_000.0, 20_000.0, model="constant_z", load_id="load")
    builder.shunt("ld", 200.0, shunt_id="cap")
    network = builder.build()
    network.shunts[0].steps = 1
    vln = network.sources[0].ll_v / math.sqrt(3.0)
    emf = vln
    series = (network.sources[0].r_ohm + 1j * network.sources[0].x_ohm) + (1.0 + 2.0j)
    power = (100_000.0 + 20_000.0j) / 3.0
    admit_load = np.conj(power) / vln**2
    errors = []
    voltages = {}
    for steps in (0, 1):
        edited = network.model_copy(deep=True)
        edited.shunts[0].steps_in = steps
        solved = solve_network(edited)
        susceptance = edited.shunts[0].b_siemens_per_phase * steps
        expected = emf / (1.0 + series * (admit_load + 1j * susceptance))
        bus = compile_topology(edited).node_to_bus["ld"]
        actual = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
        errors.append(abs(abs(actual) - abs(expected)) / vln)
        errors.append(0.0 if abs(expected) < 1.0 else abs(_wrap(np.angle(actual) - np.angle(expected))))
        voltages[steps] = abs(actual) / vln
        if solved.status != "converged":
            errors.append(1.0)
    held = capacitor_next_steps(1.0, 1, 1, close_below_pu=0.97, open_above_pu=1.03)
    closed = capacitor_next_steps(0.96, 0, 1, close_below_pu=0.97, open_above_pu=1.03)
    opened = capacitor_next_steps(1.04, 1, 1, close_below_pu=0.97, open_above_pu=1.03)
    reasons = []
    if max(errors[0::2]) > VOLTAGE_PU or max(errors[1::2]) > ANGLE_RAD:
        reasons.append("voltage")
    if not voltages[1] > voltages[0]:
        reasons.append("capacitor_did_not_raise_voltage")
    if held != 1 or closed != 1 or opened != 0:
        reasons.append("hysteresis")
    try:
        capacitor_next_steps(1.0, 0, 1, close_below_pu=1.0, open_above_pu=1.0)
    except ValueError:
        pass
    else:
        reasons.append("degenerate_hysteresis_accepted")
    return _verdict(reasons, evidence="independent_mna", max_error=max(errors), voltage_pu=voltages)


def qualify_thermal() -> dict[str, Any]:
    """Закрытая формула, стационар с α и отсутствие R(T) в стационарном режиме."""
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    analytic = temperature_constant_r(60.0, 20.0, 15.0, 100.0, 0.2, 0.05, 2000.0)
    stepped = integrate_temperature(60.0, 0.1, 20.0, 15.0, 100.0, 0.2, 0.0, 20.0, 0.05, 2000.0)
    tau = 0.05 * 2000.0
    rise = abs(analytic - 15.0) + 1.0
    steady = steady_temperature(100.0, 0.2, 0.004, 20.0, 15.0, 0.05)
    linearized = 2000.0 / (1.0 / 0.05 - (100.0**2) * 0.2 * 0.004)
    long_run = integrate_temperature(20.0 * linearized, 1.0, 20.0, 15.0, 100.0, 0.2, 0.004, 20.0, 0.05, 2000.0)
    builder = NetBuilder("thermal", "Тепло", 10.0)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.05, 0.2)
    builder.line_z1("src", "ld", 0.3 + 0.4j, line_id="line")
    builder.load("ld", 50_000.0, 0.0, model="constant_z", load_id="load")
    cold = builder.build()
    hot = cold.model_copy(deep=True)
    hot.lines[0].temperature_c = 80.0
    hot.lines[0].resistance_alpha_per_c = 0.004
    cold_solved = solve_network(cold)
    hot_solved = solve_network(hot)
    bus = compile_topology(cold).node_to_bus["ld"]
    cold_v = next(item.complex for item in cold_solved.voltages if item.bus_id == bus and item.phase == "A")
    hot_v = next(item.complex for item in hot_solved.voltages if item.bus_id == bus and item.phase == "A")
    reasons = []
    if abs(stepped - analytic) / rise > 2.0 * 0.1 / tau:
        reasons.append("integrator")
    if abs(long_run - steady) > 1e-3:
        reasons.append("steady")
    if abs(cold_v - hot_v) / ll_to_ln(10_000.0) > 1e-12:
        reasons.append("resistance_changed_in_power_flow")
    try:
        steady_temperature(10_000.0, 0.2, 0.004, 20.0, 15.0, 0.05)
    except ValueError:
        pass
    else:
        reasons.append("runaway_not_rejected")
    return _verdict(
        reasons,
        evidence="analytic_thermal",
        integrator_error_c=abs(stepped - analytic),
        steady_error_c=abs(long_run - steady),
        power_flow_delta_pu=abs(cold_v - hot_v) / ll_to_ln(10_000.0),
    )


def _active(solved, equipment_id: str) -> float:
    return sum(flow.p_into_w for flow in solved.flows if flow.equipment_id == equipment_id and flow.phase in "ABC")


def qualify_energy() -> dict[str, Any]:
    """Два установившихся интервала. Это не расчёт 7 или 30 суток."""
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.scenarios import Scenario
    from power_grid_env.simulation import Engine
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("energy", "Энергия", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.05, 0.2)
    builder.line_z1("src", "ld", 0.4 + 0.8j, line_id="line")
    builder.load("ld", 80_000.0, 10_000.0, model="constant_z", load_id="load")
    network = builder.build()

    def scaled(multiplier: float):
        edited = network.model_copy(deep=True)
        for phase in edited.loads[0].p_w:
            edited.loads[0].p_w[phase] *= multiplier
            edited.loads[0].q_var[phase] *= multiplier
        return edited

    cold = {1.0: solve_network(scaled(1.0)), 1.5: solve_network(scaled(1.5))}
    engine = Engine(
        network,
        Scenario(
            id="energy",
            horizon_s=60,
            settle_s=600,
            control_interval_s=60,
            load_profile={"load": [[0.0, 1.0], [30.0, 1.5]]},
        ),
    )
    engine.reset()
    first = engine.last_result
    engine.advance_to(30.0)
    second = engine.last_result
    reasons = []
    samples = []
    for multiplier, solved, live in ((1.0, cold[1.0], first), (1.5, cold[1.5], second)):
        if solved.status != "converged" or live is None or live.status != "converged":
            reasons.append("status")
            continue
        bus = compile_topology(network).node_to_bus["ld"]
        expected = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
        actual = next(item.complex for item in live.voltages if item.bus_id == bus and item.phase == "A")
        if abs(abs(actual) - abs(expected)) / ll_to_ln(10_000.0) > VOLTAGE_PU:
            reasons.append(f"voltage_{multiplier}")
        injected = -_active(solved, "source")
        consumed = _active(solved, "load")
        loss = _active(solved, "line")
        mismatch = abs(injected - consumed - loss)
        tolerance = max(POWER_FLOOR_W, POWER_RELATIVE * network.s_base_va)
        if mismatch > tolerance:
            reasons.append(f"balance_{multiplier}")
        samples.append((injected, consumed, loss, mismatch))
    if len(samples) == 2:
        energy_j = (samples[0][0] + samples[1][0]) * 30.0
        load_j = (samples[0][1] + samples[1][1]) * 30.0
        loss_j = (samples[0][2] + samples[1][2]) * 30.0
        if abs(energy_j - load_j - loss_j) > 1.0:
            reasons.append("interval_energy")
    else:
        energy_j = load_j = loss_j = 0.0
    return _verdict(
        reasons,
        evidence="two_interval_balance",
        horizon_s=60,
        not_a_week=True,
        source_energy_j=energy_j,
        load_energy_j=load_j,
        loss_energy_j=loss_j,
    )


def _fault_network(kind: str, phases: list[str], z0: complex, z_fault: float):
    from power_grid_env.build import NetBuilder
    from power_grid_env.model import StudyFault

    builder = NetBuilder(kind, kind, 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 0.05, 0.4, source_id="source")
    network = builder.build()
    network.sources[0].r0_ohm = z0.real
    network.sources[0].x0_ohm = z0.imag
    network.study_faults.append(StudyFault(id="fault", bus_id="src", kind=kind, phases=phases, r_ohm=z_fault))
    return network


def _fault_flow(solved, phase: str) -> complex:
    found = [flow.current for flow in solved.flows if flow.equipment_id == "fault" and flow.phase == phase]
    if len(found) != 1:
        raise RuntimeError(f"ток КЗ фазы {phase} не единственный: {len(found)}")
    return found[0]


def qualify_slg() -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network

    z1 = 0.05 + 0.4j
    z0 = 0.2 + 1.2j
    z_fault = 1.0
    network = _fault_network("slg", ["A"], z0, z_fault)
    solved = solve_network(network)
    expected = thevenin_fault_current(ll_to_ln(10_000.0), z1, z1, z0, z_fault + 0j, "slg")
    actual = _fault_flow(solved, "A")
    return _sequence_verdict(solved, actual, expected, "faulted_operating_point")


def qualify_ll() -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network

    z1 = 0.05 + 0.4j
    network = _fault_network("ll", ["A", "B"], z1, 1.0)
    solved = solve_network(network)
    expected = line_to_line_current(ll_to_ln(10_000.0), z1, z1, 1.0 + 0j)
    actual = _fault_flow(solved, "A")
    return _sequence_verdict(solved, actual, expected, "faulted_operating_point")


def qualify_llg() -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network

    z1 = 0.05 + 0.4j
    z0 = 0.2 + 1.2j
    network = _fault_network("llg", ["B", "C"], z0, 1.0)
    solved = solve_network(network)
    expected = llg_phase_currents(ll_to_ln(10_000.0), z1, z1, z0, 1.0 + 0j)
    reasons = []
    errors = []
    if solved.status != "converged":
        reasons.append("status")
    healthy = sum(flow.current for flow in solved.flows if flow.equipment_id == "source" and flow.phase == "A")
    if abs(healthy) > 1e-4:
        reasons.append("healthy_phase")
    for phase, value in zip("BC", expected[1:], strict=True):
        actual = _fault_flow(solved, phase)
        errors.append(abs(actual - value) / max(1.0, abs(value)))
        if abs(value) > 1.0 and abs(_wrap(np.angle(actual) - np.angle(value))) > ANGLE_RAD:
            reasons.append(f"angle_{phase}")
    if any(error > CURRENT_RELATIVE for error in errors):
        reasons.append("current")
    return _verdict(
        reasons,
        evidence="faulted_operating_point",
        method="sequence_network",
        not_iec_60909=True,
        max_current_error=max(errors) if errors else None,
        status=solved.status,
    )


def _sequence_verdict(solved, actual: complex, expected: complex, evidence: str) -> dict[str, Any]:
    reasons = []
    if solved.status != "converged":
        reasons.append("status")
    relative = abs(actual - expected) / max(1.0, abs(expected))
    if relative > CURRENT_RELATIVE:
        reasons.append("current")
    if abs(expected) > 1.0 and abs(_wrap(np.angle(actual) - np.angle(expected))) > ANGLE_RAD:
        reasons.append("angle")
    return _verdict(
        reasons,
        evidence=evidence,
        method="sequence_network",
        not_iec_60909=True,
        max_current_error=relative,
        status=solved.status,
    )


def qualify_selectivity() -> dict[str, Any]:
    """Ниже уставки тишина. Ближнее реле раньше дальнего. Отказ ближнего отпускает дальнее."""
    downstream = iec_operate_time(4.0, 1.0, "standard_inverse", 0.2)
    upstream = iec_operate_time(4.0, 1.0, "standard_inverse", 0.5)
    near = RelayState("near", "overcurrent", "sw-near", 1.0, "definite", 0.2)
    far = RelayState("far", "overcurrent", "sw-far", 1.0, "definite", 0.5)
    quiet = RelayState("quiet", "overcurrent", "sw-near", 1.0, "definite", 0.2)
    blocked = RelayState("blocked", "overcurrent", "sw-near", 1.0, "definite", 0.2, lockout=True)
    backup = RelayState("backup", "overcurrent", "sw-far", 1.0, "definite", 0.5)
    near_trip = far_trip = None
    for step in range(1, 21):
        if near_trip is None and near.integrate(4.0, 0.05) == "trip":
            near_trip = step * 0.05
        if far_trip is None and far.integrate(4.0, 0.05) == "trip":
            far_trip = step * 0.05
        quiet.integrate(0.5, 0.05)
    backup_trip = None
    for step in range(1, 21):
        blocked.integrate(4.0, 0.05)
        if backup_trip is None and backup.integrate(4.0, 0.05) == "trip":
            backup_trip = step * 0.05
    reasons = []
    if quiet.tripped or quiet.accumulator_s != 0.0:
        reasons.append("below_pickup")
    if near_trip is None or far_trip is None or not near_trip < far_trip:
        reasons.append("order")
    if abs((near_trip or 0) - 0.2) > 0.05 + 1e-9 or abs((far_trip or 0) - 0.5) > 0.05 + 1e-9:
        reasons.append("time")
    if blocked.tripped or backup_trip is None or abs(backup_trip - 0.5) > 0.05 + 1e-9:
        reasons.append("failed_breaker")
    if not downstream < upstream:
        reasons.append("iec_order")
    return _verdict(
        reasons,
        evidence="iec_and_definite",
        near_s=near_trip,
        far_s=far_trip,
        backup_s=backup_trip,
        iec_near_s=downstream,
        iec_far_s=upstream,
    )


def qualify_directional() -> dict[str, Any]:
    voltage = 1000.0 + 0.0j
    reverse = -10.0 + 0.0j
    forward = 10.0 + 0.0j
    relay = RelayState("dir", "overcurrent", "line", 1.0, "definite", 0.1, directional=True)
    blocked = False
    for _ in range(10):
        if relay.integrate(20.0, 0.05, voltage, reverse) == "trip":
            blocked = True
    forward_relay = RelayState("dir-f", "overcurrent", "line", 1.0, "definite", 0.1, directional=True)
    tripped = None
    for step in range(1, 10):
        if forward_relay.integrate(20.0, 0.05, voltage, forward) == "trip":
            tripped = step * 0.05
            break
    reasons = []
    if not directional_allows(voltage, forward, forward=True):
        reasons.append("forward_polarity")
    if directional_allows(voltage, reverse, forward=True):
        reasons.append("reverse_polarity")
    if relay.tripped or blocked:
        reasons.append("reverse_tripped")
    if tripped is None or abs(tripped - 0.1) > 0.05 + 1e-9:
        reasons.append("forward_time")
    return _verdict(reasons, evidence="power_polarity", forward_trip_s=tripped)


def qualify_three_winding() -> dict[str, Any]:
    """YNyn0yn0. Сопротивления пар X переводятся в звезду и приводятся к базе ВН.

    Проценты R заданы на каждой обмотке. Общий %loadloss сюда не подмешивается:
    его внутреннее распределение OpenDSS этим эталоном не считается.
    """
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.checks import kcl_report
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("three", "Три обмотки", 110.0, s_base_va=10.0e6)
    builder.node("hv", 110.0)
    builder.node("mv", 10.0)
    builder.node("tv", 10.0)
    builder.source("hv", 1.0, 4.0)
    builder.transformer(
        "hv",
        "mv",
        vector_group="YNyn0yn0",
        kva=10000,
        hv_kv=110.0,
        lv_kv=10.0,
        x_pct=10.0,
        r_pct=0.0,
        x_ht_pct=12.0,
        x_lt_pct=8.0,
        hv_ground="solid",
        lv_ground="solid",
        tertiary=("tv", 10.0, 10000, "solid"),
        transformer_id="t",
    )
    builder.load("mv", 1_000_000.0, 200_000.0, model="constant_z", load_id="mv")
    builder.load("tv", 500_000.0, 0.0, model="constant_z", load_id="tv")
    network = builder.build()
    transformer = network.transformers[0]
    transformer.ppm_antifloat = 0.0
    transformer.load_loss_pct = 0.0
    transformer.windings[0].r_pct = 0.4
    transformer.windings[1].r_pct = 0.35
    transformer.windings[2].r_pct = 0.25
    network.sources[0].r0_ohm = 1.0
    network.sources[0].x0_ohm = 4.0
    solved = solve_network(network)
    reference = _three_winding_star(network)
    topology = compile_topology(network)
    reasons = []
    voltage_errors = []
    angle_errors = []
    for node_id, expected in reference["voltage"].items():
        bus_id = topology.node_to_bus[node_id]
        actual = next(item.complex for item in solved.voltages if item.bus_id == bus_id and item.phase == "A")
        base = reference["vln"][node_id]
        voltage_errors.append(abs(abs(actual) - abs(expected)) / base)
        angle_errors.append(abs(_wrap(np.angle(actual) - np.angle(expected))))
    bus_hv = topology.node_to_bus["hv"]
    actual_i = next(
        item.current
        for item in solved.flows
        if item.equipment_id == "t" and item.bus_id == bus_hv and item.phase == "A"
    )
    current_error = abs(actual_i - reference["current_hv"]) / max(1.0, abs(reference["current_hv"]))
    loss = sum(flow.p_into_w for flow in solved.flows if flow.equipment_id == "t")
    loss_error = abs(loss - reference["loss_w"])
    if solved.status != "converged":
        reasons.append("status")
    if max(voltage_errors) > VOLTAGE_PU:
        reasons.append("voltage")
    if max(angle_errors) > ANGLE_RAD:
        reasons.append("angle")
    if current_error > CURRENT_RELATIVE:
        reasons.append("current")
    if loss_error > max(POWER_FLOOR_W, POWER_RELATIVE * network.s_base_va):
        reasons.append("losses")
    if kcl_report(network, solved)["mismatch_pu"] > 1e-8:
        reasons.append("kcl")
    return _verdict(
        reasons,
        evidence="star_equivalent",
        max_voltage_error_pu=max(voltage_errors) if voltage_errors else None,
        max_angle_error_rad=max(angle_errors) if angle_errors else None,
        max_current_error=current_error,
        loss_error_w=loss_error,
        status=solved.status,
    )


def _three_winding_star(network) -> dict[str, Any]:
    transformer = network.transformers[0]
    windings = transformer.windings
    rated = {item.rated_va for item in windings}
    if len(rated) != 1:
        raise ValueError("звезда здесь приведена к одной мощности всех обмоток")
    power_base = windings[0].rated_va
    voltage = [item.nominal_ll_v for item in windings]
    zbase = voltage[0] ** 2 / power_base
    pair_x = (transformer.x_hl_pct, transformer.x_ht_pct, transformer.x_lt_pct)
    star_x = (
        0.5 * (pair_x[0] + pair_x[1] - pair_x[2]),
        0.5 * (pair_x[0] + pair_x[2] - pair_x[1]),
        0.5 * (pair_x[1] + pair_x[2] - pair_x[0]),
    )
    branch = [((item.r_pct or 0.0) + 1j * reactance) / 100.0 * zbase for item, reactance in zip(windings, star_x, strict=True)]
    vln = [item / math.sqrt(3.0) for item in voltage]
    ratio = [item / vln[0] for item in vln]
    source = network.sources[0]
    emf = vln[0] * np.exp(1j * source.angle_a_rad)
    z_source = source.r_ohm + 1j * source.x_ohm
    loads = {item.id: item for item in network.loads}
    admit = []
    for index, load_id in ((1, "mv"), (2, "tv")):
        load = loads[load_id]
        power = (sum(load.p_w.values()) + 1j * sum(load.q_var.values())) / 3.0
        z_winding = vln[index] ** 2 / np.conj(power)
        admit.append((ratio[index] ** 2) / z_winding)
    unknown = np.zeros((4, 4), dtype=np.complex128)
    rhs = np.zeros(4, dtype=np.complex128)
    unknown[0, 0] = 1.0 / z_source + 1.0 / branch[0]
    unknown[0, 3] = -1.0 / branch[0]
    rhs[0] = emf / z_source
    unknown[1, 1] = 1.0 / branch[1] + admit[0]
    unknown[1, 3] = -1.0 / branch[1]
    unknown[2, 2] = 1.0 / branch[2] + admit[1]
    unknown[2, 3] = -1.0 / branch[2]
    for index in range(3):
        unknown[3, index] = 1.0 / branch[index]
    unknown[3, 3] = -sum(1.0 / item for item in branch)
    solved = np.linalg.solve(unknown, rhs)
    current = [(solved[index] - solved[3]) / branch[index] for index in range(3)]
    loss = 3.0 * sum(float(np.real(branch[index]) * abs(current[index]) ** 2) for index in range(3))
    return {
        "voltage": {"hv": solved[0], "mv": solved[1] * ratio[1], "tv": solved[2] * ratio[2]},
        "vln": {"hv": vln[0], "mv": vln[1], "tv": vln[2]},
        "current_hv": current[0],
        "loss_w": loss,
    }


def qualify_capacitor_engine() -> dict[str, Any]:
    """Ступень конденсатора в Engine после выдержки. Снимок сохраняет ожидание."""
    import json

    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.scenarios import Scenario
    from power_grid_env.simulation import Engine
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("cap-engine", "Батарея", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.05, 0.2)
    builder.line_z1("src", "ld", 1.0 + 2.0j, line_id="line")
    builder.load("ld", 200_000.0, 40_000.0, model="constant_z", load_id="load")
    builder.shunt("ld", 400.0, shunt_id="cap")
    network = builder.build()
    network.shunts[0].steps = 1
    network.shunts[0].steps_in = 0
    control = {
        "shunt_id": "cap",
        "measured_node_id": "ld",
        "close_below_pu": 1.0,
        "open_above_pu": 1.05,
        "delay_s": 5.0,
    }
    engine = Engine(
        network,
        Scenario(id="cap-engine", horizon_s=30, settle_s=600, control_interval_s=30, capacitor_controls=[control]),
    )
    engine.reset()
    measured = engine._node_voltage_pu("ld")
    engine.advance_to(4.0)
    before = engine.net.shunts[0].steps_in
    snapshot = json.loads(json.dumps(engine.snapshot()))
    engine.advance_to(5.0)
    after = engine.net.shunts[0].steps_in
    expected = capacitor_next_steps(
        measured,
        0,
        1,
        close_below_pu=control["close_below_pu"],
        open_above_pu=control["open_above_pu"],
    )
    restored = Engine(network.model_copy(deep=True), engine.scenario)
    restored.restore(snapshot)
    restored.advance_to(5.0)
    held = Engine(
        network.model_copy(deep=True),
        Scenario(
            id="cap-hold",
            horizon_s=30,
            settle_s=600,
            control_interval_s=30,
            capacitor_controls=[{**control, "close_below_pu": 0.5, "open_above_pu": 1.5}],
        ),
    )
    held.reset()
    held.advance_to(10.0)
    closed = network.model_copy(deep=True)
    closed.shunts[0].steps_in = 1
    cold = solve_network(closed)
    bus = compile_topology(network).node_to_bus["ld"]
    live = next(item.complex for item in engine.last_result.voltages if item.bus_id == bus and item.phase == "A")
    reference = next(item.complex for item in cold.voltages if item.bus_id == bus and item.phase == "A")
    reasons = []
    if measured is None or measured >= control["close_below_pu"]:
        reasons.append("premise")
    if before != 0 or after != expected or expected != 1:
        reasons.append("step")
    if restored.net.shunts[0].steps_in != after:
        reasons.append("snapshot")
    if held.net.shunts[0].steps_in != 0:
        reasons.append("hold")
    if abs(abs(live) - abs(reference)) / ll_to_ln(10_000.0) > VOLTAGE_PU:
        reasons.append("voltage")
    return _verdict(reasons, evidence="event_engine", steps_in=after, measured_pu=measured)


def qualify_resonance() -> dict[str, Any]:
    inductance = 10.0e-3
    capacitance = 100.0e-6
    frequency = resonance_hz(inductance, capacitance)
    at_resonance = series_rlc_impedance(2.0, inductance, capacitance, frequency)
    doubled = series_rlc_impedance(2.0, inductance, capacitance, 2.0 * frequency)
    omega = 2.0 * math.pi * 2.0 * frequency
    hand = 2.0 + 1j * (omega * inductance - 1.0 / (omega * capacitance))
    neutral = triplen_neutral(4.0 + 1.0j)
    reasons = []
    if abs(at_resonance.imag) > 1e-9:
        reasons.append("resonance")
    if abs(doubled - hand) > 1e-9:
        reasons.append("sweep")
    if abs(series_rlc_impedance(2.0, inductance, capacitance, frequency / 2.0).imag) < 1.0:
        reasons.append("off_resonance_not_reactive")
    if neutral != 12.0 + 3.0j:
        reasons.append("triplen")
    return _verdict(
        reasons,
        evidence="analytic_rlc",
        not_a_measured_spectrum=True,
        resonance_hz=frequency,
        residual_imag=at_resonance.imag,
    )


def qualify_mesh_fault() -> dict[str, Any]:
    """Два источника и трёхфазное КЗ. Вклад каждого источника считается своей схемой."""
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.checks import kcl_report
    from power_grid_env.model import StudyFault
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("mesh-fault", "КЗ в сетке", 10.0, s_base_va=1.0e6)
    builder.node("a")
    builder.node("b")
    builder.node("f")
    builder.source("a", 0.05, 0.40, source_id="left")
    builder.source("b", 0.08, 0.50, source_id="right", is_slack=False)
    builder.line_z1("a", "f", 0.20 + 0.50j, line_id="af")
    builder.line_z1("b", "f", 0.30 + 0.60j, line_id="bf")
    network = builder.build()
    for source in network.sources:
        source.r0_ohm = source.r_ohm
        source.x0_ohm = source.x_ohm
    network.study_faults.append(StudyFault(id="fault", bus_id="f", kind="3ph", phases=["A", "B", "C"], r_ohm=1.0))
    solved = solve_network(network)
    reference = _mesh_fault_sequence(network)
    topology = compile_topology(network)
    reasons = []
    voltage_errors = []
    angle_errors = []
    current_errors = []
    for node_id, expected in reference["voltage"].items():
        bus_id = topology.node_to_bus[node_id]
        actual = next(item.complex for item in solved.voltages if item.bus_id == bus_id and item.phase == "A")
        voltage_errors.append(abs(abs(actual) - abs(expected)) / reference["vln"])
        angle_errors.append(abs(_wrap(np.angle(actual) - np.angle(expected))))
    for line_id, expected in reference["line_current"].items():
        bus_id = topology.node_to_bus[line_id[0]]
        actual = next(
            item.current
            for item in solved.flows
            if item.equipment_id == line_id and item.bus_id == bus_id and item.phase == "A"
        )
        current_errors.append(abs(actual - expected) / max(1.0, abs(expected)))
    fault_current = next(item.current for item in solved.flows if item.equipment_id == "fault" and item.phase == "A")
    current_errors.append(abs(fault_current - reference["fault_current"]) / max(1.0, abs(reference["fault_current"])))
    if solved.status != "converged":
        reasons.append("status")
    if max(voltage_errors) > VOLTAGE_PU:
        reasons.append("voltage")
    if max(angle_errors) > ANGLE_RAD:
        reasons.append("angle")
    if max(current_errors) > CURRENT_RELATIVE:
        reasons.append("current")
    if kcl_report(network, solved)["mismatch_pu"] > 1e-8:
        reasons.append("kcl")
    if abs(reference["line_current"]["af"]) <= 1.0 or abs(reference["line_current"]["bf"]) <= 1.0:
        reasons.append("one_source_only")
    return _verdict(
        reasons,
        evidence="independent_mna",
        method="faulted_operating_point",
        not_iec_60909=True,
        max_voltage_error_pu=max(voltage_errors),
        max_current_error=max(current_errors),
        status=solved.status,
    )


def _mesh_fault_sequence(network) -> dict[str, Any]:
    vln = network.sources[0].ll_v / math.sqrt(3.0)
    by_id = {item.id: item for item in network.sources}
    lines = {item.id: item.r_ohm[0][0] + 1j * item.x_ohm[0][0] for item in network.lines}
    zf = network.study_faults[0].r_ohm + 1j * network.study_faults[0].x_ohm
    emf = {
        "left": vln * np.exp(1j * by_id["left"].angle_a_rad),
        "right": vln * np.exp(1j * by_id["right"].angle_a_rad),
    }
    zs = {
        "left": by_id["left"].r_ohm + 1j * by_id["left"].x_ohm,
        "right": by_id["right"].r_ohm + 1j * by_id["right"].x_ohm,
    }
    admit = np.zeros((3, 3), dtype=np.complex128)
    inject = np.zeros(3, dtype=np.complex128)
    for index, name in enumerate(("left", "right")):
        admit[index, index] += 1.0 / zs[name]
        inject[index] += emf[name] / zs[name]
    for i, j, impedance in ((0, 2, lines["af"]), (1, 2, lines["bf"])):
        admit[i, i] += 1.0 / impedance
        admit[j, j] += 1.0 / impedance
        admit[i, j] -= 1.0 / impedance
        admit[j, i] -= 1.0 / impedance
    admit[2, 2] += 1.0 / zf
    voltage = np.linalg.solve(admit, inject)
    return {
        "vln": vln,
        "voltage": {"a": voltage[0], "b": voltage[1], "f": voltage[2]},
        "line_current": {
            "af": (voltage[0] - voltage[2]) / lines["af"],
            "bf": (voltage[1] - voltage[2]) / lines["bf"],
        },
        "fault_current": voltage[2] / zf,
    }
