"""Независимые проверки KCL, баланса и сравнения напряжений."""

from __future__ import annotations

import math

from power_grid_env.conventions import align_angle, i_base
from power_grid_env.results import SolveResult

POWER_FLOOR_W = 1e-3
CURRENT_FLOOR_A = 1e-6


def kcl_report(network, result: SolveResult) -> dict[str, float]:
    sums: dict[tuple[str, str], complex] = {}
    bases = {(item.bus_id, item.phase): item for item in result.voltages}
    for flow in result.flows:
        if flow.bus_id is None or flow.phase not in {"A", "B", "C", "N"}:
            continue
        key = (flow.bus_id, flow.phase)
        sample = bases.get(key)
        if sample is None or not sample.energized:
            continue
        sums[key] = sums.get(key, 0j) + flow.current
    worst_pu = 0.0
    worst_a = 0.0
    for (bus_id, phase), current in sums.items():
        sample = bases[(bus_id, phase)]
        worst_a = max(worst_a, abs(current))
        base_ll = sample.v_base_ln_v * math.sqrt(3.0)
        if base_ll > 0:
            worst_pu = max(worst_pu, abs(current) / i_base(base_ll, network.s_base_va))
    return {"mismatch_pu": worst_pu, "mismatch_a": worst_a}


def power_balance_report(result: SolveResult) -> dict[str, float]:
    """Сумма мощностей, входящих во все терминалы, равна потерям и должна сойтись с разностью источников и нагрузок."""
    generated = 0.0
    consumed = 0.0
    losses = 0.0
    by_equipment: dict[str, float] = {}
    for flow in result.flows:
        if flow.phase not in {"A", "B", "C"}:
            continue
        by_equipment[flow.equipment_id] = by_equipment.get(flow.equipment_id, 0.0) + flow.p_into_w
    for equipment_id, power in by_equipment.items():
        if equipment_id.startswith("source") or equipment_id == "source":
            generated += -power
        elif power > 0 and _is_load(result, equipment_id):
            consumed += power
        else:
            losses += power
    # Источники распознаются по отрицательной сумме, если идентификатор не «source».
    for equipment_id, power in list(by_equipment.items()):
        if equipment_id.startswith("source") or equipment_id == "source":
            continue
        if power < -1.0 and not _is_load(result, equipment_id):
            generated += -power
            losses -= power
    mismatch = generated - consumed - losses
    return {"generated_w": generated, "consumed_w": consumed, "losses_w": losses, "mismatch_w": mismatch}


def _is_load(result: SolveResult, equipment_id: str) -> bool:
    return any(flow.equipment_id == equipment_id and flow.p_into_w > 0 and flow.terminal_index == 0 for flow in result.flows) and not any(
        flow.equipment_id == equipment_id and flow.terminal_index == 1 for flow in result.flows
    )


def voltage_error(actual: complex, expected: complex, v_base_ln: float) -> tuple[float, float]:
    if v_base_ln <= 0:
        raise ValueError("нулевая база напряжения")
    magnitude = abs(abs(actual) - abs(expected)) / v_base_ln
    angle = align_angle(math.atan2(actual.imag, actual.real), math.atan2(expected.imag, expected.real))
    return magnitude, angle
