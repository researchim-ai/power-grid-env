"""Короткий замер полного solve. Это не обещание масштаба 10 000 узлов."""

from __future__ import annotations

import time

from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder


def time_radial(buses: int, repeats: int = 1) -> dict[str, float]:
    """Замер лучевой схемы. Число шин — размер этого прогона, не обещание 10 000 узлов."""
    if buses < 2:
        raise ValueError("нужно хотя бы две шины")
    builder = NetBuilder(f"radial-{buses}", "radial", 10.0, s_base_va=10e6)
    builder.node("n0", 10)
    builder.source("n0", 0.05, 0.2)
    for index in range(1, buses):
        builder.node(f"n{index}", 10)
        builder.line_z1(f"n{index - 1}", f"n{index}", 0.05 + 0.08j, rating_a=400, line_id=f"l{index}")
    builder.load(f"n{buses - 1}", 150e3, 30e3, load_id="load")
    network = builder.build()
    samples = []
    status = ""
    for _ in range(repeats):
        started = time.perf_counter()
        result = solve_network(network)
        samples.append(time.perf_counter() - started)
        status = result.status
    ordered = sorted(samples)
    return {
        "buses": float(buses),
        "repeats": float(repeats),
        "p50_s": ordered[len(ordered) // 2],
        "converged": 1.0 if status == "converged" else 0.0,
    }


def time_two_bus(repeats: int = 5) -> dict[str, float]:
    builder = NetBuilder("bench", "bench", 12.47)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 1e-3, 1e-3)
    builder.line_z1("src", "ld", 0.2 + 0.4j, line_id="line")
    builder.load("ld", 200e3, 40e3, load_id="load")
    network = builder.build()
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        result = solve_network(network)
        samples.append(time.perf_counter() - started)
        if result.status != "converged":
            raise RuntimeError(result.status)
    ordered = sorted(samples)
    return {
        "repeats": float(repeats),
        "p50_s": ordered[len(ordered) // 2],
        "max_s": ordered[-1],
    }
