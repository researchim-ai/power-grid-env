"""Горизонт Engine в сутках. Шаг управления — одни сутки, схема двухшинная.

Это проверка интегралов и ограниченного следа, не расчёт городского фидера
и не 15-минутный профиль.
"""

from __future__ import annotations

import json
from typing import Any

from power_grid_env.build import NetBuilder
from power_grid_env.conventions import ll_to_ln
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine

DAY_S = 86_400.0
TRACE_LIMIT = 2


def _network():
    builder = NetBuilder("horizon", "Горизонт", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.05, 0.2)
    builder.line_z1("src", "ld", 0.2 + 0.4j, line_id="line")
    builder.load("ld", 100_000.0, 10_000.0, model="constant_z", critical=True, load_id="load")
    return builder.build()


def _scenario(days: int) -> Scenario:
    horizon = days * DAY_S
    return Scenario(
        id=f"horizon-{days}",
        horizon_s=horizon,
        control_interval_s=DAY_S,
        settle_s=horizon + DAY_S,
        trace_limit=TRACE_LIMIT,
    )


def run_horizon(days: int) -> dict[str, Any]:
    if days < 1:
        raise ValueError("горизонт задаётся положительным числом суток")
    network = _network()
    engine = Engine(network, _scenario(days))
    engine.reset()
    for _ in range(days):
        engine.step_control({"kind": "hold"})
    demand_w = sum(network.loads[0].p_w.values())
    expected_wh = demand_w * days * 24.0
    totals = engine.total_integrals()
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.topology import compile_topology

    cold = solve_network(network)
    bus = compile_topology(network).node_to_bus["ld"]
    live = next(item.complex for item in engine.last_result.voltages if item.bus_id == bus and item.phase == "A")
    reference = next(item.complex for item in cold.voltages if item.bus_id == bus and item.phase == "A")
    reasons = []
    if engine.time_s != days * DAY_S:
        reasons.append("time")
    if len(engine.trace) > TRACE_LIMIT:
        reasons.append("trace_unbounded")
    if days > TRACE_LIMIT and engine.dropped_trace_steps != days - TRACE_LIMIT:
        reasons.append("dropped")
    if abs(totals["critical_demand_wh"] - expected_wh) > 1e-3:
        reasons.append("integral")
    if abs(abs(live) - abs(reference)) / ll_to_ln(10_000.0) > 1e-5:
        reasons.append("voltage")
    if engine.last_result is None or engine.last_result.status != "converged":
        reasons.append("status")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "days": days,
        "step_s": DAY_S,
        "not_a_feeder_study": True,
        "trace_len": len(engine.trace),
        "dropped_trace_steps": engine.dropped_trace_steps,
        "critical_demand_wh": totals["critical_demand_wh"],
        "expected_wh": expected_wh,
        "engine": engine,
    }


def replay_matches(days: int = 7) -> dict[str, Any]:
    """Середина горизонта восстанавливается и доходит до того же интеграла."""
    full = run_horizon(days)
    network = _network()
    partial = Engine(network, _scenario(days))
    partial.reset()
    midpoint = days // 2
    for _ in range(midpoint):
        partial.step_control({"kind": "hold"})
    raw = json.loads(json.dumps(partial.snapshot()))
    restored = Engine(network.model_copy(deep=True), partial.scenario)
    restored.restore(raw)
    for _ in range(days - midpoint):
        restored.step_control({"kind": "hold"})
    reasons = list(full["reasons"])
    if restored.time_s != full["engine"].time_s:
        reasons.append("replay_time")
    if abs(restored.total_integrals()["critical_demand_wh"] - full["critical_demand_wh"]) > 1e-3:
        reasons.append("replay_integral")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "days": days,
        "replay_wh": restored.total_integrals()["critical_demand_wh"],
    }
