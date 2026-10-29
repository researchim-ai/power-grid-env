"""Отключение параллельного трансформатора и недоотпуск критической нагрузки.

Ток оставшегося трансформатора — делитель, не удвоение.
Недоотпуск считается только по критическим нагрузкам с погашенной шиной.
"""

from __future__ import annotations

import math
from typing import Any

Z_SOURCE = 0.02 + 0.05j
Z_TRANSFORMER = 1j * 0.40 * (400.0**2 / 100_000.0)
Z_LOAD = 2.0 + 0.0j
LN_V = 400.0 / math.sqrt(3.0)


def parallel_transformer_current(emf: complex, branches: int) -> complex:
    if branches <= 0:
        raise ValueError("нужна хотя бы одна параллельная ветвь")
    return emf / (Z_SOURCE + Z_TRANSFORMER / branches + Z_LOAD)


def critical_unserved_j(
    demands: list[tuple[str, float, bool]],
    voltage_pu: dict[str, float],
    duration_s: float,
    *,
    dark_pu: float = 0.05,
) -> float:
    if duration_s < 0 or dark_pu <= 0:
        raise ValueError("длительность и порог погашенной шины должны быть неотрицательными")
    return sum(
        power_w * duration_s
        for load_id, power_w, critical in demands
        if critical and voltage_pu[load_id] < dark_pu
    )


def qualify_transformers() -> dict[str, Any]:
    emf = LN_V + 0j
    expected_each = parallel_transformer_current(emf, 2) / 2.0
    expected_post = parallel_transformer_current(emf, 1)
    both = _transformer_currents(None)
    without_first = _transformer_currents("t1")
    without_second = _transformer_currents("t2")
    ratio = abs(expected_post) / abs(expected_each)
    reasons = []
    if any(item[1] != "converged" for item in (both, without_first, without_second)):
        reasons.append("status")
    errors = [
        abs(both[0]["t1"] - abs(expected_each)) / abs(expected_each),
        abs(both[0]["t2"] - abs(expected_each)) / abs(expected_each),
        abs(without_first[0]["t2"] - abs(expected_post)) / abs(expected_post),
        abs(without_second[0]["t1"] - abs(expected_post)) / abs(expected_post),
    ]
    if max(errors) > 1e-4:
        reasons.append("current")
    if abs(ratio - 2.0) / 2.0 < 0.02:
        reasons.append("naive_doubling")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "current_error": max(errors),
        "post_to_pre": ratio,
    }


def qualify_unserved() -> dict[str, Any]:
    duration_s = 3600.0
    demands = [("critical", 100_000.0, True), ("spur", 40_000.0, False)]
    healthy = _supply(source_closed=True, spur_closed=True)
    dark = _supply(source_closed=False, spur_closed=True)
    spur_open = _supply(source_closed=True, spur_closed=False)
    reasons = []
    if any(item["status"] not in {"converged", "deenergized_no_source"} for item in (healthy, dark, spur_open)):
        reasons.append("status")
    if healthy["critical_pu"] < 0.9 or dark["critical_pu"] > 0.05 or spur_open["critical_pu"] < 0.9:
        reasons.append("voltage")
    if spur_open["spur_pu"] > 0.05:
        reasons.append("spur")
    healthy_energy = critical_unserved_j(demands, {"critical": healthy["critical_pu"], "spur": healthy["spur_pu"]}, duration_s)
    dark_energy = critical_unserved_j(demands, {"critical": dark["critical_pu"], "spur": dark["spur_pu"]}, duration_s)
    spur_energy = critical_unserved_j(demands, {"critical": spur_open["critical_pu"], "spur": spur_open["spur_pu"]}, duration_s)
    if healthy_energy != 0.0 or spur_energy != 0.0 or abs(dark_energy - 100_000.0 * duration_s) > 1.0:
        reasons.append("energy")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "dark_j": dark_energy,
        "spur_open_j": spur_energy,
        "critical_dark_pu": dark["critical_pu"],
    }


def qualify_ramp_power() -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.der import apply_ramp

    requested = 100_000.0
    stepped = apply_ramp(0.0, requested, 25_000.0, 1.0)
    inside = apply_ramp(0.0, 10_000.0, 25_000.0, 1.0)
    downward = apply_ramp(50_000.0, 0.0, 25_000.0, 1.0)
    measured = _bus_load(stepped)
    full = _bus_load(requested)
    reasons = []
    if stepped != 25_000.0 or inside != 10_000.0 or downward != 25_000.0:
        reasons.append("ramp")
    if measured[1] != "converged" or full[1] != "converged":
        reasons.append("status")
    if measured[2] > 1e-4:
        reasons.append("power")
    if full[0] <= measured[0] * 2.0:
        reasons.append("step")
    try:
        apply_ramp(0.0, 1.0, -1.0, 1.0)
        reasons.append("negative")
    except ValueError:
        pass
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "power_error": measured[2],
        "ramped_w": stepped,
    }


def _transformer_currents(removed: str | None) -> tuple[dict[str, float], str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("tn1", "Два трансформатора", 0.4, s_base_va=1.0e6)
    builder.node("hv", 0.4)
    builder.node("lv", 0.4)
    builder.source("hv", Z_SOURCE.real, Z_SOURCE.imag)
    for name in ("t1", "t2"):
        builder.transformer(
            "hv",
            "lv",
            vector_group="YNyn0",
            kva=100.0,
            hv_kv=0.4,
            lv_kv=0.4,
            x_pct=40.0,
            r_pct=0.0,
            transformer_id=name,
        )
    active = float(3.0 * (LN_V**2 / Z_LOAD).real)
    builder.load("lv", active, 0.0, model="constant_z", load_id="load")
    network = builder.build()
    for transformer in network.transformers:
        transformer.ppm_antifloat = 0.0
        transformer.imag_pct = 0.0
        transformer.no_load_loss_pct = 0.0
        transformer.load_loss_pct = 0.0
        for winding in transformer.windings:
            winding.r_pct = 0.0
    if removed is not None:
        network.transformers = [item for item in network.transformers if item.id != removed]
        network.terminals = [item for item in network.terminals if item.equipment_id != removed]
    solved = solve_network(network)
    currents = {"t1": 0.0, "t2": 0.0}
    if solved.status != "converged":
        return currents, solved.status
    bus = compile_topology(network).node_to_bus["lv"]
    for name in ("t1", "t2"):
        if name == removed:
            continue
        currents[name] = abs(
            next(item.current for item in solved.flows if item.equipment_id == name and item.bus_id == bus and item.phase == "A")
        )
    return currents, solved.status


def _supply(*, source_closed: bool, spur_closed: bool) -> dict[str, float | str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("ens", "Недоотпуск", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("critical")
    builder.node("spur")
    builder.source("src", 0.02, 0.05)
    builder.switch("src", "critical", switch_id="infeed", closed=source_closed)
    builder.switch("critical", "spur", switch_id="lateral", closed=spur_closed)
    builder.load("critical", 100_000.0, 0.0, load_id="critical", critical=True)
    builder.load("spur", 40_000.0, 0.0, load_id="spur")
    network = builder.build()
    solved = solve_network(network)
    if solved.status not in {"converged", "deenergized_no_source"}:
        return {"status": solved.status, "critical_pu": 1.0, "spur_pu": 1.0}
    topology = compile_topology(network)

    def pu(node_id: str) -> float:
        bus = topology.node_to_bus[node_id]
        voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
        return abs(voltage) / (10_000.0 / math.sqrt(3.0))

    return {"status": solved.status, "critical_pu": pu("critical"), "spur_pu": pu("spur")}


def _bus_load(active_w: float) -> tuple[float, str, float]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    builder = NetBuilder("ramp", "Рампа", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 0.01, 0.02)
    builder.load("src", active_w, 0.0, load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return 0.0, solved.status, 1.0
    measured = -sum(item.p_into_w for item in solved.flows if item.equipment_id == "source" and item.phase in "ABC")
    return measured, solved.status, abs(measured - active_w) / active_w
