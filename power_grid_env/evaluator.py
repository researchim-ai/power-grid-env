"""Независимая оценка по журналу исполнения.

Самоотчёт контроллера не используется. Если расчёт оборвался, остаток горизонта
не считается безопасным и не обнуляет недоотпуск.
"""

from __future__ import annotations

from typing import Any

from power_grid_env.metrics import summarize_episode


def evaluate_trace(trace: list[dict[str, Any]], horizon_s: float, controller_outcome: str) -> dict[str, Any]:
    physical = summarize_episode(trace, horizon_s)
    solve_failures = [step["time_s"] for step in trace if step.get("solve_status") not in {"converged", "deenergized_no_source", None}]
    physical_violations = [item for step in trace for item in step.get("violations", [])]
    episode_outcome = controller_outcome
    if solve_failures and controller_outcome == "completed":
        episode_outcome = "simulation_failure"
    if controller_outcome in {"timeout", "controller_crash", "invalid_action"}:
        category = "controller_failure"
    elif episode_outcome == "simulation_failure":
        category = "simulation_failure"
    elif physical_violations or physical.get("interlock_violations"):
        category = "physical_violation"
    elif physical.get("incomplete"):
        category = "incomplete"
    else:
        category = "completed_episode"
    return {
        **physical,
        "controller_outcome": controller_outcome,
        "episode_outcome": episode_outcome,
        "category": category,
        "solve_failure_times_s": solve_failures,
        "violation_count": len(physical_violations),
        "controller_self_report_ignored": True,
    }
