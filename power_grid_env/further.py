"""Зоны дистанции, реактор, беспозиционная передача и мягкий пуск.

Дистанционные зоны остаются фазорными: фильтр переходного режима не заявлен.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from power_grid_env.dynamics import induction_torque, soft_start_fraction
from power_grid_env.protection import distance_zones

LN = 10_000.0 / math.sqrt(3.0)
Z_NEAR = 0.5 + 2.0j
Z_FAR = 0.5 + 2.0j
Z_FAULT = 0.05 + 0.0j


def qualify_machine_follow() -> dict[str, Any]:
    """Ток машины по двум экспонентам входит в КЗ. В первый момент вклад больше, чем в установившемся режиме."""
    from power_grid_env.converter_controls import bus_voltage, fault_current_setpoint
    from power_grid_env.dynamics import ac_component

    z_source = 0.3 + 0.8j
    z_fault = 1.0 + 0.0j
    early = ac_component(0.0, 40.0, 25.0, 10.0, 0.04, 0.8)
    late = ac_component(4.0, 40.0, 25.0, 10.0, 0.04, 0.8)
    early_v = bus_voltage(LN, z_source, z_fault, early)
    late_v = bus_voltage(LN, z_source, z_fault, late)
    rated = 100_000.0
    multiple = early * math.sqrt(3.0) * 10_000.0 / rated
    measured = _machine_voltage(z_source, z_fault, rated, multiple)
    reasons = []
    if measured[1] != "converged":
        reasons.append("status")
    error = abs(measured[0] - early_v) / abs(early_v)
    if error > 1e-4:
        reasons.append("voltage")
    if abs(early_v / z_fault) <= abs(late_v / z_fault):
        reasons.append("decay")
    if abs(fault_current_setpoint(rated, 10_000.0, multiple) - early) / early > 1e-9:
        reasons.append("setpoint")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "voltage_error": error,
        "early_a": abs(early_v / z_fault),
        "late_a": abs(late_v / z_fault),
    }


def accept_black_start(voltage_pu: float, minimum_pu: float) -> bool:
    """Нагрузку пускаем только если опора уже подняла напряжение выше порога."""
    if minimum_pu <= 0:
        raise ValueError("порог пуска должен быть положительным")
    return voltage_pu >= minimum_pu


def qualify_black_start() -> dict[str, Any]:
    import numpy as np

    light = (100_000.0 + 20_000.0j) / 3.0
    heavy_z = 0.3 + 0.0j
    impedance = 0.5 + 2.0j
    light_v = LN * (LN**2 / np.conj(light)) / (impedance + LN**2 / np.conj(light))
    heavy_v = LN * heavy_z / (impedance + heavy_z)
    dark = _black_bus(False, 100_000.0, 20_000.0)
    alive = _black_bus(True, 100_000.0, 20_000.0)
    overloaded = _black_bus(True, float(3.0 * (LN**2 / heavy_z).real), 0.0)
    reasons = []
    if alive[1] != "converged" or overloaded[1] != "converged":
        reasons.append("status")
    if dark[2] > 0.01:
        reasons.append("dark")
    if abs(alive[0] - light_v) / LN > 1e-5 or abs(overloaded[0] - heavy_v) / LN > 1e-4:
        reasons.append("voltage")
    if not accept_black_start(alive[2], 0.9) or accept_black_start(overloaded[2], 0.9) or accept_black_start(dark[2], 0.9):
        reasons.append("decision")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "light_pu": alive[2],
        "heavy_pu": overloaded[2],
    }


def qualify_zones() -> dict[str, Any]:
    near = _seen("mid")
    remote = _seen("end")
    angle = math.atan2(Z_NEAR.imag, Z_NEAR.real)
    zone1 = 1.2 * abs(Z_NEAR)
    zone2 = 1.2 * abs(Z_NEAR + Z_FAR)
    close = distance_zones(
        near[0], near[1], zone1_ohm=zone1, zone2_ohm=zone2, line_angle_rad=angle, half_width_rad=0.4, zone1_s=0.05, zone2_s=0.4
    )
    far = distance_zones(
        remote[0], remote[1], zone1_ohm=zone1, zone2_ohm=zone2, line_angle_rad=angle, half_width_rad=0.4, zone1_s=0.05, zone2_s=0.4
    )
    blind = distance_zones(
        near[0], near[1], zone1_ohm=zone1, zone2_ohm=zone2, line_angle_rad=angle, half_width_rad=0.4, zone1_s=0.05, zone2_s=0.4, available=False
    )
    reasons = []
    if near[2] != "converged" or remote[2] != "converged":
        reasons.append("status")
    if near[3] > 1e-4 or remote[3] > 1e-4:
        reasons.append("impedance")
    if close["zone"] != "zone1" or far["zone"] != "zone2" or blind["trip"] or close["dynamic_qualified"]:
        reasons.append("zone")
    if not close["delay_s"] < far["delay_s"]:
        reasons.append("time")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "impedance_error": max(near[3], remote[3]),
        "close_zone": close["zone"],
        "remote_zone": far["zone"],
        "dynamic_qualified": False,
    }


def qualify_reactor() -> dict[str, Any]:
    from power_grid_env.more_steps import _receiving_voltage

    bare = _receiving_voltage(0.8 + 2.0j, 300_000.0, 40_000.0)
    reactor = _reactor_voltage()
    reasons = []
    if bare[1] != "converged" or reactor[1] != "converged":
        reasons.append("status")
    if abs(reactor[0]) >= abs(bare[0]):
        reasons.append("drop")
    if reactor[2] > 1e-3:
        reasons.append("reactive")
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "reactive_error": reactor[2]}


def qualify_lossless() -> dict[str, Any]:
    z_source = 1.0e-4 + 1.0e-4j
    z_line = 0.0 + 1.5j
    z_load = 25.0 + 0.0j
    voltage = LN * z_load / (z_source + z_line + z_load)
    reactance = (z_source + z_line).imag
    angle = math.atan2(voltage.imag, voltage.real)
    expected = 3.0 * LN * abs(voltage) / reactance * math.sin(-angle)
    measured = _export(z_source, z_line, z_load)
    reasons = []
    if measured[1] != "converged":
        reasons.append("status")
    error = abs(measured[0] - expected) / expected
    if error > 1e-4:
        reasons.append("power")
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "power_error": error}


def qualify_soft_start() -> dict[str, Any]:
    voltage = 400.0
    rotor_r, reactance = 0.2, 1.5
    full = induction_torque(1.0, voltage, rotor_r, reactance)
    half = induction_torque(1.0, voltage * soft_start_fraction(1.0, 2.0), rotor_r, reactance)
    ratio = _current_ratio()
    reasons = []
    if abs(half / full - 0.25) > 1e-12:
        reasons.append("torque")
    if soft_start_fraction(3.0, 2.0) != 1.0:
        reasons.append("end")
    if ratio[1] != "converged" or abs(ratio[0] - 2.0) / 2.0 > 1e-4:
        reasons.append("current")
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "current_ratio": ratio[0]}


def qualify_capability() -> dict[str, Any]:
    from power_grid_env.der import capability_q

    active = 60_000.0
    reactive = capability_q(active, 100_000.0, 40_000.0, 0.0)
    circle = capability_q(active, 100_000.0, 200_000.0, 0.0)
    measured = _q_delivery(active, reactive)
    reasons = []
    if abs(reactive - 40_000.0) > 1e-6 or abs(circle - 80_000.0) > 1e-3:
        reasons.append("limit")
    if measured[1] != "converged":
        reasons.append("status")
    if measured[2] > 1e-3:
        reasons.append("reactive")
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "reactive_error": measured[2]}


def _machine_voltage(z_source: complex, z_fault: complex, rated: float, multiple: float) -> tuple[complex, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.model import StudyFault
    from power_grid_env.results import SolveOptions
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("mac", "Машина", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", z_source.real, z_source.imag)
    builder.inverter("src", s_rated_va=rated, p_w=1_000.0, fault_current_multiple=multiple, inverter_id="machine")
    network = builder.build()
    network.sources[0].r0_ohm = z_source.real
    network.sources[0].x0_ohm = z_source.imag
    network.study_faults.append(StudyFault(id="fault", bus_id="src", kind="3ph", phases=["A", "B", "C"], r_ohm=z_fault.real))
    solved = solve_network(network, SolveOptions(inverter_current_limit_model=True))
    if solved.status != "converged":
        return 0j, solved.status
    bus = compile_topology(network).node_to_bus["src"]
    voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    return voltage, solved.status


def _black_bus(forming: bool, active_w: float, reactive_var: float) -> tuple[complex, str, float]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("black", "Пуск", 10.0, s_base_va=1.0e6)
    builder.node("kept")
    builder.node("ld")
    builder.source("kept", 0.05, 0.2)
    if forming:
        builder.inverter(
            "ld",
            s_rated_va=20.0e6,
            p_w=0.0,
            forming=True,
            forming_r_ohm=0.5,
            forming_x_ohm=2.0,
            inverter_id="grid",
        )
    builder.load("ld", active_w, reactive_var, model="constant_z", load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status not in {"converged", "deenergized_no_source"}:
        return 0j, solved.status, 1.0
    bus = compile_topology(network).node_to_bus["ld"]
    voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    return voltage, solved.status, abs(voltage) / LN


def _seen(fault_bus: str) -> tuple[complex, complex, str, float]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.model import StudyFault
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("zones", "Зоны", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("mid")
    builder.node("end")
    builder.source("src", 0.02, 0.05)
    builder.line_z1("src", "mid", Z_NEAR, line_id="near")
    builder.line_z1("mid", "end", Z_FAR, line_id="far")
    network = builder.build()
    network.sources[0].r0_ohm = 0.02
    network.sources[0].x0_ohm = 0.05
    network.study_faults.append(StudyFault(id="fault", bus_id=fault_bus, kind="3ph", phases=["A", "B", "C"], r_ohm=Z_FAULT.real))
    solved = solve_network(network)
    if solved.status != "converged":
        return 0j, 1j, solved.status, 1.0
    bus = compile_topology(network).node_to_bus["src"]
    voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    current = next(
        item.current
        for item in solved.flows
        if item.equipment_id == "near" and item.bus_id == bus and item.phase == "A" and item.terminal_index == 0
    )
    expected = Z_NEAR + Z_FAULT if fault_bus == "mid" else Z_NEAR + Z_FAR + Z_FAULT
    seen = voltage / current
    return voltage, current, solved.status, abs(seen - expected) / abs(expected)


def _reactor_voltage() -> tuple[complex, str, float]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("reactor", "Реактор", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 1.0e-5, 1.0e-5)
    builder.line_z1("src", "ld", 0.8 + 2.0j, line_id="line")
    builder.load("ld", 300_000.0, 40_000.0, model="constant_z", load_id="load")
    builder.shunt("ld", -150.0, shunt_id="reactor")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return 0j, solved.status, 1.0
    bus = compile_topology(network).node_to_bus["ld"]
    voltage = next(item for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    nominal = abs(voltage.complex) / voltage.v_base_ln_v
    reactive = sum(item.q_into_var for item in solved.flows if item.equipment_id == "reactor" and item.phase in "ABC")
    expected = 150_000.0 * nominal**2
    return voltage.complex, solved.status, abs(reactive - expected) / 150_000.0


def _export(z_source: complex, z_line: complex, z_load: complex) -> tuple[float, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    phase = LN**2 / np.conj(z_load)
    builder = NetBuilder("lossless", "Передача", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", z_source.real, z_source.imag)
    builder.line_z1("src", "ld", z_line, line_id="line")
    builder.load("ld", float(3.0 * phase.real), float(3.0 * phase.imag), model="constant_z", load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status
    exported = -sum(item.p_into_w for item in solved.flows if item.equipment_id == "source" and item.phase in "ABC")
    return exported, solved.status


def _current_ratio() -> tuple[float, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    def ampere(scale: float) -> tuple[float, str]:
        voltage = 400.0 * scale
        impedance = 0.2 + 1.5j
        power = 3.0 * (400.0**2 / np.conj(impedance))
        builder = NetBuilder(f"soft{scale}", "Пуск", 0.4, s_base_va=1.0e6)
        builder.node("src", 0.4)
        builder.source("src", 1.0e-6, 1.0e-6, ll_v=400.0 * scale)
        builder.load("src", float(power.real), float(power.imag), model="constant_z", load_id="motor")
        solved = solve_network(builder.build())
        if solved.status != "converged":
            return math.nan, solved.status
        current = abs(next(item.current for item in solved.flows if item.equipment_id == "source" and item.phase == "A"))
        return current, solved.status

    full = ampere(1.0)
    half = ampere(0.5)
    if full[1] != "converged" or half[1] != "converged":
        return math.nan, "not_converged"
    return full[0] / half[0], "converged"


def _q_delivery(active_w: float, reactive_var: float) -> tuple[float, str, float]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    builder = NetBuilder("capab", "Характеристика", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 0.01, 0.02)
    builder.load("src", active_w, reactive_var, load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status, 1.0
    measured = sum(item.q_into_var for item in solved.flows if item.equipment_id == "load" and item.phase in "ABC")
    return measured, solved.status, abs(measured - reactive_var) / abs(reactive_var)
