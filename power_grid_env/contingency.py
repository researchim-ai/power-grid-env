"""Отключение одной из двух параллельных линий.

Ток считается делителем напряжения, а не правилом «удваивается».
Порог перегрузки берётся между током до и после отключения, до просмотра
результата решателя.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

VOLTAGE_PU = 1e-5
CURRENT_RELATIVE = 1e-5


def receiving_end(emf: complex, source_z: complex, line_z: complex, load_z: complex, parallel: int) -> dict[str, complex]:
    """Ток каждой линии и напряжение приёмника. parallel — число одинаковых линий."""
    if parallel < 1 or abs(line_z) == 0 or abs(load_z) == 0:
        raise ValueError("число линий и сопротивления должны быть положительными")
    equivalent = line_z / parallel
    current = emf / (source_z + equivalent + load_z)
    return {"voltage": current * load_z, "line_current": current / parallel, "total_current": current}


def qualify_parallel_outage() -> dict[str, Any]:
    from power_grid_env.analysis import n1_line_outages
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    voltage_ln = 10_000.0 / math.sqrt(3.0)
    source_z = 0.05 + 0.2j
    line_z = 20.0 + 40.0j
    power = (2_000_000.0 + 400_000.0j) / 3.0
    load_z = voltage_ln**2 / np.conj(power)
    before = receiving_end(voltage_ln + 0.0j, source_z, line_z, load_z, 2)
    after = receiving_end(voltage_ln + 0.0j, source_z, line_z, load_z, 1)
    rating = 0.5 * (abs(before["line_current"]) + abs(after["line_current"]))

    def build(parallel: int, amp_rating: float):
        builder = NetBuilder(f"n1-{parallel}", "Параллель", 10.0, s_base_va=1.0e6)
        builder.node("src")
        builder.node("ld")
        builder.source("src", source_z.real, source_z.imag)
        for index in range(parallel):
            builder.line_z1("src", "ld", line_z, line_id=f"line{index}", rating_a=amp_rating)
        builder.load("ld", 2_000_000.0, 400_000.0, model="constant_z", load_id="load")
        network = builder.build()
        network.sources[0].r0_ohm = source_z.real
        network.sources[0].x0_ohm = source_z.imag
        return network

    intact = build(2, rating)
    solved = solve_network(intact)
    bus = compile_topology(intact).node_to_bus["ld"]
    actual_v = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    actual_i = next(
        item.current
        for item in solved.flows
        if item.equipment_id == "line0" and item.phase == "A" and item.terminal_index == 0
    )
    opened = build(1, rating)
    solved_open = solve_network(opened)
    open_i = next(
        item.current
        for item in solved_open.flows
        if item.equipment_id == "line0" and item.phase == "A" and item.terminal_index == 0
    )
    voltage_error = abs(abs(actual_v) - abs(before["voltage"])) / voltage_ln
    current_error = abs(abs(actual_i) - abs(before["line_current"])) / abs(before["line_current"])
    outage_error = abs(abs(open_i) - abs(after["line_current"])) / abs(after["line_current"])
    reports = n1_line_outages(intact)
    overloaded = {item["outage"]: item["overloads"] for item in reports}
    reasons = []
    if solved.status != "converged" or solved_open.status != "converged":
        reasons.append("status")
    if voltage_error > VOLTAGE_PU:
        reasons.append("voltage")
    if current_error > CURRENT_RELATIVE or outage_error > CURRENT_RELATIVE:
        reasons.append("current")
    if abs(after["line_current"]) <= abs(before["line_current"]):
        reasons.append("outage_not_heavier")
    naive = 2.0 * abs(before["line_current"])
    if abs(abs(after["line_current"]) - naive) / naive < 0.02:
        reasons.append("naive_doubling")
    if overloaded.get("line0") != ["line1"] or overloaded.get("line1") != ["line0"]:
        reasons.append("n1_overload")
    if len(reports) != 2:
        reasons.append("n1_dropped_case")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "evidence": "current_divider",
        "voltage_error_pu": voltage_error,
        "current_error": max(current_error, outage_error),
        "post_to_pre": abs(after["line_current"]) / abs(before["line_current"]),
        "status": solved.status,
    }
