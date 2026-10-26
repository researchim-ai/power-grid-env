"""Короткий замер полного solve. Это не обещание масштаба 10 000 узлов."""

from __future__ import annotations

import time

from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder


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
