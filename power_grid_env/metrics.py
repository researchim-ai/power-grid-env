"""Эксплуатационные метрики. Награда — производный сигнал, не замена отчёта.

Обесточенная нагрузка увеличивает недоотпуск. Нарушение напряжения считается
только на питаемых узлах: нулевое напряжение острова без источника не
дублируется как отдельное нарушение полосы.
"""

from __future__ import annotations

import math
from typing import Any


def phase_voltage_pu(magnitude_v: float, v_base_ln_v: float) -> float:
    if v_base_ln_v <= 0:
        return 0.0
    return magnitude_v / v_base_ln_v


def unserved_fraction(phase_pu: list[float], v_min_pu: float) -> float:
    """Нагрузка обслужена только если все её фазы не ниже порога службы.

    Промежуточное напряжение не считается частичной поставкой энергии: недоотпуск
    и отклонение напряжения считаются разными метриками, но порог службы общий.
    """
    if not phase_pu or max(phase_pu) <= 0.05:
        return 1.0
    if min(phase_pu) >= v_min_pu:
        return 0.0
    return 1.0


def step_reward(
    *,
    energy_not_served_wh: float,
    interlock_attempts: int,
    voltage_pu_seconds: float,
    overload_pu_seconds: float,
    operations: int,
) -> float:
    """Штраф шага. Жёсткие нарушения остаются в отдельных метриках."""
    return (
        -energy_not_served_wh / 1.0e3
        - 50.0 * interlock_attempts
        - 10.0 * voltage_pu_seconds
        - 5.0 * overload_pu_seconds
        - 0.01 * operations
    )


def summarize_episode(steps: list[dict[str, Any]], horizon_s: float) -> dict[str, Any]:
    """Сводит журнал шагов. Неполный эпизод не объявляется нулевым недоотпуском."""
    if not steps:
        return {
            "status": "unassessed",
            "energy_not_served_wh": None,
            "coverage_s": 0.0,
            "horizon_s": horizon_s,
            "incomplete": True,
        }
    ens = 0.0
    critical_served = 0.0
    critical_demand = 0.0
    voltage_duration = 0.0
    voltage_integral = 0.0
    overload_duration = 0.0
    overload_integral = 0.0
    interlocks = 0
    rejected = 0
    operations = 0
    timeouts = 0
    last_time = 0.0
    restored_at = None
    currently_ok_since = None
    solver_failed = False
    for step in steps:
        dt = float(step["dt_s"])
        last_time = float(step["time_s"])
        ens += float(step["energy_not_served_wh"])
        critical_served += float(step["critical_served_wh"])
        critical_demand += float(step["critical_demand_wh"])
        voltage_duration += float(step["voltage_violation_s"])
        voltage_integral += float(step["voltage_violation_pu_s"])
        overload_duration += float(step["overload_s"])
        overload_integral += float(step["overload_pu_s"])
        interlocks += int(step["interlock_attempts"])
        rejected += int(step["rejected_actions"])
        operations += int(step["operations"])
        timeouts += int(step.get("timeouts", 0))
        if step.get("solve_status") not in {"converged", "deenergized_no_source"}:
            solver_failed = True
        if step.get("restorable_ok"):
            if currently_ok_since is None:
                currently_ok_since = float(step["ok_since_s"])
        else:
            currently_ok_since = None
    if currently_ok_since is not None and not solver_failed:
        restored_at = currently_ok_since
    # Раннее завершение после восстановления — полный успех задачи.
    # Неполный эпизод — это обрыв расчёта или горизонта без восстановления.
    incomplete = solver_failed or (last_time + 1e-6 < horizon_s and restored_at is None)
    critical_fraction = None if critical_demand <= 0 else critical_served / critical_demand
    return {
        "status": "incomplete" if incomplete else "complete",
        "incomplete": incomplete,
        "energy_not_served_wh": ens,
        "energy_not_served_is_lower_bound": incomplete,
        "critical_demand_wh": critical_demand,
        "critical_served_wh": critical_served,
        "critical_served_fraction": critical_fraction,
        "restoration_time_s": restored_at,
        "restored": restored_at is not None and not incomplete,
        "voltage_violation_duration_s": voltage_duration,
        "voltage_violation_pu_s": voltage_integral,
        "overload_duration_s": overload_duration,
        "overload_pu_s": overload_integral,
        "interlock_violations": interlocks,
        "rejected_actions": rejected,
        "operations": operations,
        "timeouts": timeouts,
        "coverage_s": last_time,
        "horizon_s": horizon_s,
        "reward": step_reward(
            energy_not_served_wh=ens,
            interlock_attempts=interlocks,
            voltage_pu_seconds=voltage_integral,
            overload_pu_seconds=overload_integral,
            operations=operations,
        ),
    }


def compare_pair(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Парное сравнение. Больше недоотпуска, нарушений или времени — хуже."""
    worse: list[str] = []
    unknown: list[str] = []

    def worse_higher(key: str, tolerance: float) -> None:
        left, right = baseline.get(key), candidate.get(key)
        if left is None or right is None:
            unknown.append(key)
            return
        if float(right) > float(left) + tolerance:
            worse.append(key)

    worse_higher("energy_not_served_wh", 1.0)
    worse_higher("voltage_violation_duration_s", 1e-6)
    worse_higher("overload_duration_s", 1e-6)
    worse_higher("interlock_violations", 0.0)
    worse_higher("rejected_actions", 0.0)
    base_time = baseline.get("restoration_time_s")
    cand_time = candidate.get("restoration_time_s")
    if baseline.get("restored") and not candidate.get("restored"):
        worse.append("restoration_time_s")
    elif baseline.get("restored") and candidate.get("restored") and cand_time is not None and base_time is not None:
        if float(cand_time) > float(base_time) + 1e-6:
            worse.append("restoration_time_s")
    elif not baseline.get("restored") and not candidate.get("restored"):
        unknown.append("restoration_time_s")
    return {
        "candidate_worse_on": worse,
        "unassessed_metrics": unknown,
        "candidate_has_violation_baseline_does_not": (
            int(candidate.get("interlock_violations") or 0) > int(baseline.get("interlock_violations") or 0)
            or float(candidate.get("overload_duration_s") or 0) > float(baseline.get("overload_duration_s") or 0) + 1e-6
        ),
    }


def finite_or_none(value: float) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return float(value)
