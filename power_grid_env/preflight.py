"""Предварительная проверка. Ошибка и предупреждение различаются, файл источника не меняется."""

from __future__ import annotations

from typing import Any

from power_grid_env.model import Network
from power_grid_env.topology import compile_topology


def preflight(network: Network, *, task: str = "restoration") -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    topology = compile_topology(network)
    errors.extend(topology.errors)
    if not network.sources:
        errors.append("нет источника")
    if task == "restoration" and not network.switches:
        errors.append("задача восстановления требует коммутационные аппараты")
    energized = [island for island in topology.islands if island.energized]
    if not energized:
        warnings.append("в исходной топологии нет питаемого острова")
    for island in topology.islands:
        slacks = [source for source in network.sources if source.id in island.source_ids and source.is_slack]
        if island.energized and len(island.source_ids) > 1 and len(slacks) != 1:
            errors.append(f"{island.id}: не задана единственная опора угла")
    for transformer in network.transformers:
        if transformer.imag_pct is None or transformer.no_load_loss_pct is None:
            warnings.append(f"{transformer.id}: намагничивание не задано и не будет подобрано")
    for load in network.loads:
        if load.cold_load_multiplier is None:
            continue
        warnings.append(f"{load.id}: cold-load pickup включён с явными параметрами")
    if any(line.kron_reduction == "allowed_solid_neutral" for line in network.lines):
        warnings.append("запрошена редукция Крона; она выполнится только при твёрдой нейтрали на обоих концах")
    for switch in network.switches:
        if switch.kind == "disconnector" and switch.interrupt_current_a != 0:
            errors.append(f"{switch.id}: разъединитель получил ток отключения")
    return {
        "ok": not errors,
        "task": task,
        "errors": errors,
        "warnings": warnings,
        "islands": [
            {
                "id": island.id,
                "energized": island.energized,
                "reason": island.reason,
                "buses": len(island.bus_ids),
                "sources": list(island.source_ids),
            }
            for island in topology.islands
        ],
        "assumptions": list(network.assumptions),
    }
