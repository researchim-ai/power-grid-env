"""Предварительная проверка. Ошибка и предупреждение различаются, файл источника не меняется."""

from __future__ import annotations

import math
from typing import Any

from power_grid_env.model import Network
from power_grid_env.topology import compile_topology


def preflight(
    network: Network,
    *,
    task: str = "restoration",
    solve_initial: bool = True,
    approximate_phase_shift: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    topology = compile_topology(network)
    errors.extend(topology.errors)
    if not network.sources and not any(item.forming for item in network.inverters):
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
        for index, winding in enumerate(transformer.windings):
            if abs(winding.angle_deg) > 1e-9 and not approximate_phase_shift:
                errors.append(
                    f"{transformer.id}: сдвиг обмотки {index + 1} на {winding.angle_deg:.8g}° "
                    "не подтверждён backend и в точном режиме запрещён"
                )
    for omission in network.omissions:
        if task == "power_flow" and omission.power_flow == "not_in_admittance":
            warnings.append(f"{omission.object_id}: {omission.note}")
        elif task in {"protection", "restoration"} and omission.protection == "unsupported":
            errors.append(f"{omission.object_id}: для задачи {task} не поддержан. {omission.note}")
    for load in network.loads:
        if load.cold_load_multiplier is None:
            continue
        warnings.append(f"{load.id}: cold-load pickup включён с явными параметрами")
    if any(line.kron_reduction == "allowed_solid_neutral" for line in network.lines):
        warnings.append("запрошена редукция Крона; она выполнится только при твёрдой нейтрали на обоих концах")
    for line in network.lines:
        for label, matrix in ("r_ohm", line.r_ohm), ("x_ohm", line.x_ohm), ("b_siemens", line.b_siemens):
            for row in matrix:
                if any(not math.isfinite(value) for value in row):
                    errors.append(f"{line.id}: матрица {label} содержит нечисловое значение")
        for index, row in enumerate(line.r_ohm):
            if row[index] < -1e-8:
                errors.append(f"{line.id}: отрицательное продольное сопротивление")
    for switch in network.switches:
        if switch.kind == "disconnector" and switch.interrupt_current_a != 0:
            errors.append(f"{switch.id}: разъединитель получил ток отключения")
    if task in {"rms", "harmonic", "short_circuit"}:
        from power_grid_env.scope import compile_scope

        scope = compile_scope(network, task)  # type: ignore[arg-type]
        errors.extend(scope["errors"])
        warnings.extend(scope["warnings"])
    else:
        scope = None
    initial_solve: dict[str, Any] | None = None
    if solve_initial and not errors:
        from power_grid_env.backend_opendss import solve_network

        try:
            solved = solve_network(network)
        except Exception as exc:
            warnings.append(f"начальный расчёт не выполнен: {exc}")
        else:
            initial_solve = {"status": solved.status, "iterations": solved.iterations, "collapse": False}
            if solved.status not in {"converged", "deenergized_no_source"}:
                warnings.append(f"начальный режим: {solved.status}. Это не коллапс и не скрытый успех.")
    return {
        "ok": not errors,
        "task": task,
        "errors": errors,
        "warnings": warnings,
        "initial_solve": initial_solve,
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
        "scope": scope,
    }
