"""Короткий замер полного solve. Несходимость длинного луча здесь — предел передаваемой мощности."""

from __future__ import annotations

import math
import resource
import subprocess
import sys
import time
from typing import Any

from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder

RADIAL_VOLTAGE_LL_V = 10_000.0
RADIAL_SEGMENT_Z = 0.05 + 0.08j
RADIAL_SOURCE_Z = 0.05 + 0.2j
RADIAL_LOAD_P_W = 150_000.0
RADIAL_LOAD_Q_VAR = 30_000.0


def radial_pq_limit(
    buses: int,
    *,
    segment_z: complex = RADIAL_SEGMENT_Z,
    source_z: complex = RADIAL_SOURCE_Z,
    p_w: float = RADIAL_LOAD_P_W,
    q_var: float = RADIAL_LOAD_Q_VAR,
    vll_v: float = RADIAL_VOLTAGE_LL_V,
) -> dict[str, float | bool | None]:
    """Есть ли постоянная мощность на конце последовательной цепочки.

    Уравнение конца линии квадратное. Отрицательный дискриминант значит,
    что запрошенные P и Q лежат за носом кривой и решения нет.
    """
    if buses < 2:
        raise ValueError("нужно хотя бы две шины")
    voltage = vll_v / math.sqrt(3.0)
    active = p_w / 3.0
    reactive = q_var / 3.0
    impedance = source_z + (buses - 1) * segment_z
    resistance, reactance = impedance.real, impedance.imag
    linear = 2.0 * (active * resistance + reactive * reactance) - voltage * voltage
    constant = (active * resistance + reactive * reactance) ** 2 + (active * reactance - reactive * resistance) ** 2
    discriminant = linear * linear - 4.0 * constant
    high_pu = None
    if discriminant >= 0:
        high_pu = math.sqrt((-linear + math.sqrt(discriminant)) / 2.0) / voltage
    return {
        "buses": float(buses),
        "exists": discriminant >= 0.0,
        "discriminant": discriminant,
        "high_voltage_pu": high_pu,
        "series_ohm": abs(impedance),
    }


def time_radial(buses: int, repeats: int = 1, *, segment_z: complex = RADIAL_SEGMENT_Z) -> dict[str, float | str | bool | None]:
    """Луч с одной концевой нагрузкой.

    При segment_z = 0.05+j0.08 Ом на участок решение постоянной мощности
    есть только до 2054 шин. Более длинная цепочка с тем же участком
    физически не передаёт 150 кВт. Это не предел числа узлов.
    """
    if buses < 2:
        raise ValueError("нужно хотя бы две шины")
    limit = radial_pq_limit(buses, segment_z=segment_z)
    builder = NetBuilder(f"radial-{buses}", "radial", RADIAL_VOLTAGE_LL_V / 1000.0, s_base_va=10e6)
    builder.node("n0", RADIAL_VOLTAGE_LL_V / 1000.0)
    builder.source("n0", RADIAL_SOURCE_Z.real, RADIAL_SOURCE_Z.imag)
    for index in range(1, buses):
        builder.node(f"n{index}", RADIAL_VOLTAGE_LL_V / 1000.0)
        builder.line_z1(f"n{index - 1}", f"n{index}", segment_z, rating_a=400, line_id=f"l{index}")
    builder.load(f"n{buses - 1}", RADIAL_LOAD_P_W, RADIAL_LOAD_Q_VAR, load_id="load")
    network = builder.build()
    samples = []
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = solve_network(network)
        samples.append(time.perf_counter() - started)
    ordered = sorted(samples)
    assert result is not None
    voltages = [abs(item.complex) / item.v_base_ln_v for item in result.voltages if item.v_base_ln_v and item.phase in "ABC"]
    attempts = result.diagnostics.get("attempts") or []
    opendss_converged = any(bool(item.get("converged")) for item in attempts)
    return {
        "buses": float(buses),
        "repeats": float(repeats),
        "p50_s": ordered[len(ordered) // 2],
        "converged": 1.0 if result.status == "converged" else 0.0,
        "status": result.status,
        "pq_solution_exists": bool(limit["exists"]),
        "nose_voltage_pu": limit["high_voltage_pu"],
        "min_voltage_pu": min(voltages) if voltages else None,
        "pq_setpoint_error_pu": result.diagnostics.get("pq_setpoint_error_pu"),
        "opendss_reported_converged": opendss_converged,
        "iterations": float(result.iterations),
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
