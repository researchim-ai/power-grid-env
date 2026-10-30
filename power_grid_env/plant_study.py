"""Площадка 110/10/0,4 кВ и замороженный набор режимов.

Динамический самозапуск группы двигателей в этот набор не входит и остаётся видимым отказом.
Натурным измерением результат не становится.
"""

from __future__ import annotations

import math
from typing import Any

from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder
from power_grid_env.model import HarmonicComponent, Network, Omission, StudyFault
from power_grid_env.protection import ats_decision, breaker_failure_time, critical_stays_supplied, directional_allows, iec_operate_time
from power_grid_env.study_spec import StudySpec, run_study
from power_grid_env.topology import compile_topology

VOLTAGE_MIN_PU = 0.95
_LIGHT_W = 200_000.0
_HEAVY_W = 3_200_000.0
_FAULT_R_OHM = 1.0
_PICKUP_SENSITIVE_A = 50.0
_PICKUP_BLIND_A = 5_000.0


def build_plant() -> Network:
    builder = NetBuilder("plant-study", "Площадка 110/10/0.4", 110.0, s_base_va=10.0e6)
    builder.node("hv_a", 110.0)
    builder.node("hv_b", 110.0)
    builder.node("mv_a", 10.0)
    builder.node("mv_b", 10.0)
    builder.node("lv", 0.4)
    builder.source("hv_a", 0.4, 2.0, source_id="in_a")
    builder.source("hv_b", 0.4, 2.0, source_id="in_b")
    builder.transformer("hv_a", "mv_a", vector_group="YNyn0", kva=10_000, hv_kv=110.0, lv_kv=10.0, x_pct=10.0, r_pct=0.5, transformer_id="t_a")
    builder.transformer("hv_b", "mv_b", vector_group="YNyn0", kva=10_000, hv_kv=110.0, lv_kv=10.0, x_pct=10.0, r_pct=0.5, transformer_id="t_b")
    builder.switch("mv_a", "mv_b", switch_id="section", closed=False, kind="breaker")
    builder.transformer(
        "mv_a",
        "lv",
        vector_group="Dyn11",
        kva=1_600,
        hv_kv=10.0,
        lv_kv=0.4,
        x_pct=6.0,
        r_pct=1.0,
        hv_ground="not_applicable",
        lv_ground="solid",
        transformer_id="t_lv",
    )
    builder.load("lv", _LIGHT_W, _LIGHT_W * 0.2, load_id="critical", critical=True)
    builder.load("mv_b", 400_000.0, 100_000.0, load_id="motors")
    builder.inverter("mv_b", s_rated_va=400_000.0, p_w=50_000.0, inverter_id="pv")
    builder.storage("lv", e_j=1.0e9, soc=0.5, p_w=0.0, s_rated_va=200_000.0, storage_id="bess")
    builder.meter("lv", meter_id="meter_lv")
    builder.meter("mv_a", meter_id="meter_mv_a")
    network = builder.build()
    network.omissions.append(
        Omission(
            object_id="motors",
            status="approximate",
            power_flow="approximate",
            protection="unsupported",
            note="группа двигателей задана мощностью. Динамический пуск и опрокидывание этим исследованием не считаются",
        )
    )
    network.assumptions.append("секционный аппарат нормально разомкнут: два ввода не работают параллельно")
    return network


def run_plant_study() -> dict[str, Any]:
    """Один проход замороженного набора. Профиль поставки этим не открывается."""
    base = build_plant()
    rows = [
        _normal(base),
        _load_case(base, "light_load", _LIGHT_W, False),
        _load_case(base, "heavy_load", _HEAVY_W, True),
        _transfer(base),
        _both_out(base),
        _parallel(base),
        _fault(base, "fault_mv_a", "mv_a"),
        _fault(base, "fault_mv_b", "mv_b"),
        _pickup(base, "sensitive_pickup", _PICKUP_SENSITIVE_A, False),
        _pickup(base, "blind_pickup", _PICKUP_BLIND_A, True),
        _breaker(False),
        _breaker(True),
        _missing_sensor(),
        _ats(False),
        _ats(True),
        _critical(False),
        _critical(True),
        _motor(base),
        _harmonic(base),
        _locked(base),
        _section_interlock(base),
        _weak_source(base),
        _qsts(base),
    ]
    reasons = [row["id"] for row in rows if row["violation"] != row["expected_violation"]]
    fault_a = next(row for row in rows if row["id"] == "fault_mv_a")
    fault_b = next(row for row in rows if row["id"] == "fault_mv_b")
    light = next(row for row in rows if row["id"] == "light_load")
    heavy = next(row for row in rows if row["id"] == "heavy_load")
    if fault_a["fed_from"] == fault_b["fed_from"]:
        reasons.append("fault_location")
    if not light["voltage_pu"] > heavy["voltage_pu"]:
        reasons.append("load")
    if light["violation"] or not heavy["violation"]:
        reasons.append("voltage_band")
    expected = [row["id"] for row in rows if row["expected_violation"]]
    if len(rows) < 20 or len(expected) < 5:
        reasons.append("manifest")
    if not any(item.object_id == "motors" for item in base.omissions):
        reasons.append("motor_omission")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "scenarios": rows,
        "scenario_count": len(rows),
        "expected_violations": expected,
        "losses_w": next(row["losses_w"] for row in rows if row["id"] == "normal"),
        "dynamic_motor_start": False,
        "profile_released": False,
        "field_status": "waiting_for_data",
    }


def _normal(network: Network) -> dict[str, Any]:
    solved = solve_network(network)
    voltage = _voltage(network, solved, "lv")
    losses = _losses(solved)
    return _row("normal", solved.status, not (voltage >= VOLTAGE_MIN_PU and losses > 100.0), False, voltage_pu=voltage, losses_w=losses)


def _load_case(network: Network, name: str, power_w: float, expected: bool) -> dict[str, Any]:
    edited = network.model_copy(deep=True)
    _set_load(edited, "critical", power_w, power_w * 0.2)
    solved = solve_network(edited)
    voltage = _voltage(edited, solved, "lv")
    violation = voltage < VOLTAGE_MIN_PU
    return _row(name, solved.status, violation, expected, voltage_pu=voltage)


def _transfer(network: Network) -> dict[str, Any]:
    edited = _without(network, "in_a")
    _switch(edited, "section").closed = True
    solved = solve_network(edited)
    voltage = _voltage(edited, solved, "lv")
    return _row("transfer", solved.status, voltage < 0.9, False, voltage_pu=voltage)


def _both_out(network: Network) -> dict[str, Any]:
    edited = _without(_without(network, "in_a"), "in_b")
    solved = solve_network(edited)
    voltage = _voltage(edited, solved, "lv")
    return _row("both_out", solved.status, solved.status == "deenergized_no_source" and voltage < 0.05, True, voltage_pu=voltage)


def _parallel(network: Network) -> dict[str, Any]:
    edited = network.model_copy(deep=True)
    _switch(edited, "section").closed = True
    solved = solve_network(edited)
    return _row("parallel_forbidden", solved.status, solved.status == "invalid_model", True)


def _fault(network: Network, name: str, bus_id: str) -> dict[str, Any]:
    edited = network.model_copy(deep=True)
    edited.study_faults.append(StudyFault(id="fault", bus_id=bus_id, kind="3ph", phases=["A", "B", "C"], r_ohm=_FAULT_R_OHM))
    solved = solve_network(edited)
    current_a = _current(solved, "in_a")
    current_b = _current(solved, "in_b")
    fed = "a" if current_a > current_b else "b"
    voltage = _voltage(edited, solved, "lv")
    return _row(name, solved.status, False, False, fed_from=fed, current_a=current_a, current_b=current_b, voltage_pu=voltage)


def _pickup(network: Network, name: str, pickup_a: float, expected: bool) -> dict[str, Any]:
    faulted = _fault(network, "probe", "mv_a")
    operate = iec_operate_time(faulted["current_a"], pickup_a, "standard_inverse", 0.1)
    blind = math.isinf(operate)
    return _row(name, "protection", blind, expected, operate_s=operate if math.isfinite(operate) else None)


def _breaker(failed: bool) -> dict[str, Any]:
    delay = breaker_failure_time(0.1, 0.2, 0.5, failed=failed)
    return _row("breaker_failed" if failed else "breaker_ok", "protection", failed, failed, clear_s=delay)


def _missing_sensor() -> dict[str, Any]:
    allowed = directional_allows(0.0j, 100.0 + 0.0j, forward=True)
    return _row("missing_sensor", "protection", not allowed, True)


def _ats(reserve_dead: bool) -> dict[str, Any]:
    decision = ats_decision(0.0j, 0.0j if reserve_dead else 10_000.0 + 0.0j, 10_000.0, 10_000.0, primary_closed=True, reserve_closed=False, reserve_available=True, max_angle_rad=0.3, max_voltage_pu=0.2, dead_pu=0.2)
    blocked = decision["mode"] == "blocked"
    return _row("ats_failed" if reserve_dead else "ats_transfer", str(decision["mode"]), blocked, reserve_dead)


def _critical(lost: bool) -> dict[str, Any]:
    supplied = critical_stays_supplied("t_lv" if lost else "section", {"t_lv": {"lv"}, "section": set()}, "lv")
    return _row("critical_lost" if lost else "critical_kept", "topology", not supplied, lost)


def _motor(network: Network) -> dict[str, Any]:
    report = run_study(StudySpec(id="motor", study="power_flow", backend="opendss", network=network, requested_motors=["motor_group"]), _forbid_solve)
    visible = report["solved"] is False and report["reason"] == "unsupported_motor"
    return _row("motor_rejected", report["reason"], visible, True)


def _harmonic(network: Network) -> dict[str, Any]:
    edited = network.model_copy(deep=True)
    edited.harmonics.append(HarmonicComponent(order=5, current_a=1.0))
    from power_grid_env.scope import compile_scope

    scoped = compile_scope(edited, "harmonic")
    visible = scoped["ok"] is False and any("не привязан" in item for item in scoped["errors"])
    return _row("harmonic_rejected", "scope", visible, True)


def _locked(network: Network) -> dict[str, Any]:
    from power_grid_env.scenarios import Scenario
    from power_grid_env.simulation import Engine

    edited = network.model_copy(deep=True)
    _switch(edited, "section").locked = True
    decision = Engine(edited, Scenario(id="lock", horizon_s=60)).validate_action({"kind": "switch", "equipment_id": "section", "command": "close"}, enforce_hidden_current=False)
    return _row("locked_switch", decision["reason"], decision["reason"] == "locked", True)


def _section_interlock(network: Network) -> dict[str, Any]:
    from power_grid_env.scenarios import Scenario
    from power_grid_env.simulation import Engine

    decision = Engine(network, Scenario(id="section", horizon_s=60)).validate_action({"kind": "switch", "equipment_id": "section", "command": "close"}, enforce_hidden_current=False)
    return _row("section_interlock", decision["reason"], decision["reason"] == "parallel_infeed", True)


def _weak_source(network: Network) -> dict[str, Any]:
    edited = network.model_copy(deep=True)
    _set_load(edited, "critical", _HEAVY_W, _HEAVY_W * 0.2)
    source = next(item for item in edited.sources if item.id == "in_a")
    source.r_ohm = 20.0
    source.x_ohm = 80.0
    solved = solve_network(edited)
    voltage = _voltage(edited, solved, "lv")
    return _row("weak_source", solved.status, voltage < VOLTAGE_MIN_PU, True, voltage_pu=voltage)


def _qsts(network: Network) -> dict[str, Any]:
    from power_grid_env.scenarios import Scenario
    from power_grid_env.simulation import Engine

    engine = Engine(network, Scenario(id="plant-qsts", horizon_s=60.0, control_interval_s=60.0, settle_s=1.0))
    engine.reset()
    engine.advance_to(60.0)
    solved = engine.last_result
    voltage = _voltage(engine.net, solved, "lv") if solved is not None else 0.0
    ok = engine.time_s == 60.0 and solved is not None and solved.status == "converged" and voltage >= VOLTAGE_MIN_PU
    return _row("qsts_minute", solved.status if solved is not None else "missing", not ok, False, voltage_pu=voltage)


def _row(name: str, status: str, violation: bool, expected: bool, **fields: Any) -> dict[str, Any]:
    return {"id": name, "status": status, "violation": violation, "expected_violation": expected, **fields}


def _without(network: Network, source_id: str) -> Network:
    edited = network.model_copy(deep=True)
    edited.sources = [item for item in edited.sources if item.id != source_id]
    return edited


def _switch(network: Network, switch_id: str):
    return next(item for item in network.switches if item.id == switch_id)


def _set_load(network: Network, load_id: str, active_w: float, reactive_var: float) -> None:
    load = next(item for item in network.loads if item.id == load_id)
    share = len(load.phases)
    for phase in load.phases:
        load.p_w[phase] = active_w / share
        load.q_var[phase] = reactive_var / share


def _voltage(network: Network, solved, node_id: str) -> float:
    bus = compile_topology(network).node_to_bus[node_id]
    found = [item for item in solved.voltages if item.bus_id == bus and item.phase == "A"]
    if not found or found[0].v_base_ln_v == 0:
        return 0.0
    return abs(found[0].complex) / found[0].v_base_ln_v


def _current(solved, equipment_id: str) -> float:
    found = [abs(item.current) for item in solved.flows if item.equipment_id == equipment_id and item.phase == "A"]
    return found[0] if found else 0.0


def _losses(solved) -> float:
    generated = -sum(item.p_into_w for item in solved.flows if item.equipment_id in {"in_a", "in_b", "pv", "bess"} and item.phase in "ABC")
    consumed = sum(item.p_into_w for item in solved.flows if item.equipment_id in {"critical", "motors"} and item.phase in "ABC")
    return generated - consumed


def _forbid_solve(_network: Network):
    raise AssertionError("расчёт не должен стартовать")
