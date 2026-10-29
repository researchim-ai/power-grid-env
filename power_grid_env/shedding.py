"""Частотная разгрузка. Снятая нагрузка не остаётся в балансе мощности."""

from __future__ import annotations

from typing import Any

from power_grid_env.protection import underfrequency_shed

STAGES = [(49.0, ["aux", "critical"]), (48.0, ["heat"])]
CRITICAL = {"critical"}
LOADS = {"critical": 100_000.0, "aux": 80_000.0, "heat": 50_000.0}


def qualify() -> dict[str, Any]:
    above = underfrequency_shed(49.5, STAGES, CRITICAL)
    first = underfrequency_shed(48.5, STAGES, CRITICAL)
    both = underfrequency_shed(47.5, STAGES, CRITICAL)
    blind = underfrequency_shed(47.0, STAGES, CRITICAL, available=False)
    kept = sum(power for name, power in LOADS.items() if name not in both)
    measured = _remaining_power(kept)
    reasons = []
    if above or first != ["aux"] or both != ["aux", "heat"] or "critical" in both or blind:
        reasons.append("stages")
    if measured[1] != "converged":
        reasons.append("status")
    if measured[2] > 1e-4:
        reasons.append("power")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "shed": both,
        "kept_w": kept,
        "power_error": measured[2],
    }


def _remaining_power(active_w: float) -> tuple[float, str, float]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    builder = NetBuilder("shed", "Разгрузка", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 0.01, 0.02)
    builder.load("src", active_w, 0.0, load_id="kept")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return 0.0, solved.status, 1.0
    measured = -sum(item.p_into_w for item in solved.flows if item.equipment_id == "source" and item.phase in "ABC")
    return measured, solved.status, abs(measured - active_w) / active_w
