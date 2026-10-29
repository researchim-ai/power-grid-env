"""Однофазное КЗ через трансформатор и направленная земля на линии.

YNyn0 пропускает нулевую последовательность источника. Dyn11 оставляет её
в треугольнике: на стороне ВН сумма фазных токов равна нулю, а ток НН считается
только с Z0 трансформатора.
"""

from __future__ import annotations

import math
from typing import Any

from power_grid_env.protection import earth_directional, residual_current

Z_SOURCE = 2.0 + 8.0j
X_PCT = 6.0
KVA = 1000.0
HV_KV = 10.0
LV_KV = 0.4
Z_FAULT = 0.001


def slg_current(emf: complex, z1: complex, z2: complex, z0: complex, z_fault: complex) -> complex:
    """Ia = 3 E / (Z1 + Z2 + Z0 + 3 Zf). Это режим повреждённой сети, c = 1."""
    return 3.0 * emf / (z1 + z2 + z0 + 3.0 * z_fault)


def _referred() -> tuple[complex, complex, complex, complex]:
    ratio = HV_KV / LV_KV
    z_base_lv = (LV_KV * 1000.0) ** 2 / (KVA * 1000.0)
    transformer = 1j * (X_PCT / 100.0) * z_base_lv
    source_lv = Z_SOURCE / ratio**2
    positive = source_lv + transformer
    return (LV_KV * 1000.0 / math.sqrt(3.0)) + 0j, positive, transformer, source_lv


def qualify_ynyn0() -> dict[str, Any]:
    emf, positive, _transformer, _source = _referred()
    expected = slg_current(emf, positive, positive, positive, Z_FAULT)
    solved, currents = _fault("YNyn0", "solid", "solid")
    lv = _sum(currents["lv"])
    hv = _sum(currents["hv"])
    phase = currents["lv"][0]
    turns = HV_KV / LV_KV
    reasons = []
    if solved != "converged":
        reasons.append("status")
    error = abs(abs(phase) - abs(expected)) / abs(expected)
    if error > 1e-5:
        reasons.append("current")
    balance = abs(abs(hv) - abs(lv) / turns) / (abs(lv) / turns)
    if balance > 1e-3:
        reasons.append("ampere_turn")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "current_error": error,
        "ampere_turn_error": balance,
        "neutral_passes_source": True,
    }


def qualify_dyn11() -> dict[str, Any]:
    emf, positive, transformer, _source = _referred()
    expected = slg_current(emf, positive, positive, transformer, Z_FAULT)
    yn_expected = slg_current(emf, positive, positive, positive, Z_FAULT)
    solved, currents = _fault("Dyn11", "not_applicable", "solid")
    phase = currents["lv"][0]
    hv_sum = abs(_sum(currents["hv"]))
    reasons = []
    if solved != "converged":
        reasons.append("status")
    error = abs(abs(phase) - abs(expected)) / abs(expected)
    if error > 1e-5:
        reasons.append("current")
    if hv_sum > 1e-2:
        reasons.append("delta")
    if abs(abs(expected) - abs(yn_expected)) / abs(yn_expected) < 0.05:
        reasons.append("same_as_yn")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "current_error": error,
        "hv_residual_a": hv_sum,
        "delta_blocks_zero_sequence": hv_sum <= 1e-2,
    }


def qualify_earth() -> dict[str, Any]:
    """SLG на линии. Остаток совпадает с формулой, обратный ток и отказ измерения молчат."""
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.model import StudyFault
    from power_grid_env.topology import compile_topology

    z_line = 0.4 + 0.8j
    z_fault = 0.05 + 0j
    emf = 10_000.0 / math.sqrt(3.0)
    expected = slg_current(emf, Z_SOURCE + z_line, Z_SOURCE + z_line, Z_SOURCE + z_line, z_fault)
    builder = NetBuilder("earth", "Земля", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", Z_SOURCE.real, Z_SOURCE.imag)
    builder.line_z1("src", "ld", z_line, line_id="line")
    network = builder.build()
    network.sources[0].r0_ohm = Z_SOURCE.real
    network.sources[0].x0_ohm = Z_SOURCE.imag
    network.study_faults.append(StudyFault(id="slg", bus_id="ld", kind="slg", phases=["A"], r_ohm=z_fault.real))
    solved = solve_network(network)
    bus = compile_topology(network).node_to_bus["src"]
    phases = [
        next(
            item.current
            for item in solved.flows
            if item.equipment_id == "line" and item.bus_id == bus and item.phase == phase and item.terminal_index == 0
        )
        for phase in "ABC"
    ]
    voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    residual = residual_current(phases)
    forward = earth_directional(voltage, residual, pickup_a=50.0)
    reverse = earth_directional(voltage, -residual, pickup_a=50.0)
    blind = earth_directional(voltage, residual, pickup_a=50.0, available=False)
    quiet = earth_directional(voltage, residual * 1e-4, pickup_a=50.0)
    error = abs(residual - expected) / abs(expected)
    reasons = []
    if solved.status != "converged":
        reasons.append("status")
    if error > 1e-5:
        reasons.append("residual")
    if not forward["trip"] or reverse["trip"] or blind["trip"] or quiet["trip"]:
        reasons.append("direction")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "residual_error": error,
        "forward_reason": forward["reason"],
        "reverse_reason": reverse["reason"],
    }


def qualify_neutral_reactor() -> dict[str, Any]:
    """Реактор в нейтрали НН добавляет 3Zn в нулевую последовательность."""
    neutral = 0.15 + 0.05j
    emf, positive, _transformer, _source = _referred()
    expected = slg_current(emf, positive, positive, positive + 3.0 * neutral, Z_FAULT)
    solid = slg_current(emf, positive, positive, positive, Z_FAULT)
    solved, currents = _fault("YNyn0", "solid", "solid", neutral_lv=neutral)
    phase = currents["lv"][0]
    error = abs(abs(phase) - abs(expected)) / abs(expected)
    reasons = []
    if solved != "converged":
        reasons.append("status")
    if error > 1e-4:
        reasons.append("current")
    if abs(abs(expected) - abs(solid)) / abs(solid) < 0.2:
        reasons.append("unchanged")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "current_error": error,
        "reactor_a": abs(expected),
        "solid_a": abs(solid),
    }


def _fault(
    vector_group: str,
    hv_ground: str,
    lv_ground: str,
    neutral_lv: complex | None = None,
) -> tuple[str, dict[str, list[complex]]]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.model import StudyFault
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("neutral", vector_group, HV_KV, s_base_va=1.0e6)
    builder.node("hv", HV_KV)
    builder.node("lv", LV_KV)
    builder.source("hv", Z_SOURCE.real, Z_SOURCE.imag)
    builder.transformer(
        "hv",
        "lv",
        vector_group=vector_group,
        kva=KVA,
        hv_kv=HV_KV,
        lv_kv=LV_KV,
        x_pct=X_PCT,
        r_pct=0.0,
        hv_ground=hv_ground,
        lv_ground=lv_ground,
        transformer_id="t",
    )
    network = builder.build()
    transformer = network.transformers[0]
    transformer.ppm_antifloat = 0.0
    transformer.imag_pct = 0.0
    transformer.no_load_loss_pct = 0.0
    transformer.load_loss_pct = 0.0
    for winding in transformer.windings:
        winding.r_pct = 0.0
    if neutral_lv is not None:
        from power_grid_env.build import grounding

        transformer.windings[1].grounding = grounding("impedance", neutral_lv.real, neutral_lv.imag)
    network.sources[0].r0_ohm = Z_SOURCE.real
    network.sources[0].x0_ohm = Z_SOURCE.imag
    network.study_faults.append(StudyFault(id="slg", bus_id="lv", kind="slg", phases=["A"], r_ohm=Z_FAULT))
    solved = solve_network(network)
    if solved.status != "converged":
        return solved.status, {"hv": [0j, 0j, 0j], "lv": [0j, 0j, 0j]}
    topology = compile_topology(network)
    currents = {}
    for node_id in ("hv", "lv"):
        bus = topology.node_to_bus[node_id]
        currents[node_id] = [
            next(item.current for item in solved.flows if item.equipment_id == "t" and item.bus_id == bus and item.phase == phase)
            for phase in "ABC"
        ]
    return solved.status, currents


def _sum(currents: list[complex]) -> complex:
    return currents[0] + currents[1] + currents[2]
