"""Мутации напряжения, Q и P ловят уже найденную точку. Глобальный оптимум не заявлен."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np

from power_grid_env.network_opf import Branch, Bus, GeneratorSpec, OpfCase, audit_feasibility, solve_ac_opf


def _case() -> OpfCase:
    return OpfCase(
        name="two",
        base_mva=10.0,
        buses=[
            Bus(1, 3, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.9, 1.1),
            Bus(2, 1, 5.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.9, 1.1),
        ],
        generators=[GeneratorSpec(1, -5.0, 5.0, 0.0, 20.0, (0.0, 20.0, 0.0))],
        branches=[
            Branch(1, 2, 0.01, 0.1, 0.0, 20.0, 0.0, 0.0, 1, -30.0, 30.0),
        ],
    )


def qualify() -> dict[str, Any]:
    case = _case()
    solved = solve_ac_opf(case)
    vm = np.array(solved["vm_pu"])
    theta = np.radians(solved["va_deg"])
    pg = np.array(solved["pg_mw"])
    qg = np.array(solved["qg_mvar"])
    base = audit_feasibility(case, vm, theta, pg, qg)
    low_voltage = replace(case.buses[1], vmax=0.5)
    voltage_case = replace(case, buses=[case.buses[0], low_voltage])
    voltage_audit = audit_feasibility(voltage_case, vm, theta, pg, qg)
    tight_q = replace(case.generators[0], qmax=0.01)
    reactive_case = replace(case, generators=[tight_q])
    reactive_audit = audit_feasibility(reactive_case, vm, theta, pg, qg)
    tight_p = replace(case.generators[0], pmax=0.1)
    active_case = replace(case, generators=[tight_p])
    active_audit = audit_feasibility(active_case, vm, theta, pg, qg)
    refused = solve_ac_opf(active_case)
    power_error = _phase_power(float(vm[0]), float(pg[0]))
    reasons = []
    if not solved["success"] or not base["feasible"]:
        reasons.append("base")
    if voltage_audit["feasible"] or reactive_audit["feasible"] or active_audit["feasible"]:
        reasons.append("mutation")
    if refused["success"] or refused["global_optimum_claimed"]:
        reasons.append("resolve")
    if power_error > 1e-4:
        reasons.append("phase")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "voltage_feasible": voltage_audit["feasible"],
        "reactive_feasible": reactive_audit["feasible"],
        "active_feasible": active_audit["feasible"],
        "phase_power_error": power_error,
        "global_optimum_claimed": False,
    }


def _phase_power(slack_pu: float, pg_mw: float) -> float:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    builder = NetBuilder("opf", "Мутация", 10.0, s_base_va=10.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 1.0e-5, 1.0e-5, ll_v=slack_pu * 10_000.0)
    builder.line_z1("src", "ld", 0.1 + 1.0j, line_id="line")
    builder.load("ld", 5.0e6, 1.0e6, load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return 1.0
    measured_mw = -sum(item.p_into_w for item in solved.flows if item.equipment_id == "source" and item.phase in "ABC") / 1.0e6
    return abs(measured_mw - pg_mw) / max(abs(pg_mw), 1e-6)
