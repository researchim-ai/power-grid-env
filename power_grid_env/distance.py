"""Дистанционный луч по фазному напряжению и току линии.

Видимое сопротивление сравнивается с Zлинии + Zкз. Переходный фильтр не заявлен.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from power_grid_env.protection import distance_zone

LINE_Z = 0.5 + 2.0j
FAULT_Z = 0.05 + 0.0j


def qualify() -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.model import StudyFault
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("distance", "Дистанция", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.02, 0.05)
    builder.line_z1("src", "ld", LINE_Z, line_id="line")
    network = builder.build()
    network.sources[0].r0_ohm = 0.02
    network.sources[0].x0_ohm = 0.05
    network.study_faults.append(StudyFault(id="end", bus_id="ld", kind="3ph", phases=["A", "B", "C"], r_ohm=FAULT_Z.real))
    solved = solve_network(network)
    topology = compile_topology(network)
    src = topology.node_to_bus["src"]
    voltage = next(item.complex for item in solved.voltages if item.bus_id == src and item.phase == "A")
    current = next(
        item.current
        for item in solved.flows
        if item.equipment_id == "line" and item.bus_id == src and item.phase == "A" and item.terminal_index == 0
    )
    seen = voltage / current
    expected = LINE_Z + FAULT_Z
    error = abs(seen - expected) / abs(expected)
    line_angle = math.atan2(LINE_Z.imag, LINE_Z.real)
    inside = distance_zone(voltage, current, reach_ohm=abs(expected) * 1.2, line_angle_rad=line_angle, half_width_rad=0.4)
    outside = distance_zone(voltage, current, reach_ohm=abs(LINE_Z) * 0.5, line_angle_rad=line_angle, half_width_rad=0.4)
    blind = distance_zone(voltage, current, reach_ohm=abs(expected) * 1.2, line_angle_rad=line_angle, half_width_rad=0.4, available=False)
    reasons = []
    if solved.status != "converged":
        reasons.append("status")
    if error > 1e-4:
        reasons.append("impedance")
    if not inside["trip"] or outside["trip"] or blind["trip"] or inside["dynamic_qualified"]:
        reasons.append("zone")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "evidence": "phase_vi",
        "dynamic_qualified": False,
        "impedance_error": error,
        "seen_ohm": abs(seen),
        "status": solved.status,
    }


def qualify_frequency_scaling() -> dict[str, Any]:
    """X пересчитывается пропорционально частоте. Это не модель ШИМ-преобразователя."""
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    voltage_ln = 10_000.0 / math.sqrt(3.0)
    rotor_r = 15.0
    reactance = 80.0
    source_z = 0.05 + 0.2j

    def ampere(scale: float) -> float:
        motor = rotor_r + 1j * reactance * scale
        active, reactive = _power(motor, voltage_ln)
        builder = NetBuilder(f"f{scale}", "Частота", 10.0, s_base_va=1.0e6)
        builder.node("src")
        builder.source("src", source_z.real, source_z.imag * scale)
        builder.load("src", active, reactive, model="constant_z", load_id="motor")
        network = builder.build()
        network.sources[0].r0_ohm = source_z.real
        network.sources[0].x0_ohm = source_z.imag * scale
        solved = solve_network(network)
        if solved.status != "converged":
            return math.nan
        bus = compile_topology(network).node_to_bus["src"]
        return abs(next(item.current for item in solved.flows if item.equipment_id == "source" and item.bus_id == bus and item.phase == "A"))

    def formula(scale: float) -> float:
        return abs(voltage_ln / (source_z.real + 1j * source_z.imag * scale + rotor_r + 1j * reactance * scale))

    full = ampere(1.0)
    half = ampere(0.5)
    reasons = []
    if math.isnan(full) or math.isnan(half):
        reasons.append("status")
    if abs(full - formula(1.0)) / formula(1.0) > 1e-5 or abs(half - formula(0.5)) / formula(0.5) > 1e-5:
        reasons.append("current")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "evidence": "scaled_reactance",
        "not_a_pwm_drive": True,
        "full_a": full,
        "half_a": half,
    }


def _power(impedance: complex, voltage_ln: float) -> tuple[float, float]:
    phase = voltage_ln**2 / np.conj(impedance)
    return float(3.0 * phase.real), float(3.0 * phase.imag)
