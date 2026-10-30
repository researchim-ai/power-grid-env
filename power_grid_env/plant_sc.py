"""Короткое замыкание площадки. Формула одного источника на два ввода не переносится.

Пик и ток отключения считаются для объявленного одного эквивалента. Это не весь IEC 60909
и не модель заводского терминала.
"""

from __future__ import annotations

import math
from typing import Any

from power_grid_env.backend_opendss import solve_network
from power_grid_env.iec60909 import (
    breaking_current,
    initial_symmetrical_single_line_to_ground,
    initial_symmetrical_three_phase,
    peak_current,
    peak_factor,
)
from power_grid_env.model import StudyFault
from power_grid_env.plant_study import build_plant
from power_grid_env.protection import clearing_times, iec_operate_time

_SINGLE_Z = 0.4 + 2.0j
_NOMINAL_LL_V = 110_000.0
_C = 1.0


def normative_three_phase(sources: int, nominal_ll_v: float, c: float, impedance: complex) -> dict[str, Any]:
    """I''k одного эквивалента. При двух вводах формула не применяется."""
    if sources != 1:
        return {
            "applicable": False,
            "reason": "mesh_not_single_source",
            "current_a": None,
            "standard_compliance_claimed": False,
        }
    current = initial_symmetrical_three_phase(nominal_ll_v, c, impedance)
    return {
        "applicable": True,
        "reason": "",
        "current_a": abs(current),
        "current": current,
        "standard_compliance_claimed": False,
    }


def qualify_plant_faults() -> dict[str, Any]:
    """Вклад ввода зависит от места КЗ. Селективность и чувствительность проверяются на этом токе."""
    refused = normative_three_phase(2, _NOMINAL_LL_V, 1.1, _SINGLE_Z)
    single = normative_three_phase(1, _NOMINAL_LL_V, _C, _SINGLE_Z)
    formula = abs(initial_symmetrical_three_phase(_NOMINAL_LL_V, _C, _SINGLE_Z))
    weaker = abs(initial_symmetrical_three_phase(_NOMINAL_LL_V, _C, _SINGLE_Z * 2.0))
    solid = abs(initial_symmetrical_single_line_to_ground(_NOMINAL_LL_V, _C, _SINGLE_Z, _SINGLE_Z, 1.0 + 4.0j))
    reactor = abs(initial_symmetrical_single_line_to_ground(_NOMINAL_LL_V, _C, _SINGLE_Z, _SINGLE_Z, 8.0 + 20.0j))
    near = _plant_fault("mv_a")
    far = _plant_fault("mv_b")
    ordered = clearing_times(
        near["own_a"],
        [("near", 80.0, "standard_inverse", 0.05), ("upstream", 80.0, "standard_inverse", 0.2)],
    )
    swapped = clearing_times(
        near["own_a"],
        [("near", 80.0, "standard_inverse", 0.2), ("upstream", 80.0, "standard_inverse", 0.05)],
    )
    blind = iec_operate_time(near["own_a"], 10_000.0, "standard_inverse", 0.05)
    kappa = peak_factor(_SINGLE_Z.real / _SINGLE_Z.imag)
    peak = peak_current(single["current"], kappa)
    breaking, factor = breaking_current(single["current"], near_synchronous=True, short_circuit_over_rated=5.0, minimum_time_s=0.10)
    reasons = []
    if refused["applicable"] or refused["current_a"] is not None:
        reasons.append("mesh_formula")
    if single["current_a"] is None or abs(single["current_a"] - formula) / formula > 1e-12:
        reasons.append("single")
    if weaker >= single["current_a"] * 0.6:
        reasons.append("source")
    if abs(reactor - solid) / solid < 0.05:
        reasons.append("neutral")
    if near["status"] != "converged" or far["status"] != "converged":
        reasons.append("status")
    if not near["own_a"] > 10.0 * near["other_a"] or not far["other_a"] > 10.0 * far["own_a"]:
        reasons.append("location")
    if abs(near["own_a"] - formula) / formula < 0.05:
        reasons.append("substituted_formula")
    if ordered[0][0] != "near" or not ordered[0][1] < ordered[1][1]:
        reasons.append("selectivity")
    if swapped[0][0] == "near":
        reasons.append("reversed_settings")
    if not math.isinf(blind):
        reasons.append("sensitivity")
    if peak <= math.sqrt(2.0) * formula or abs(breaking) > formula or factor > 1.0:
        reasons.append("duty")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "near_current_a": near["own_a"],
        "far_current_a": far["other_a"],
        "near_time_s": ordered[0][1],
        "upstream_time_s": ordered[1][1],
        "peak_a": peak,
        "breaking_factor": factor,
        "standard_compliance_claimed": False,
        "vendor_relay_claimed": False,
    }


def _plant_fault(bus_id: str) -> dict[str, Any]:
    network = build_plant()
    network.study_faults.append(StudyFault(id="fault", bus_id=bus_id, kind="3ph", phases=["A", "B", "C"], r_ohm=1.0))
    solved = solve_network(network)
    return {
        "status": solved.status,
        "own_a": _phase_current(solved, "in_a"),
        "other_a": _phase_current(solved, "in_b"),
    }


def _phase_current(solved, equipment_id: str) -> float:
    found = [abs(item.current) for item in solved.flows if item.equipment_id == equipment_id and item.phase == "A"]
    return found[0] if found else 0.0
