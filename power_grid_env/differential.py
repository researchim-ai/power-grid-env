"""Дифференциальная зона трансформатора YNyn0 по токам фазного расчёта.

Уставка 0.2 тока низкой стороны задана до просмотра небаланса.
Динамическая корректность фильтра не заявляется.
"""

from __future__ import annotations

import math
from typing import Any

from power_grid_env.protection import transformer_differential

SLOPE = 0.3
PICKUP_FRACTION = 0.2


def _transformer():
    from power_grid_env.build import NetBuilder

    builder = NetBuilder("diff", "Дифзащита", 10.0, s_base_va=1.0e6)
    builder.node("hv", 10.0)
    builder.node("lv", 0.4)
    builder.source("hv", 0.05, 0.2)
    builder.transformer(
        "hv",
        "lv",
        vector_group="YNyn0",
        kva=1000,
        hv_kv=10.0,
        lv_kv=0.4,
        x_pct=6.0,
        r_pct=1.0,
        hv_ground="solid",
        lv_ground="solid",
        transformer_id="t",
    )
    builder.load("lv", 300_000.0, 40_000.0, model="constant_z", load_id="load")
    network = builder.build()
    transformer = network.transformers[0]
    transformer.ppm_antifloat = 0.0
    transformer.imag_pct = 0.0
    transformer.no_load_loss_pct = 0.0
    network.sources[0].r0_ohm = 0.05
    network.sources[0].x0_ohm = 0.2
    return network


def _zone_fault_currents() -> tuple[complex, complex]:
    """КЗ между двумя трансформаторами зоны. Токи берутся на внешних выводах."""
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.model import StudyFault
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("zone", "Внутреннее", 10.0, s_base_va=1.0e6)
    builder.node("hv", 10.0)
    builder.node("mid", 1.0)
    builder.node("lv", 0.4)
    builder.source("hv", 0.05, 0.2)
    builder.transformer(
        "hv", "mid", vector_group="YNyn0", kva=1000, hv_kv=10.0, lv_kv=1.0, x_pct=4.0, r_pct=0.5,
        hv_ground="solid", lv_ground="solid", transformer_id="t1",
    )
    builder.transformer(
        "mid", "lv", vector_group="YNyn0", kva=1000, hv_kv=1.0, lv_kv=0.4, x_pct=4.0, r_pct=0.5,
        hv_ground="solid", lv_ground="solid", transformer_id="t2",
    )
    network = builder.build()
    for transformer in network.transformers:
        transformer.ppm_antifloat = 0.0
        transformer.imag_pct = 0.0
        transformer.no_load_loss_pct = 0.0
    network.sources[0].r0_ohm = 0.05
    network.sources[0].x0_ohm = 0.2
    network.study_faults.append(StudyFault(id="inside", bus_id="mid", kind="3ph", phases=["A", "B", "C"], r_ohm=0.2))
    solved = solve_network(network)
    if solved.status != "converged":
        raise RuntimeError("внутреннее КЗ зоны не сошлось")
    topology = compile_topology(network)
    hv_bus = topology.node_to_bus["hv"]
    lv_bus = topology.node_to_bus["lv"]
    hv = next(item.current for item in solved.flows if item.equipment_id == "t1" and item.bus_id == hv_bus and item.phase == "A")
    lv = next(item.current for item in solved.flows if item.equipment_id == "t2" and item.bus_id == lv_bus and item.phase == "A")
    return hv, lv


def qualify_through_fault() -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.topology import compile_topology

    network = _transformer()
    solved = solve_network(network)
    transformer = network.transformers[0]
    ratio = transformer.windings[0].nominal_ll_v / transformer.windings[1].nominal_ll_v
    rated_lv = transformer.windings[1].rated_va / (math.sqrt(3.0) * transformer.windings[1].nominal_ll_v)
    pickup = PICKUP_FRACTION * rated_lv
    topology = compile_topology(network)
    currents = {}
    for node_id, name in (("hv", "hv"), ("lv", "lv")):
        bus = topology.node_to_bus[node_id]
        currents[name] = next(
            item.current for item in solved.flows if item.equipment_id == "t" and item.bus_id == bus and item.phase == "A"
        )
    external = transformer_differential(currents["hv"], currents["lv"], ratio, slope=SLOPE, pickup_a=pickup)
    internal_hv, internal_lv = _zone_fault_currents()
    internal = transformer_differential(internal_hv, internal_lv, ratio, slope=SLOPE, pickup_a=pickup)
    blind = transformer_differential(internal_hv, internal_lv, ratio, slope=SLOPE, pickup_a=pickup, available=False)
    reasons = []
    if solved.status != "converged":
        reasons.append("status")
    if external["trip"] or external["operate_a"] > pickup:
        reasons.append("external_trip")
    if not internal["trip"]:
        reasons.append("internal_no_trip")
    if blind["trip"] or blind["dynamic_qualified"]:
        reasons.append("blind_or_dynamic")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "evidence": "phase_currents",
        "dynamic_qualified": False,
        "operate_fraction": external["operate_a"] / rated_lv,
        "internal_operate_a": internal["operate_a"],
        "pickup_a": pickup,
        "status": solved.status,
    }
