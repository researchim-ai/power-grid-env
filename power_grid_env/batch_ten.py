"""Коэффициент c, перенапряжение, остров, отпайка и потери линии.

Каждый режим сверяется со своей формулой. Коэффициент c не подменяется режимом сети.
"""

from __future__ import annotations

import math
from typing import Any

from power_grid_env.iec60909 import initial_symmetrical_three_phase, voltage_factor

LN = 10_000.0 / math.sqrt(3.0)


def nearest_tap(target: float, taps: list[float]) -> float:
    """Ближайшая дискретная отпайка. При равенстве расстояний берётся меньшая. Это не непрерывный оптимум."""
    if not taps or target <= 0 or any(item <= 0 for item in taps):
        raise ValueError("отпайки и цель должны быть положительными")
    return min(taps, key=lambda item: (abs(item - target), item))


def qualify_voltage_factor() -> dict[str, Any]:
    impedance = 0.3 + 0.8j
    fault = 0.5 + 0.0j
    loop = impedance + fault
    c_max = voltage_factor(10_000.0, maximum=True)
    c_min = voltage_factor(10_000.0, maximum=False)
    low_max = voltage_factor(400.0, maximum=True)
    low_min = voltage_factor(400.0, maximum=False)
    at_one = initial_symmetrical_three_phase(10_000.0, 1.0, loop)
    at_max = initial_symmetrical_three_phase(10_000.0, c_max, loop)
    measured = _fault_current(impedance, fault.real)
    reasons = []
    if c_max != 1.1 or c_min != 1.0 or low_max != 1.05 or low_min != 0.95:
        reasons.append("table")
    if abs(abs(at_max) / abs(at_one) - c_max) > 1e-12:
        reasons.append("scale")
    if measured[1] != "converged":
        reasons.append("status")
    error = abs(measured[0] - abs(at_one)) / abs(at_one)
    if error > 1e-4:
        reasons.append("current")
    if abs(measured[0] - abs(at_max)) / abs(at_max) < 0.05:
        reasons.append("confused_with_c")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "current_error": error,
        "c_max": c_max,
        "c_min": c_min,
    }


def qualify_overvoltage() -> dict[str, Any]:
    from power_grid_env.protection import RelayState, advance_relay

    measured = _source_pu(1.08)
    quiet = _source_pu(1.02)
    high = RelayState("ov", "overvoltage", "src", 1.05, "definite", 0.2, breaker_delay_s=0.05)
    early = RelayState("ov-early", "overvoltage", "src", 1.05, "definite", 0.2, breaker_delay_s=0.05)
    hold = RelayState("ov-hold", "overvoltage", "src", 1.05, "definite", 0.2, breaker_delay_s=0.05)
    tripped = False
    early_trip = False
    held = False
    for step in range(5):
        if advance_relay(high, measured[0], 0.05) == "trip":
            tripped = True
        if step < 4 and advance_relay(early, measured[0], 0.05) == "trip":
            early_trip = True
        if advance_relay(hold, quiet[0], 0.05) == "trip":
            held = True
    reasons = []
    if measured[1] != "converged" or quiet[1] != "converged":
        reasons.append("status")
    if abs(measured[0] - 1.08) > 1e-4 or abs(quiet[0] - 1.02) > 1e-4:
        reasons.append("voltage")
    if not tripped or early_trip or held:
        reasons.append("relay")
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "high_pu": measured[0]}


def qualify_island() -> dict[str, Any]:
    import numpy as np

    vln = LN
    impedance = 5.0 + 10.0j
    power = (3.0e6 + 0.6e6j) / 3.0
    z_load = vln**2 / np.conj(power)
    forming = vln * z_load / (impedance + z_load)
    utility = vln * z_load / (0.02 + 0.05j + z_load)
    island = _island_voltage(forming=True, utility=False)
    dark = _island_voltage(forming=False, utility=False)
    grid = _island_voltage(forming=False, utility=True)
    reasons = []
    if island[1] != "converged" or grid[1] != "converged":
        reasons.append("status")
    if dark[1] not in {"converged", "deenergized_no_source"}:
        reasons.append("dark_status")
    forming_error = abs(island[0] - forming) / vln
    grid_error = abs(grid[0] - utility) / vln
    if forming_error > 1e-5 or grid_error > 1e-5:
        reasons.append("voltage")
    if dark[2] > 0.01:
        reasons.append("following")
    if abs(abs(forming) - abs(utility)) / vln < 1e-4:
        reasons.append("same_voltage")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "forming_error": forming_error,
        "grid_error": grid_error,
        "dark_pu": dark[2],
    }


def qualify_tap() -> dict[str, Any]:
    import numpy as np

    taps = [0.975, 1.0, 1.025]
    chosen = nearest_tap(1.012, taps)
    tie = nearest_tap(1.0125, taps)
    vln_lv = 400.0 / math.sqrt(3.0) * 1.025
    z_transformer = 1j * 0.001 * (400.0**2 / 1.0e6)
    power = (20_000.0 + 0.0j) / 3.0
    z_load = (400.0 / math.sqrt(3.0)) ** 2 / np.conj(power)
    expected = vln_lv * z_load / (z_transformer + z_load)
    measured = _tap_voltage(1.025)
    reasons = []
    if chosen != 1.0 or tie != 1.0:
        reasons.append("discrete")
    if measured[1] != "converged":
        reasons.append("status")
    error = abs(measured[0] - expected) / abs(expected)
    if error > 1e-4:
        reasons.append("voltage")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "voltage_error": error,
        "chosen_tap": chosen,
        "global_optimum_claimed": False,
    }


def qualify_line_loss() -> dict[str, Any]:
    z_source = 1.0e-4 + 1.0e-4j
    z_line = 0.4 + 0.8j
    z_load = 30.0 + 0.0j
    current = LN / (z_source + z_line + z_load)
    expected = 3.0 * abs(current) ** 2 * z_line.real
    measured = _line_loss(z_source, z_line, z_load)
    reasons = []
    if measured[1] != "converged":
        reasons.append("status")
    error = abs(measured[0] - expected) / expected
    if error > 1e-4:
        reasons.append("loss")
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "loss_error": error, "loss_w": expected}


def _fault_current(z_source: complex, fault_r: float) -> tuple[float, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.model import StudyFault

    builder = NetBuilder("cfactor", "Коэффициент", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", z_source.real, z_source.imag)
    network = builder.build()
    network.sources[0].r0_ohm = z_source.real
    network.sources[0].x0_ohm = z_source.imag
    network.study_faults.append(StudyFault(id="fault", bus_id="src", kind="3ph", phases=["A", "B", "C"], r_ohm=fault_r))
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status
    current = abs(next(item.current for item in solved.flows if item.equipment_id == "source" and item.phase == "A"))
    return current, solved.status


def _source_pu(scale: float) -> tuple[float, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("ov", "Перенапряжение", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 0.001, 0.001, ll_v=scale * 10_000.0)
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status
    bus = compile_topology(network).node_to_bus["src"]
    voltage = next(item for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    return abs(voltage.complex) / voltage.v_base_ln_v, solved.status


def _island_voltage(*, forming: bool, utility: bool) -> tuple[complex, str, float]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("island", "Остров", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    if utility:
        builder.source("src", 0.02, 0.05)
        builder.switch("src", "ld", switch_id="tie", closed=True)
    else:
        builder.node("kept")
        builder.source("kept", 0.05, 0.2)
    if forming:
        builder.inverter(
            "ld",
            s_rated_va=10.0e6,
            p_w=0.0,
            forming=True,
            forming_r_ohm=5.0,
            forming_x_ohm=10.0,
            inverter_id="grid",
        )
    elif not utility:
        builder.inverter("ld", s_rated_va=10.0e6, p_w=0.0, inverter_id="follow")
    builder.load("ld", 3.0e6, 0.6e6, model="constant_z", load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status not in {"converged", "deenergized_no_source"}:
        return 0j, solved.status, 1.0
    bus = compile_topology(network).node_to_bus["ld"]
    voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    return voltage, solved.status, abs(voltage) / LN


def _tap_voltage(tap: float) -> tuple[complex, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("tap", "Отпайка", 10.0, s_base_va=1.0e6)
    builder.node("hv", 10.0)
    builder.node("lv", 0.4)
    builder.source("hv", 1.0e-5, 1.0e-5)
    builder.transformer(
        "hv",
        "lv",
        vector_group="YNyn0",
        kva=1000.0,
        hv_kv=10.0,
        lv_kv=0.4,
        x_pct=0.1,
        r_pct=0.0,
        transformer_id="t",
    )
    builder.load("lv", 20_000.0, 0.0, model="constant_z", load_id="load")
    network = builder.build()
    transformer = network.transformers[0]
    transformer.ppm_antifloat = 0.0
    transformer.imag_pct = 0.0
    transformer.no_load_loss_pct = 0.0
    transformer.load_loss_pct = 0.0
    for winding in transformer.windings:
        winding.r_pct = 0.0
    transformer.windings[1].tap_pu = tap
    solved = solve_network(network)
    if solved.status != "converged":
        return 0j, solved.status
    bus = compile_topology(network).node_to_bus["lv"]
    voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    return voltage, solved.status


def _line_loss(z_source: complex, z_line: complex, z_load: complex) -> tuple[float, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    phase = LN**2 / z_load.conjugate()
    builder = NetBuilder("loss", "Потери", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", z_source.real, z_source.imag)
    builder.line_z1("src", "ld", z_line, line_id="line")
    builder.load("ld", float(3.0 * phase.real), float(3.0 * phase.imag), model="constant_z", load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status
    loss = sum(item.p_into_w for item in solved.flows if item.equipment_id == "line" and item.phase in "ABC")
    return loss, solved.status
