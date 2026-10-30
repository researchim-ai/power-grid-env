"""Каталог внешнего корпуса. Семейства и допуски не подменяют друг друга.

Пропуск канала и исчезновение объекта не становятся успехом. Упрощения SimBench
не переносятся на другую сеть. Полевой трек без парных измерений не оценивается.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from benchmarks.external.simbench_network import CROSS_SOLVER_VOLTAGE_PU
from power_grid_env.phase_oracle import ANGLE_RAD, KCL_PU, VOLTAGE_PU

HELD_OUT_FAMILY = "enwl"
_SAME_ENGINE_NODES = ("1", "2", "3", "4")

CATALOG: tuple[dict[str, Any], ...] = (
    {"id": "ieee13", "family": "ieee-pes", "tier": "same_engine"},
    {"id": "ieee34", "family": "ieee-pes", "tier": "same_engine"},
    {"id": "ieee123", "family": "ieee-pes", "tier": "same_engine"},
    {"id": "enwl-network23-feeder3", "family": "enwl", "tier": "same_engine"},
    {"id": "enwl-median", "family": "enwl", "tier": "same_engine"},
    {"id": "enwl-largest", "family": "enwl", "tier": "same_engine"},
    {"id": "1-LV-rural1--0-sw", "family": "simbench", "tier": "cross_solver_voltage"},
    {"id": "1-MV-rural--0-sw", "family": "simbench", "tier": "cross_solver_voltage"},
    {"id": "1-MV-comm--0-sw", "family": "simbench", "tier": "cross_solver_voltage"},
    {"id": "pglib_opf_case3_lmbd", "family": "pglib", "tier": "opf_local"},
    {"id": "pglib_opf_case5_pjm", "family": "pglib", "tier": "opf_local"},
    {"id": "pglib_opf_case14_ieee", "family": "pglib", "tier": "opf_local"},
    {"id": "pglib_opf_case118_ieee", "family": "pglib", "tier": "opf_local"},
    {"id": "plant-study", "family": "authored", "tier": "authored_pack"},
)


def tiers() -> dict[str, Any]:
    return {
        "same_engine": {"voltage_pu": VOLTAGE_PU, "angle_rad": ANGLE_RAD, "channels": ["voltage", "angle", "losses", "power", "current"]},
        "analytic": {"voltage_pu": VOLTAGE_PU, "angle_rad": ANGLE_RAD, "kcl_pu": KCL_PU, "channels": ["voltage", "angle", "kcl"]},
        "cross_solver_voltage": {"voltage_pu": CROSS_SOLVER_VOLTAGE_PU, "channels": ["voltage"], "angles_required": False},
        "opf_local": {"channels": ["feasibility"], "global_optimum_claimed": False},
        "authored_pack": {"channels": ["scenario_verdict"]},
        "field": {"channels": ["paired_measurements"]},
    }


def assess(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Каждая сеть оценивается своей записью. Успех одного семейства не закрывает другое."""
    by_id = {str(item.get("id")): item for item in results}
    rows = []
    for entry in CATALOG:
        rows.append(_one(entry, by_id.get(entry["id"])))
    families = sorted({entry["family"] for entry in CATALOG})
    held = [row for row in rows if row["family"] == HELD_OUT_FAMILY]
    rest = [row for row in rows if row["family"] != HELD_OUT_FAMILY]
    return {
        "networks": len(CATALOG),
        "families": families,
        "held_out_family": HELD_OUT_FAMILY,
        "rows": rows,
        "qualified_families_verdict": _summary(rest),
        "held_out_verdict": _summary(held),
        "verdict": _summary(rows),
        "field_status": "waiting_for_data",
        "cross_solver_voltage_pu": CROSS_SOLVER_VOLTAGE_PU,
        "analytic_voltage_pu": VOLTAGE_PU,
        "simbench_assumptions_are_local": True,
    }


def two_feeder_qsts(first_csv: Path | None, second_csv: Path | None) -> dict[str, Any]:
    """Нет своего CSV у фидера — случай закрыт. Чужой или случайный профиль не подставляется."""
    missing = [name for name, path in (("first", first_csv), ("second", second_csv)) if path is None or not Path(path).is_file()]
    if missing:
        return {"status": "blocked", "reason": "missing_csv", "missing": missing, "synthetic_profile_used": False, "intervals": 0}
    return {"status": "ready", "reason": "", "missing": [], "synthetic_profile_used": False, "intervals": 0}


def analytic_member() -> dict[str, Any]:
    """Двухшинная схема против делителя. Допуски аналитического яруса, не 1e-4 SimBench."""
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.checks import kcl_report
    from power_grid_env.topology import compile_topology

    load_z = 20.0 + 5.0j
    source_z = 0.05 + 0.2j
    line_z = 0.3 + 0.8j
    line_ln = 10_000.0 / math.sqrt(3.0)
    power = 3.0 * (line_ln**2 / load_z.conjugate())
    builder = NetBuilder("corpus", "Корпус", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", source_z.real, source_z.imag)
    builder.line_z1("src", "ld", line_z, line_id="line")
    builder.load("ld", float(power.real), float(power.imag), model="constant_z", load_id="load")
    network = builder.build()
    solved = solve_network(network)
    bus = compile_topology(network).node_to_bus["ld"]
    voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    expected = line_ln * load_z / (source_z + line_z + load_z)
    voltage_error = abs(abs(voltage) - abs(expected)) / line_ln
    angle_error = abs(_wrap(math.atan2(voltage.imag, voltage.real) - math.atan2(expected.imag, expected.real)))
    mismatch = kcl_report(network, solved)["mismatch_pu"]
    reasons = []
    if solved.status != "converged":
        reasons.append("status")
    if voltage_error > VOLTAGE_PU:
        reasons.append("voltage")
    if angle_error > ANGLE_RAD:
        reasons.append("angle")
    if mismatch > KCL_PU:
        reasons.append("kcl")
    return {
        "id": "analytic-two-bus",
        "tier": "analytic",
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "voltage_error_pu": voltage_error,
        "angle_error_rad": angle_error,
        "kcl_pu": mismatch,
    }


def _one(entry: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    base = {"id": entry["id"], "family": entry["family"], "tier": entry["tier"]}
    if result is None:
        return {**base, "status": "incomplete", "reason": "missing"}
    if result.get("inherited_simplifications") and entry["family"] != "simbench":
        return {**base, "status": "fail", "reason": "inherited_simbench"}
    tier = entry["tier"]
    if tier == "same_engine":
        present = set(result.get("nodes") or [])
        if not set(_SAME_ENGINE_NODES) <= present:
            return {**base, "status": "fail", "reason": "missing_node"}
        channels = set(result.get("channels") or [])
        required = set(tiers()["same_engine"]["channels"])
        if not required <= channels:
            return {**base, "status": "incomplete", "reason": "missing_channel"}
        if result.get("voltage_error_pu") is None or float(result["voltage_error_pu"]) > VOLTAGE_PU:
            return {**base, "status": "fail", "reason": "voltage"}
        if result.get("angle_error_rad") is None or float(result["angle_error_rad"]) > ANGLE_RAD:
            return {**base, "status": "fail", "reason": "angle"}
        return {**base, "status": "pass", "reason": ""}
    if tier == "cross_solver_voltage":
        if result.get("angles_compared") or result.get("same_engine_gate_claimed"):
            return {**base, "status": "fail", "reason": "tier_renamed"}
        if not result.get("assumptions"):
            return {**base, "status": "incomplete", "reason": "assumptions"}
        if result.get("voltage_error_pu") is None or float(result["voltage_error_pu"]) > CROSS_SOLVER_VOLTAGE_PU:
            return {**base, "status": "fail", "reason": "voltage"}
        return {**base, "status": "pass", "reason": ""}
    if tier == "opf_local":
        if result.get("global_optimum_claimed"):
            return {**base, "status": "fail", "reason": "global_claim"}
        if result.get("feasible") is not True:
            return {**base, "status": "fail", "reason": "feasibility"}
        return {**base, "status": "pass", "reason": ""}
    if tier == "authored_pack":
        if result.get("profile_released"):
            return {**base, "status": "fail", "reason": "profile_released"}
        if result.get("verdict") != "pass":
            return {**base, "status": "fail", "reason": "verdict"}
        return {**base, "status": "pass", "reason": ""}
    return {**base, "status": "incomplete", "reason": "tier"}


def _summary(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "incomplete"
    if any(row["status"] == "fail" for row in rows):
        return "fail"
    if any(row["status"] != "pass" for row in rows):
        return "incomplete"
    return "pass"


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
