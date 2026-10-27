"""Короткий замер полного solve. Это не обещание масштаба 10 000 узлов."""

from __future__ import annotations

import resource
import subprocess
import sys
import time
from typing import Any

from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder


def time_radial(buses: int, repeats: int = 1) -> dict[str, float | str]:
    """Замер лучевой схемы с одной нагрузкой на конце.

    На этой машине 100 и 1000 шин сходились. 10 000 шин за один прогон не сошлись:
    это измерение, а не обещание размера.
    """
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
        "status": status,
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


def measure_envelope(sizes: tuple[int, ...] = (100, 1000), *, probe_10000: bool = False, timeout_s: float = 20.0) -> dict[str, Any]:
    """Замер объявленных длин луча. 10 000 шин и 7/30 суток в масштаб не входят, пока прогон не сошёлся."""
    rows = []
    for buses in sizes:
        measured = time_radial(buses, repeats=1)
        measured["rss_kb"] = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        measured["claimed_as_supported_city"] = False
        rows.append(measured)
    return {
        "workloads": rows,
        "buses_10000": _probe_10000(timeout_s) if probe_10000 else {"buses": 10000, "status": "not_probed", "claimed_as_supported_city": False},
        "horizons_days": {"7": "not_measured", "30": "not_measured"},
        "size_claim": False,
    }


def _probe_10000(timeout_s: float) -> dict[str, Any]:
    code = "from power_grid_env.benchmarks import time_radial; import json; print(json.dumps(time_radial(10000)))"
    try:
        completed = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"buses": 10000, "status": "timeout", "claimed_as_supported_city": False, "timeout_s": timeout_s}
    if completed.returncode != 0:
        return {"buses": 10000, "status": "failed", "claimed_as_supported_city": False, "returncode": completed.returncode}
    import json

    payload = json.loads(completed.stdout)
    payload["claimed_as_supported_city"] = False
    return payload
