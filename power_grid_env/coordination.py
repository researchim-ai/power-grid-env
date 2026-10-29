"""Карта селективности луча и два режима АВР.

Место КЗ, сопротивление источника, шунт нагрузки и уставки меняют время.
Это три шины, не карта всей площадки.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from power_grid_env.protection import ats_decision, iec_operate_time

LN_V = 10_000.0 / math.sqrt(3.0)
Z_NEAR = 0.3 + 0.6j
Z_FAR = 0.4 + 0.8j
Z_FAULT = 0.05 + 0.0j
Z_STRONG = 0.05 + 0.2j
Z_MEDIUM = 1.0 + 2.0j
Z_WEAK = 20.0 + 40.0j
LOAD_P_W = 8.0e6
LOAD_Q_VAR = 1.6e6


def shunt_current(
    emf: complex,
    z_source: complex,
    z_upstream: complex,
    z_fault: complex,
    z_load: complex | None = None,
) -> complex:
    """Ток выше повреждения. Нагрузка — шунт на той же шине, что и КЗ."""
    admit = 1.0 / z_fault
    if z_load is not None:
        admit += 1.0 / z_load
    voltage = emf / (1.0 + (z_source + z_upstream) * admit)
    return voltage * admit


def downstream_current(emf: complex, z_source: complex, z_near: complex, z_far: complex, z_fault: complex, z_load: complex | None) -> tuple[complex, complex]:
    """КЗ в конце луча. Нагрузка сидит на промежуточной шине."""
    admit = 1.0 / (z_far + z_fault)
    if z_load is not None:
        admit += 1.0 / z_load
    voltage = emf / (1.0 + (z_source + z_near) * admit)
    near = voltage * admit
    far = voltage / (z_far + z_fault)
    return near, far


def qualify_feeder_map() -> dict[str, Any]:
    """Место КЗ × источник × нагрузка × уставка. Это объявленный луч, не всякая площадка."""
    emf = LN_V + 0j
    z_load = _load_impedance()
    rows = []
    for source_name, z_source in (("strong", Z_STRONG), ("weak", Z_WEAK)):
        for place, current in (
            ("close", shunt_current(emf, z_source, Z_NEAR, Z_FAULT, None)),
            ("far", downstream_current(emf, z_source, Z_NEAR, Z_FAR, Z_FAULT, None)[0]),
            ("far_loaded", downstream_current(emf, z_source, Z_NEAR, Z_FAR, Z_FAULT, z_load)[0]),
        ):
            for multiplier in (0.05, 0.2):
                rows.append(
                    {
                        "source": source_name,
                        "place": place,
                        "multiplier": multiplier,
                        "current_a": abs(current),
                        "time_s": iec_operate_time(abs(current), 500.0, "standard_inverse", multiplier),
                    }
                )
    checked = {
        "close": _phase_ampere(Z_STRONG, "mid", False),
        "far_loaded": _phase_ampere(Z_STRONG, "end", True),
        "weak": _phase_ampere(Z_WEAK, "end", False),
    }
    expected = {
        "close": abs(shunt_current(emf, Z_STRONG, Z_NEAR, Z_FAULT, None)),
        "far_loaded": abs(downstream_current(emf, Z_STRONG, Z_NEAR, Z_FAR, Z_FAULT, z_load)[0]),
        "weak": abs(downstream_current(emf, Z_WEAK, Z_NEAR, Z_FAR, Z_FAULT, None)[0]),
    }
    by_key = {(row["source"], row["place"], row["multiplier"]): row for row in rows}
    reasons = []
    errors = []
    for key, measured in checked.items():
        if measured[1] != "converged":
            reasons.append("status")
            errors.append(1.0)
            continue
        errors.append(abs(measured[0] - expected[key]) / expected[key])
    if max(errors) > 1e-4:
        reasons.append("current")
    if not by_key[("strong", "close", 0.05)]["time_s"] < by_key[("strong", "far", 0.05)]["time_s"]:
        reasons.append("location")
    if not by_key[("strong", "close", 0.05)]["current_a"] > by_key[("weak", "close", 0.05)]["current_a"]:
        reasons.append("source")
    if abs(by_key[("strong", "far_loaded", 0.05)]["current_a"] - by_key[("strong", "far", 0.05)]["current_a"]) / by_key[("strong", "far", 0.05)]["current_a"] < 0.01:
        reasons.append("load")
    if not by_key[("strong", "close", 0.05)]["time_s"] < by_key[("strong", "close", 0.2)]["time_s"]:
        reasons.append("settings")
    if by_key[("weak", "far", 0.05)]["time_s"] != math.inf:
        reasons.append("pickup")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "current_error": max(errors),
        "cells": len(rows),
        "plant_wide_map": False,
    }


def qualify() -> dict[str, Any]:
    emf = LN_V + 0j
    z_load = _load_impedance()
    strong_end, strong_far = downstream_current(emf, Z_STRONG, Z_NEAR, Z_FAR, Z_FAULT, None)
    strong_loaded, loaded_far = downstream_current(emf, Z_STRONG, Z_NEAR, Z_FAR, Z_FAULT, z_load)
    strong_mid = shunt_current(emf, Z_STRONG, Z_NEAR, Z_FAULT, None)
    weak_end, _weak_far = downstream_current(emf, Z_WEAK, Z_NEAR, Z_FAR, Z_FAULT, None)
    medium_end, _medium_far = downstream_current(emf, Z_MEDIUM, Z_NEAR, Z_FAR, Z_FAULT, None)
    measured = {
        "end": _phase_ampere(Z_STRONG, "end", False),
        "mid": _phase_ampere(Z_STRONG, "mid", False),
        "loaded": _phase_ampere(Z_STRONG, "end", True),
        "weak": _phase_ampere(Z_WEAK, "end", False),
        "far_open": _phase_ampere(Z_STRONG, "mid", False, equipment_id="far"),
        "far_loaded": _phase_ampere(Z_STRONG, "end", True, equipment_id="far"),
    }
    pickup = 0.25 * abs(strong_end)
    near_time = iec_operate_time(abs(strong_end), pickup, "standard_inverse", 0.05)
    far_time = iec_operate_time(abs(strong_end), pickup, "standard_inverse", 0.2)
    medium_time = iec_operate_time(abs(medium_end), pickup, "standard_inverse", 0.05)
    swapped_near = iec_operate_time(abs(strong_end), pickup, "standard_inverse", 0.5)
    swapped_far = iec_operate_time(abs(strong_end), pickup, "standard_inverse", 0.05)
    mid_far = iec_operate_time(0.0, pickup, "standard_inverse", 0.2)
    weak_time = iec_operate_time(abs(weak_end), pickup, "standard_inverse", 0.05)
    errors = [
        abs(measured["end"][0] - abs(strong_end)) / abs(strong_end),
        abs(measured["mid"][0] - abs(strong_mid)) / abs(strong_mid),
        abs(measured["loaded"][0] - abs(strong_loaded)) / abs(strong_loaded),
        abs(measured["weak"][0] - abs(weak_end)) / abs(weak_end),
        abs(measured["far_loaded"][0] - abs(loaded_far)) / abs(loaded_far),
    ]
    reasons = []
    if any(item[1] != "converged" for item in measured.values()):
        reasons.append("status")
    if max(errors) > 1e-5:
        reasons.append("current")
    if measured["far_open"][0] > 1e-4:
        reasons.append("downstream")
    if not near_time < far_time < math.inf:
        reasons.append("order")
    if not medium_time > near_time:
        reasons.append("source")
    if not swapped_far < swapped_near:
        reasons.append("settings")
    if mid_far != math.inf or weak_time != math.inf:
        reasons.append("silent")
    if abs(abs(strong_loaded) - abs(strong_end)) / abs(strong_end) < 0.01:
        reasons.append("load_axis")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "current_error": max(errors),
        "near_before_far": near_time < far_time,
        "weak_source_silent": weak_time == math.inf,
        "far_blind_at_midpoint": mid_far == math.inf,
        "swapped_settings_reverse_order": swapped_far < swapped_near,
        "load_changes_current": abs(abs(strong_loaded) - abs(strong_end)) / abs(strong_end),
        "plant_wide_map": False,
    }


def qualify_transfer() -> dict[str, Any]:
    """Два допустимых режима: рабочий ввод и резервный. Параллель с чужим углом не собирается."""
    z_line = 0.2 + 0.4j
    z_load = _load_impedance(200e3, 40e3)
    emf = LN_V + 0j
    primary = _feeder_voltage(Z_STRONG, z_line, z_load)
    reserve = _feeder_voltage(0.08 + 0.25j, z_line, z_load)
    expected_primary = emf * z_load / (Z_STRONG + z_line + z_load)
    expected_reserve = emf * z_load / (0.08 + 0.25j + z_line + z_load)
    limits = dict(max_angle_rad=0.2, max_voltage_pu=0.1, dead_pu=0.05)
    transferred = ats_decision(
        0j,
        reserve[0],
        LN_V,
        LN_V,
        primary_closed=True,
        reserve_closed=False,
        reserve_available=True,
        **limits,
    )
    blocked = ats_decision(
        primary[0],
        reserve[0] * np.exp(0.5j),
        LN_V,
        LN_V,
        primary_closed=True,
        reserve_closed=True,
        reserve_available=True,
        **limits,
    )
    unavailable = ats_decision(
        0j,
        reserve[0],
        LN_V,
        LN_V,
        primary_closed=False,
        reserve_closed=False,
        reserve_available=False,
        **limits,
    )
    held = ats_decision(
        primary[0],
        reserve[0],
        LN_V,
        LN_V,
        primary_closed=True,
        reserve_closed=False,
        reserve_available=True,
        **limits,
    )
    reasons = []
    if primary[1] != "converged" or reserve[1] != "converged":
        reasons.append("status")
    primary_error = abs(primary[0] - expected_primary) / LN_V
    reserve_error = abs(reserve[0] - expected_reserve) / LN_V
    if primary_error > 1e-5 or reserve_error > 1e-5:
        reasons.append("voltage")
    if transferred["mode"] != "transferred" or transferred["reserve_closed"] is not True:
        reasons.append("transfer")
    if blocked["mode"] != "blocked" or blocked["reserve_closed"] is not False:
        reasons.append("parallel")
    if unavailable["mode"] != "blocked" or unavailable["reserve_closed"] is not False:
        reasons.append("unavailable")
    if held["mode"] != "normal" or held["reserve_closed"] is not False:
        reasons.append("hold")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "voltage_error": max(primary_error, reserve_error),
        "transfer_reason": transferred["reason"],
        "parallel_reason": blocked["reason"],
    }


def _load_impedance(active_w: float = LOAD_P_W, reactive_var: float = LOAD_Q_VAR) -> complex:
    phase = (active_w + 1j * reactive_var) / 3.0
    return LN_V**2 / np.conj(phase)


def _phase_ampere(
    z_source: complex,
    fault_bus: str,
    with_load: bool,
    equipment_id: str = "near",
) -> tuple[float, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.model import StudyFault
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("coord", "Селективность", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("mid")
    builder.node("end")
    builder.source("src", z_source.real, z_source.imag)
    builder.line_z1("src", "mid", Z_NEAR, line_id="near")
    builder.line_z1("mid", "end", Z_FAR, line_id="far")
    if with_load:
        builder.load("mid", LOAD_P_W, LOAD_Q_VAR, model="constant_z", load_id="load")
    network = builder.build()
    network.sources[0].r0_ohm = z_source.real
    network.sources[0].x0_ohm = z_source.imag
    network.study_faults.append(StudyFault(id="fault", bus_id=fault_bus, kind="3ph", phases=["A", "B", "C"], r_ohm=Z_FAULT.real))
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status
    bus = compile_topology(network).node_to_bus["src" if equipment_id == "near" else "mid"]
    current = next(
        item.current
        for item in solved.flows
        if item.equipment_id == equipment_id and item.bus_id == bus and item.phase == "A" and item.terminal_index == 0
    )
    return abs(current), solved.status


def _feeder_voltage(z_source: complex, z_line: complex, z_load: complex) -> tuple[complex, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    active = float(3.0 * (LN_V**2 / np.conj(z_load)).real)
    reactive = float(3.0 * (LN_V**2 / np.conj(z_load)).imag)
    builder = NetBuilder("ats", "АВР", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", z_source.real, z_source.imag)
    builder.line_z1("src", "ld", z_line, line_id="line")
    builder.load("ld", active, reactive, model="constant_z", load_id="load")
    network = builder.build()
    network.sources[0].r0_ohm = z_source.real
    network.sources[0].x0_ohm = z_source.imag
    solved = solve_network(network)
    if solved.status != "converged":
        return 0j, solved.status
    bus = compile_topology(network).node_to_bus["ld"]
    voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    return voltage, solved.status
