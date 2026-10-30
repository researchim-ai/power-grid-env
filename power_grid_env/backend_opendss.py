"""Phase-domain backend на отдельном контексте OpenDSSDirect / DSS-Extensions.

Собственные уравнения решателя сюда не копируются. Модуль отображает
каноническую модель, читает токи и мощности в потребительской конвенции
S = V·conj(I) и не подменяет остров без источника искусственным питанием.
"""

from __future__ import annotations

import hashlib
import math
import threading
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import numpy as np

from power_grid_env.conventions import (
    SOLID_NEUTRAL_R_OHM,
    UNGROUNDED_NEUTRAL_R_OHM,
    ll_to_ln,
    rad_to_deg,
)
from power_grid_env.der import apparent_limit, interpolate_curve
from power_grid_env.model import (
    Inverter,
    Line,
    Network,
    StudyFault,
    Transformer,
    VoltageSource,
    parse_vector_group,
)
from power_grid_env.results import (
    ConductorFlow,
    IslandReport,
    PhaseVoltage,
    SolveOptions,
    SolveResult,
)
from power_grid_env.topology import Topology, compile_topology

_LOCK = threading.Lock()
_SESSION = None


def _get_session():
    """One private native context, used only under _LOCK.

    Every rendered solve begins with Clear, including retry candidates. Creating
    thousands of NewContext wrappers retains native allocations in the installed
    DSS wrapper; clearing/rebuilding one context avoids that accumulation.
    Independent parallelism is provided by separate backend processes.
    """
    global _SESSION
    if _SESSION is None:
        from opendssdirect.OpenDSSDirect import OpenDSSDirect

        _SESSION = OpenDSSDirect().NewContext()
    return _SESSION
_PHASE_NODE = {"A": "1", "B": "2", "C": "3", "N": "4"}
_NODE_PHASE = {"1": "A", "2": "B", "3": "C", "4": "N", "0": "G"}


def backend_version() -> str:
    parts = []
    for package in ("opendssdirect.py", "dss-python"):
        try:
            parts.append(f"{package}=={version(package)}")
        except PackageNotFoundError:
            parts.append(f"{package}==unknown")
    return ", ".join(parts)


def solve_network(network: Network, options: SolveOptions | None = None) -> SolveResult:
    """Один расчёт. Потокобезопасность контекста не заявлена: вызовы сериализуются."""
    with _LOCK:
        result = _solve(network, options or SolveOptions())
        result.diagnostics.pop("_topology_terminal_bus", None)
        return result


def _solve(network: Network, options: SolveOptions) -> SolveResult:
    working = network.model_copy(deep=True)
    result = _solve_once(working, options)
    if not options.apply_regulators or result.status != "converged":
        return result
    for _ in range(options.max_outer):
        if not _advance_regulators(working, result):
            return result
        result = _solve_once(working, options)
        if result.status != "converged":
            result.warnings.append("регулятор остановлен: снимок режима не сошёлся")
            return result
    if _advance_regulators(working, result):
        result = _solve_once(working, options)
        result.warnings.append(
            "внешний цикл РПН/Volt-VAR/распределённого баланса достиг предела итераций"
        )
        result.diagnostics["regulator_iteration_limit"] = True
    return result


def _advance_regulators(network: Network, result: SolveResult) -> bool:
    changed = False
    changed |= _advance_ltc(network, result)
    changed |= _advance_inverters(network, result)
    changed |= _advance_distributed_slack(network, result)
    return changed


def _advance_ltc(network: Network, result: SolveResult) -> bool:
    changed = False
    voltage = _voltage_index(result)
    topology = result.diagnostics.get("_topology_terminal_bus")
    if not isinstance(topology, dict):
        return False
    for transformer in network.transformers:
        if transformer.ltc is None or transformer.ltc.ct_primary_a:
            continue
        ltc = transformer.ltc
        winding = transformer.windings[ltc.controlled_winding]
        bus_id = topology.get(winding.terminal_id)
        if bus_id is None:
            continue
        watched = (winding.phase,) if winding.phase else ("A", "B", "C")
        mags = [
            abs(voltage[(bus_id, phase)].complex) / voltage[(bus_id, phase)].v_base_ln_v
            for phase in watched
            if (bus_id, phase) in voltage and voltage[(bus_id, phase)].energized
        ]
        if not mags:
            continue
        measured = float(sum(mags) / len(mags))
        if ltc.pt_ratio and ltc.ct_primary_a and (ltc.compensator_r_v or ltc.compensator_x_v):
            phase = winding.phase or "A"
            current = 0j
            for flow in result.flows:
                if flow.equipment_id == transformer.id and flow.phase == phase and flow.terminal_index == ltc.controlled_winding:
                    current = flow.current
                    break
            voltage_phasor = voltage[(bus_id, phase)].complex
            compensated = voltage_phasor / ltc.pt_ratio - (-current) / ltc.ct_primary_a * complex(
                ltc.compensator_r_v or 0.0, ltc.compensator_x_v or 0.0
            )
            measured = abs(compensated) * ltc.pt_ratio / voltage[(bus_id, phase)].v_base_ln_v
        if abs(measured - ltc.v_target_pu) <= ltc.deadband_pu:
            continue
        step = ltc.tap_step_pu if measured < ltc.v_target_pu else -ltc.tap_step_pu
        updated = min(ltc.tap_max_pu, max(ltc.tap_min_pu, winding.tap_pu + step))
        if updated != winding.tap_pu:
            winding.tap_pu = updated
            changed = True
    return changed


def _advance_inverters(network: Network, result: SolveResult) -> bool:
    changed = False
    voltage = _voltage_index(result)
    topology = result.diagnostics.get("_topology_terminal_bus")
    if not isinstance(topology, dict):
        return False
    for inverter in network.inverters:
        if not inverter.volt_var and not inverter.volt_watt:
            continue
        bus_id = topology.get(inverter.terminal_id)
        if bus_id is None:
            continue
        mags = [
            abs(voltage[(bus_id, phase)].complex) / voltage[(bus_id, phase)].v_base_ln_v
            for phase in inverter.phases
            if (bus_id, phase) in voltage and voltage[(bus_id, phase)].energized
        ]
        if not mags:
            continue
        measured = float(sum(mags) / len(mags))
        active = inverter.p_w
        reactive = inverter.q_var
        if inverter.volt_watt:
            if inverter.p_requested_w is None:
                inverter.p_requested_w = inverter.p_w
            active = inverter.p_requested_w * interpolate_curve(list(inverter.volt_watt), measured)
        if inverter.volt_var:
            reactive = inverter.s_rated_va * interpolate_curve(list(inverter.volt_var), measured)
        active, reactive = apparent_limit(active, reactive, inverter.s_rated_va, inverter.priority)
        if abs(active - inverter.p_w) > 1.0 or abs(reactive - inverter.q_var) > 1.0:
            inverter.p_w = active
            inverter.q_var = reactive
            changed = True
    return changed


def _advance_distributed_slack(network: Network, result: SolveResult) -> bool:
    weighted = [
        (source.id, source.participation)
        for source in network.sources
        if source.participation is not None
    ] + [
        (generator.id, generator.participation)
        for generator in network.generators
        if generator.participation is not None
    ]
    if not weighted:
        return False
    total_weight = sum(weight for _, weight in weighted)
    if abs(total_weight - 1.0) > 1e-6:
        return False
    injection = _injections(result)
    total = sum(injection.get(identifier, 0.0) for identifier, _ in weighted)
    changed = False
    for generator in network.generators:
        if generator.participation is None:
            continue
        target = generator.participation * total
        if abs(target - generator.p_w) > max(1.0, 1e-4 * max(abs(total), 1.0)):
            generator.p_w = target
            changed = True
    return changed


def _injections(result: SolveResult) -> dict[str, float]:
    """Положительное значение — мощность, отданная в сеть."""
    totals: dict[str, float] = {}
    for flow in result.flows:
        if flow.phase not in {"A", "B", "C"}:
            continue
        totals[flow.equipment_id] = totals.get(flow.equipment_id, 0.0) - flow.p_into_w
    return totals


def _voltage_index(result: SolveResult) -> dict[tuple[str, str], PhaseVoltage]:
    return {(item.bus_id, item.phase): item for item in result.voltages}


def _solve_once(network: Network, options: SolveOptions) -> SolveResult:
    topology = compile_topology(network)
    version_text = backend_version()
    islands = [
        IslandReport(
            id=island.id,
            bus_ids=list(island.bus_ids),
            source_ids=list(island.source_ids),
            energized=island.energized,
            reason=island.reason,
        )
        for island in topology.islands
    ]
    base = dict(
        iterations=0,
        algorithm="not_run",
        tolerance=options.tolerance,
        backend_name="opendss-dss-extensions",
        backend_version=version_text,
        islands=islands,
        tap_positions={
            transformer.id: [winding.tap_pu for winding in transformer.windings]
            for transformer in network.transformers
        },
    )
    if topology.errors:
        return SolveResult(
            status="invalid_model",
            converged=False,
            warnings=list(topology.errors),
            diagnostics={"topology_errors": topology.errors},
            **base,
        )
    policy_error = _source_policy_error(network, topology)
    if policy_error:
        return SolveResult(
            status="invalid_model",
            converged=False,
            warnings=[policy_error],
            diagnostics={"source_policy": policy_error},
            **base,
        )
    energized = [island for island in topology.islands if island.energized]
    if not energized:
        voltages = _zero_voltages(topology, energized_ids=set())
        return SolveResult(
            status="deenergized_no_source",
            converged=False,
            voltages=voltages,
            warnings=["остров без grid-forming источника не получил искусственное питание"],
            diagnostics={"physical_condition": "no_grid_forming_source", "collapse": False},
            **base,
        )
    try:
        script, registry, warnings, terminal_bus = _render(network, topology, options)
    except ValueError as exc:
        return SolveResult(
            status="invalid_model",
            converged=False,
            warnings=[str(exc)],
            diagnostics={"render_error": str(exc)},
            **base,
        )
    try:
        session, algorithm, iterations, converged, attempt_notes = _execute(script, options)
        if not converged and any(g.mode == "pv" for g in network.generators):
            for refinement in range(options.max_pv_refinements):
                session.Text.Command("Solve")
                converged = bool(session.Solution.Converged())
                iterations = int(session.Solution.Iterations())
                attempt_notes.append({"pv_refinement": refinement + 1, "algorithm": algorithm,
                                      "converged": converged, "iterations": iterations,
                                      "load_fraction": 1.0})
                if converged:
                    break
        if not converged and network.loads:
            continuation = _ramp_load_setpoint(session, network)
            attempt_notes.extend(continuation)
            converged = bool(session.Solution.Converged())
            if continuation:
                algorithm = "newton-continuation"
                iterations = int(continuation[-1]["iterations"])
    except Exception as exc:  # backend boundary: любая ошибка движка остаётся диагностикой
        return SolveResult(
            status="invalid_model",
            converged=False,
            warnings=[f"OpenDSS отклонил модель: {exc}"],
            diagnostics={"backend_error": str(exc), "attempts": []},
            script="\n".join(script),
            **base,
        )
    voltages, flows = _read(session, topology, registry)
    forming_limited = _enforce_forming_power_limit(session, network, flows)
    if forming_limited:
        voltages, flows = _read(session, topology, registry)
        converged = bool(session.Solution.Converged())
        warnings.extend(f"{item}: внутреннее напряжение опоры снижено так, чтобы S = Sном" for item in forming_limited)
    solved_taps = _transformer_taps(session, registry)
    limited = _limited_generators(network, flows)
    forming_over = _forming_over_rating(network, flows)
    if forming_over:
        warnings.extend(
            f"{item}: мощность grid-forming выше Sном, предел тока не применён" for item in forming_over
        )
    if any(math.isnan(item.v_real_v) or math.isnan(item.v_imag_v) for item in voltages):
        converged = False
    pq_error = _pq_setpoint_error(network, topology, flows, voltages)
    kcl_error = _kcl_residual(network, voltages, flows)
    if converged and pq_error > 1e-5:
        converged = False
        warnings = warnings + [
            "решатель сообщил сходимость, но уставки постоянной мощности не выполнены; "
            "режим сохранён как диагностика, а не как коллапс"
        ]
    finite_voltage = all(math.isfinite(item.v_real_v) and math.isfinite(item.v_imag_v) for item in voltages)
    if not converged and finite_voltage and pq_error <= 1e-5 and kcl_error <= 1e-8:
        converged = True
        warnings = warnings + [
            "у носа кривой мощности OpenDSS не ставит флаг сходимости; "
            "уставка PQ и баланс токов внутри прежних порогов 1e-5 и 1e-8 о.е."
        ]
    status = "converged" if converged else "numerically_not_converged"
    diagnostics = {
        "attempts": attempt_notes,
        "physical_condition": "solved" if converged else "solver_did_not_converge",
        "collapse": False,
        "kcl_residual_pu": kcl_error,
        "pq_setpoint_error_pu": pq_error,
        "solved_taps": solved_taps,
        "forming_over_rating": forming_over,
        "forming_limited": forming_limited,
        "_topology_terminal_bus": terminal_bus,
        "approximations": list(warnings),
    }
    if not converged:
        warnings = warnings + [
            "численная несходимость сохранена как диагностика; это не заключение о коллапсе"
        ]
    return SolveResult(
        status=status,
        converged=converged,
        iterations=iterations,
        algorithm=algorithm,
        tolerance=options.tolerance,
        backend_name=base["backend_name"],
        backend_version=version_text,
        voltages=voltages,
        flows=flows,
        islands=islands,
        warnings=warnings,
        diagnostics=diagnostics,
        script="\n".join(script),
        tap_positions=base["tap_positions"],
        limited_generators=limited,
    )


def _source_policy_error(network: Network, topology: Topology) -> str | None:
    sources = {source.id: source for source in network.sources}
    for island in topology.islands:
        if not island.energized:
            continue
        members = [sources[identifier] for identifier in island.source_ids]
        slacks = [source for source in members if source.is_slack]
        if len(members) > 1 and len(slacks) != 1:
            return (
                f"{island.id}: несколько источников требуют ровно один is_slack "
                "как опору угла; распределение P задаётся participation у генераторов"
            )
        for source in members:
            if source.participation is not None and not source.is_slack and len(members) > 1:
                return f"{source.id}: participation у дополнительного источника напряжения не задаёт P"
    participants = [source.participation for source in network.sources if source.participation is not None]
    participants += [item.participation for item in network.generators if item.participation is not None]
    if participants and abs(sum(participants) - 1.0) > 1e-6:
        return "веса participation не дают в сумме 1"
    return None


def _zero_voltages(topology: Topology, energized_ids: set[str]) -> list[PhaseVoltage]:
    voltages: list[PhaseVoltage] = []
    for bus in topology.buses.values():
        island_id = topology.bus_island[bus.id]
        base = ll_to_ln(bus.nominal_ll_v) if bus.nominal_ll_v else 1.0
        energized = island_id in energized_ids
        for phase in bus.phases:
            voltages.append(
                PhaseVoltage(
                    bus_id=bus.id,
                    phase=phase,
                    v_real_v=0.0,
                    v_imag_v=0.0,
                    v_base_ln_v=base,
                    island_id=island_id,
                    energized=energized,
                )
            )
    return voltages


def _source_impedance(source: VoltageSource) -> str:
    text = f"r1={source.r_ohm:.12g} x1={source.x_ohm:.12g}"
    if source.r0_ohm is not None and source.x0_ohm is not None:
        text += f" r0={source.r0_ohm:.12g} x0={source.x0_ohm:.12g}"
    return text


def _token(prefix: str, raw: str) -> str:
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}{digest}"


def _bus_spec(token: str, phases: list[str]) -> str:
    return token + "".join(f".{_PHASE_NODE[phase]}" for phase in phases)


def _lower_triangle(matrix: list[list[float]]) -> str:
    rows = []
    for i in range(len(matrix)):
        rows.append(" ".join(f"{matrix[i][j]:.12g}" for j in range(i + 1)))
    return "[" + " | ".join(rows) + "]"


def _render(
    network: Network, topology: Topology, options: SolveOptions
) -> tuple[list[str], list[tuple[str, str]], list[str], dict[str, str]]:
    warnings: list[str] = []
    bus_token = {bus_id: _token("b", bus_id) for bus_id in topology.buses}
    terminal_bus = dict(topology.terminal_bus)
    energized_islands = {island.id for island in topology.islands if island.energized}
    script: list[str] = [
        "Clear",
        f"Set DefaultBaseFrequency={network.frequency_hz:.8g}",
    ]
    uses_regcontrol = any(item.ltc is not None and item.ltc.ct_primary_a for item in network.transformers)
    solver_sets = [
        "Set controlmode=static" if uses_regcontrol else "Set controlmode=off",
        f"Set tolerance={options.tolerance:.1e}",
        f"Set maxiterations={options.max_iterations}",
    ]
    registry: list[tuple[str, str]] = []
    sources = {source.id: source for source in network.sources}
    circuit_started = False
    for island in topology.islands:
        if not island.energized:
            continue
        ordered = sorted(island.source_ids, key=lambda item: (not sources[item].is_slack, item))
        for position, source_id in enumerate(ordered):
            source = sources[source_id]
            bus_id = topology.terminal_bus[source.terminal_id]
            token = bus_token[bus_id]
            angle = rad_to_deg(source.angle_a_rad)
            base_kv = source.ll_v / 1000.0
            if not circuit_started:
                script.append(
                    "New Circuit.{name} basekv={kv:.8g} pu=1.0 phases=3 bus1={bus} angle={angle:.8g}".format(
                        name=_token("c", network.id),
                        kv=base_kv,
                        bus=token,
                        angle=angle,
                    )
                )
                script.append(
                    f"Edit Vsource.source {_source_impedance(source)} "
                    f"basekv={base_kv:.8g} pu=1.0 angle={angle:.8g} bus1={_bus_spec(token, ['A', 'B', 'C'])}"
                )
                registry.append(("Vsource.source", source.id))
                script.extend(solver_sets)
                circuit_started = True
            elif position == 0 and circuit_started:
                # Первый источник второго острова — отдельный Vsource, не circuit.
                name = _token("v", source.id)
                script.append(
                    f"New Vsource.{name} bus1={_bus_spec(token, ['A', 'B', 'C'])} phases=3 "
                    f"basekv={base_kv:.8g} pu=1.0 angle={angle:.8g} "
                    f"{_source_impedance(source)}"
                )
                registry.append((f"Vsource.{name}", source.id))
            else:
                name = _token("v", source.id)
                script.append(
                    f"New Vsource.{name} bus1={_bus_spec(token, ['A', 'B', 'C'])} phases=3 "
                    f"basekv={base_kv:.8g} pu=1.0 angle={angle:.8g} "
                    f"{_source_impedance(source)}"
                )
                registry.append((f"Vsource.{name}", source.id))
    forming = sorted((item for item in network.inverters if item.forming), key=lambda item: item.id)
    for inverter in forming:
        bus_id = topology.terminal_bus[inverter.terminal_id]
        if topology.bus_island[bus_id] not in energized_islands:
            continue
        token = bus_token[bus_id]
        base_kv = inverter.ll_v / 1000.0
        impedance = (
            f"r1={inverter.forming_r_ohm:.12g} x1={inverter.forming_x_ohm:.12g} "
            f"r0={inverter.forming_r_ohm:.12g} x0={inverter.forming_x_ohm:.12g}"
        )
        if not circuit_started:
            script.append(
                "New Circuit.{name} basekv={kv:.8g} pu=1.0 phases=3 bus1={bus} angle=0".format(
                    name=_token("c", network.id),
                    kv=base_kv,
                    bus=token,
                )
            )
            script.append(
                f"Edit Vsource.source {impedance} basekv={base_kv:.8g} pu=1.0 angle=0 "
                f"bus1={_bus_spec(token, ['A', 'B', 'C'])}"
            )
            registry.append(("Vsource.source", inverter.id))
            script.extend(solver_sets)
            circuit_started = True
        else:
            name = _token("v", inverter.id)
            script.append(
                f"New Vsource.{name} bus1={_bus_spec(token, ['A', 'B', 'C'])} phases=3 "
                f"basekv={base_kv:.8g} pu=1.0 angle=0 {impedance}"
            )
            registry.append((f"Vsource.{name}", inverter.id))
    # Первый источник первого острова уже добавлен. Ветвь position==0 для
    # первого острова обработана выше через circuit_started. Дополнительные
    # источники того же острова попадают в else. Источники следующих островов
    # с position 0 тоже должны быть Vsource — это делает ветка elif, но только
    # когда circuit_started уже True и position==0. Для position>0 — else.
    # Ошибка: для первого острова position>0 идёт в else. Хорошо.
    # Для второго острова position 0 идёт в elif. Хорошо.

    def energized_terminal(terminal_id: str) -> bool:
        return topology.bus_island[topology.terminal_bus[terminal_id]] in energized_islands

    grounds = {item.connectivity_node_id: item for item in network.neutral_grounds}
    for line in network.lines:
        if not (energized_terminal(line.terminal_from) and energized_terminal(line.terminal_to)):
            continue
        terminal_nodes = {item.id: item.connectivity_node_id for item in network.terminals}
        script.extend(
            _line_commands(
                line,
                topology,
                bus_token,
                grounds,
                network.frequency_hz,
                warnings,
                terminal_nodes,
            )
        )
        registry.append((f"Line.{_token('l', line.id)}", line.id))
    for transformer in network.transformers:
        if not all(energized_terminal(winding.terminal_id) for winding in transformer.windings):
            continue
        script.extend(_transformer_commands(transformer, topology, bus_token, warnings, options))
        registry.append((f"Transformer.{_token('t', transformer.id)}", transformer.id))
    for load in network.loads:
        if not energized_terminal(load.terminal_id):
            continue
        bus_id = topology.terminal_bus[load.terminal_id]
        script.extend(
            _load_commands(
                load,
                bus_token[bus_id],
                topology.buses[bus_id].nominal_ll_v,
                topology.buses[bus_id].phases,
                warnings,
            )
        )
        # Несколько однофазных элементов остаются под одним equipment id.
        for name in _load_names(load):
            registry.append((name, load.id))
    for generator in network.generators:
        if not energized_terminal(generator.terminal_id):
            continue
        bus_id = topology.terminal_bus[generator.terminal_id]
        name = _token("g", generator.id)
        kv = generator.ll_v / 1000.0
        if generator.mode == "pv":
            script.append(
                f"New Generator.{name} bus1={_bus_spec(bus_token[bus_id], list(generator.phases))} "
                f"phases={len(generator.phases)} kv={kv:.8g} kw={generator.p_w / 1000.0:.12g} model=3 "
                f"vpu={generator.v_set_pu:.8g} vminpu={generator.v_min_pu:.8g} vmaxpu={generator.v_max_pu:.8g} "
                f"minkvar={generator.q_min_var / 1000.0:.12g} maxkvar={generator.q_max_var / 1000.0:.12g}"
            )
        else:
            script.append(
                f"New Generator.{name} bus1={_bus_spec(bus_token[bus_id], list(generator.phases))} "
                f"phases={len(generator.phases)} kv={kv:.8g} kw={generator.p_w / 1000.0:.12g} "
                f"kvar={generator.q_var / 1000.0:.12g} model=1"
            )
        registry.append((f"Generator.{name}", generator.id))
    for inverter in network.inverters:
        if inverter.forming or not energized_terminal(inverter.terminal_id):
            continue
        bus_id = topology.terminal_bus[inverter.terminal_id]
        script.extend(_inverter_commands(inverter, bus_token[bus_id], options))
        for element_name in _inverter_registry_names(inverter, options):
            registry.append((element_name, inverter.id))
    for storage in network.storages:
        if not energized_terminal(storage.terminal_id):
            continue
        bus_id = topology.terminal_bus[storage.terminal_id]
        name = _token("s", storage.id)
        state = "discharging" if storage.p_w >= 0 else "charging"
        script.append(
            f"New Storage.{name} phases={len(storage.phases)} "
            f"bus1={_bus_spec(bus_token[bus_id], list(storage.phases))} "
            f"kv={storage.ll_v / 1000.0:.8g} kwrated={storage.s_rated_va / 1000.0:.12g} "
            f"kwhrated={storage.e_rated_j / 3.6e6:.12g} %stored={storage.soc * 100.0:.8g} "
            f"%effcharge={storage.eta_charge * 100.0:.8g} %effdischarge={storage.eta_discharge * 100.0:.8g} "
            f"%idlingkw=0 vminpu=0 state={state} kw={abs(storage.p_w) / 1000.0:.12g}"
        )
        registry.append((f"Storage.{name}", storage.id))
    for shunt in network.shunts:
        if not energized_terminal(shunt.terminal_id):
            continue
        bus_id = topology.terminal_bus[shunt.terminal_id]
        name = _token("c", shunt.id)
        fraction = shunt.steps_in / shunt.steps
        v_ln = ll_to_ln(shunt.ll_v)
        kvar = len(shunt.phases) * (v_ln**2) * shunt.b_siemens_per_phase * fraction / 1000.0
        conn = shunt.connection
        kv = shunt.ll_v / 1000.0 if len(shunt.phases) >= 2 else v_ln / 1000.0
        script.append(
            f"New Capacitor.{name} bus1={_bus_spec(bus_token[bus_id], list(shunt.phases))} "
            f"phases={len(shunt.phases)} kv={kv:.8g} kvar={kvar:.12g} conn={conn}"
        )
        registry.append((f"Capacitor.{name}", shunt.id))
    for ground in network.neutral_grounds:
        bus_id = topology.node_to_bus[ground.connectivity_node_id]
        if topology.bus_island[bus_id] not in energized_islands:
            continue
        if "N" not in topology.buses[bus_id].phases:
            continue
        name = _token("r", ground.id)
        if ground.grounding.kind == "solid":
            resistance, reactance = SOLID_NEUTRAL_R_OHM, 0.0
            warnings.append(
                f"{ground.id}: твёрдая нейтраль введена как {SOLID_NEUTRAL_R_OHM:.0e} Ом, "
                "потому что backend не принимает нулевой импеданс"
            )
        elif ground.grounding.kind == "impedance":
            resistance = ground.grounding.r_ohm or 0.0
            reactance = ground.grounding.x_ohm or 0.0
        elif ground.grounding.kind == "ungrounded":
            continue
        else:
            raise ValueError(f"{ground.id}: заземление нейтрали не определено")
        script.append(
            f"New Reactor.{name} phases=1 bus1={bus_token[bus_id]}.4 bus2={bus_token[bus_id]}.0 "
            f"r={resistance:.12g} x={reactance:.12g}"
        )
        registry.append((f"Reactor.{name}", ground.id))
    for fault in network.study_faults:
        bus_id = _fault_bus(fault, topology)
        if bus_id is None or topology.bus_island[bus_id] not in energized_islands:
            continue
        for element_name, command in _fault_commands(fault, bus_token[bus_id]):
            script.append(command)
            registry.append((element_name, fault.id))
    bases = sorted({f"{bus.nominal_ll_v / 1000.0:.8g}" for bus in topology.buses.values() if bus.nominal_ll_v > 0})
    if bases:
        script.append("Set voltagebases=[" + " ".join(bases) + "]")
        script.append("Calcvoltagebases")
    script.append("Solve")
    return script, registry, warnings, terminal_bus


def _line_commands(line, topology, bus_token, grounds, frequency_hz, warnings, terminal_nodes) -> list[str]:
    phases = list(line.phases)
    resistance = [list(row) for row in line.r_ohm]
    reactance = [list(row) for row in line.x_ohm]
    susceptance = [list(row) for row in line.b_siemens]
    if line.kron_reduction == "allowed_solid_neutral":
        for terminal_id, label in ((line.terminal_from, "from"), (line.terminal_to, "to")):
            ground = grounds.get(terminal_nodes[terminal_id])
            if ground is None or ground.grounding.kind != "solid":
                raise ValueError(
                    f"{line.id}: Kron разрешён только при явно твёрдо заземлённой нейтрали на обоих концах ({label})"
                )
        if any(abs(value) > 0 for row in susceptance for value in row):
            raise ValueError(f"{line.id}: Kron вместе с ненулевой shunt-матрицей в этой версии не применяется")
        resistance, reactance, phases = _kron(resistance, reactance, phases)
        warnings.append(f"{line.id}: применена редукция Крона при твёрдо заземлённой нейтрали")
    name = _token("l", line.id)
    left = bus_token[topology.terminal_bus[line.terminal_from]]
    right = bus_token[topology.terminal_bus[line.terminal_to]]
    commands = [
        f"New Line.{name} phases={len(phases)} bus1={_bus_spec(left, phases)} bus2={_bus_spec(right, phases)} "
        f"length=1 units=km normamps={line.rating_a:.8g}",
        f"~ rmatrix={_lower_triangle(resistance)}",
        f"~ xmatrix={_lower_triangle(reactance)}",
    ]
    omega = 2.0 * math.pi * frequency_hz
    capacitance = [[value / omega * 1.0e9 for value in row] for row in susceptance]
    # Явный ноль не даёт OpenDSS подставить свой шунт 2.8 нФ на единицу длины.
    commands.append(f"~ cmatrix={_lower_triangle(capacitance)}")
    return commands


def _kron(resistance, reactance, phases: list[str]):
    index_n = phases.index("N")
    impedance = np.asarray(resistance, dtype=float) + 1j * np.asarray(reactance, dtype=float)
    keep = [index for index in range(len(phases)) if index != index_n]
    znn = impedance[index_n, index_n]
    if abs(znn) < 1e-9:
        raise ValueError("редукция Крона невозможна: Znn близко к нулю")
    zaa = impedance[np.ix_(keep, keep)]
    zan = impedance[np.ix_(keep, [index_n])]
    zna = impedance[np.ix_([index_n], keep)]
    reduced = zaa - zan @ zna / znn
    new_phases = [phase for phase in phases if phase != "N"]
    return reduced.real.tolist(), reduced.imag.tolist(), new_phases


def _transformer_commands(transformer: Transformer, topology, bus_token, warnings, options: SolveOptions) -> list[str]:
    parsed = parse_vector_group(transformer.vector_group)
    name = _token("t", transformer.id)
    imag = 0.0 if transformer.imag_pct is None else transformer.imag_pct
    noload = 0.0 if transformer.no_load_loss_pct is None else transformer.no_load_loss_pct
    ppm = transformer.ppm_antifloat
    warnings.append(
        f"{transformer.id}: ppm_antifloat={ppm:.12g} — численный антифлоат OpenDSS, не ток холостого хода. "
        "Магничивание берётся только из imag_pct и no_load_loss_pct."
    )
    if transformer.imag_pct is None or transformer.no_load_loss_pct is None:
        warnings.append(
            f"{transformer.id}: ветвь намагничивания не задана и введена нулём; "
            "типовое значение холостого хода не подставлялось"
        )
    leadlag = _leadlag(parsed)
    header = (
        f"New Transformer.{name} phases={transformer.phases} windings={len(transformer.windings)} "
        f"xhl={transformer.x_hl_pct:.12g} %loadloss={transformer.load_loss_pct:.12g} "
        f"%imag={imag:.12g} %noloadloss={noload:.12g} ppm_antifloat={ppm:.12g}"
    )
    if leadlag:
        header += f" leadlag={leadlag}"
    if transformer.x_ht_pct is not None and transformer.x_lt_pct is not None:
        header += f" xht={transformer.x_ht_pct:.12g} xlt={transformer.x_lt_pct:.12g}"
    commands = [header]
    for index, winding in enumerate(transformer.windings):
        bus = bus_token[topology.terminal_bus[winding.terminal_id]]
        bus_spec, neutral = _winding_bus(bus, winding)
        if index > 0 and parsed.clock_hours[index] == 5:
            if transformer.phases != 3 or winding.nodes or winding.phase:
                raise ValueError("clock-5 mapping requires full ABC windings")
            # ANSI supplies -30 degrees; a cyclic LV terminal permutation adds
            # -120 degrees, giving the declared clock-5 (-150 degree) relation.
            bus_spec = bus_spec.replace(".1.2.3", ".3.1.2", 1)
        connection = "" if winding.phase or winding.nodes else f" conn={winding.connection}"
        command = (
            f"~ wdg={index + 1} bus={bus_spec}{connection} "
            f"kv={winding.nominal_ll_v / 1000.0:.8g} kva={winding.rated_va / 1000.0:.8g} "
            f"tap={winding.tap_pu:.8g}"
        )
        if winding.r_pct is not None:
            command += f" %r={winding.r_pct:.12g}"
        if abs(winding.angle_deg) > 1e-9 and (leadlag is None or parsed.clock_hours[index] == 5):
            message = (
                f"{transformer.id}: сдвиг обмотки {index + 1} на {winding.angle_deg:.8g}° "
                "не передаётся в OpenDSS, у сборки нет свойства angle"
            )
            if not options.approximate_phase_shift:
                raise ValueError(message + ". Точный режим это отклоняет")
            warnings.append(message + ". Приближение включено явно, углы этой группы не сверены")
        if neutral is not None:
            command += f" rneut={neutral[0]:.12g} xneut={neutral[1]:.12g}"
            if winding.grounding.kind == "ungrounded":
                warnings.append(
                    f"{transformer.id}: незаземлённая обмотка {index} введена как "
                    f"Rneut={UNGROUNDED_NEUTRAL_R_OHM:.0e} Ом на явном узле нейтрали"
                )
        commands.append(command)
    ltc = transformer.ltc
    if ltc is not None and ltc.ct_primary_a and ltc.pt_ratio:
        winding = transformer.windings[ltc.controlled_winding]
        kv = winding.nominal_ll_v / 1000.0
        vreg = ltc.v_target_pu * kv * 1000.0 / ltc.pt_ratio
        band = ltc.deadband_pu * 2.0 * kv * 1000.0 / ltc.pt_ratio
        commands.append(
            f"New RegControl.{name} transformer={name} winding={ltc.controlled_winding + 1} "
            f"vreg={vreg:.8g} band={band:.8g} ptratio={ltc.pt_ratio:.8g} ctprim={ltc.ct_primary_a:.8g} "
            f"R={ltc.compensator_r_v or 0:.8g} X={ltc.compensator_x_v or 0:.8g}"
        )
    return commands


def _leadlag(parsed) -> str | None:
    clocks = []
    for index, connection in enumerate(parsed.connections):
        if index == 0 or connection == parsed.connections[0]:
            continue
        clocks.append(parsed.clock_hours[index])
    if not clocks:
        return None
    unique = set(clocks)
    if unique == {1}:
        return "ansi"
    if unique == {11}:
        return "euro"
    if unique == {5}:
        return "ansi"
    raise ValueError(f"группа {parsed.raw} не имеет одного поддержанного сдвига LeadLag")


def _winding_bus(bus_token: str, winding) -> tuple[str, tuple[float, float] | None]:
    if winding.nodes:
        return bus_token + "".join(f".{node}" for node in winding.nodes), None
    if winding.phase:
        node = {"A": "1", "B": "2", "C": "3"}[winding.phase]
        return f"{bus_token}.{node}", None
    if winding.connection == "delta":
        return f"{bus_token}.1.2.3", None
    if winding.grounding.kind == "solid":
        # rneut=-1 в OpenDSS значит нейтраль сидит на узле 0. Нулевой реактор — другой элемент.
        return f"{bus_token}.1.2.3", None
    if winding.grounding.kind == "impedance":
        return f"{bus_token}.1.2.3.4", (winding.grounding.r_ohm or 0.0, winding.grounding.x_ohm or 0.0)
    if winding.grounding.kind == "ungrounded":
        return f"{bus_token}.1.2.3.4", (UNGROUNDED_NEUTRAL_R_OHM, 0.0)
    raise ValueError("заземление обмотки не задано")


def _declared_kv(load, nominal_ll_v: float, phases: list[str]) -> float:
    """kV OpenDSS: для однофазной звезды это фазное, для трёхфазной и delta — линейное."""
    if load.rated_kv is not None:
        if load.connection == "wye" and len(load.phases) == 3 and len(phases) == 1:
            return load.rated_kv / math.sqrt(3.0)
        return load.rated_kv
    if load.connection == "wye" and len(phases) == 1:
        return ll_to_ln(nominal_ll_v) / 1000.0
    return nominal_ll_v / 1000.0


def _neutral_return(load) -> bool:
    return load.connection == "wye" and load.grounding.kind == "ungrounded"


def _phase_neutral_bus(bus_token: str, phase: str) -> str:
    node = {"A": "1", "B": "2", "C": "3"}[phase]
    return f"{bus_token}.{node}.4"


def _load_commands(load, bus_token: str, nominal_ll_v: float, bus_phases: tuple[str, ...] = (), warnings: list[str] | None = None) -> list[str]:
    commands = []
    to_neutral = _neutral_return(load)
    if to_neutral and "N" not in bus_phases:
        raise ValueError(f"{load.id}: незаземлённая звезда требует узел нейтрали N, на землю её не сажаем")
    if to_neutral and warnings is not None:
        warnings.append(f"{load.id}: звезда подключена к нейтрали узла, а не к земле")
    balanced = (
        load.connection == "wye"
        and load.phases == ["A", "B", "C"]
        and len(set(round(load.p_w[phase], 6) for phase in load.phases)) == 1
        and len(set(round(load.q_var[phase], 6) for phase in load.phases)) == 1
    )
    if load.connection == "delta" and len(load.phases) == 2:
        commands.append(
            _one_load(
                _token("d", load.id),
                _bus_spec(bus_token, list(load.phases)),
                1,
                "delta",
                _declared_kv(load, nominal_ll_v, load.phases),
                sum(load.p_w.values()),
                sum(load.q_var.values()),
                load,
            )
        )
        return commands
    if (balanced or (load.connection == "delta" and len(load.phases) == 3)) and not to_neutral:
        active = sum(load.p_w.values())
        reactive = sum(load.q_var.values())
        commands.append(
            _one_load(
                _token("d", load.id),
                _bus_spec(bus_token, list(load.phases)),
                len(load.phases),
                load.connection,
                _declared_kv(load, nominal_ll_v, load.phases),
                active,
                reactive,
                load,
            )
        )
        return commands
    for phase in load.phases:
        kv = _declared_kv(load, nominal_ll_v, [phase])
        bus = _phase_neutral_bus(bus_token, phase) if to_neutral else _bus_spec(bus_token, [phase])
        commands.append(
            _one_load(
                _token("d", f"{load.id}:{phase}"),
                bus,
                1,
                load.connection,
                kv,
                load.p_w[phase],
                load.q_var[phase],
                load,
            )
        )
    return commands


def _one_load(name, bus, phases, connection, kv, active_w, reactive_var, load) -> str:
    model = load.opendss_model or {"constant_pq": 1, "constant_z": 2, "constant_i": 5, "zip": 8}[load.model]
    command = (
        f"New Load.{name} bus1={bus} phases={phases} conn={connection} kv={kv:.8g} "
        f"kw={active_w / 1000.0:.12g} kvar={reactive_var / 1000.0:.12g} model={model} "
        f"vminpu={load.vmin_pu:.8g} vmaxpu={load.vmax_pu:.8g}"
    )
    if load.model == "zip" and load.opendss_model not in {3, 4}:
        command += (
            " zipv=({zp:.12g} {ip:.12g} {pp:.12g} {zq:.12g} {iq:.12g} {pq:.12g} 0)".format(
                zp=load.zip_z,
                ip=load.zip_i,
                pp=load.zip_p,
                zq=load.zip_zq,
                iq=load.zip_iq,
                pq=load.zip_pq,
            )
        )
    return command


def _load_names(load) -> list[str]:
    balanced = (
        load.connection == "wye"
        and load.phases == ["A", "B", "C"]
        and len(set(round(load.p_w[phase], 6) for phase in load.phases)) == 1
        and len(set(round(load.q_var[phase], 6) for phase in load.phases)) == 1
    )
    if (balanced or (load.connection == "delta" and len(load.phases) in (2, 3))) and not _neutral_return(load):
        return [f"Load.{_token('d', load.id)}"]
    return [f"Load.{_token('d', f'{load.id}:{phase}')}" for phase in load.phases]


def _inverter_registry_names(inverter: Inverter, options: SolveOptions) -> list[str]:
    name = _token("i", inverter.id)
    if options.inverter_current_limit_model and inverter.fault_current_multiple is not None:
        return [f"Isource.{name}{phase}" for phase in inverter.phases]
    return [f"PVSystem.{name}"]


def _inverter_commands(inverter: Inverter, bus_token: str, options: SolveOptions) -> list[str]:
    name = _token("i", inverter.id)
    if options.inverter_current_limit_model and inverter.fault_current_multiple is not None:
        current = inverter.fault_current_multiple * inverter.s_rated_va / (math.sqrt(3.0) * inverter.ll_v)
        commands = []
        for index, phase in enumerate(inverter.phases):
            angle = rad_to_deg(-2.0 * math.pi / 3.0 * index)
            commands.append(
                f"New Isource.{name}{phase} phases=1 bus1={_bus_spec(bus_token, [phase])} "
                f"amps={current:.12g} angle={angle:.8g}"
            )
        return commands
    active, reactive = apparent_limit(inverter.p_w, inverter.q_var, inverter.s_rated_va, inverter.priority)
    return [
        f"New PVSystem.{name} phases={len(inverter.phases)} "
        f"bus1={_bus_spec(bus_token, list(inverter.phases))} kv={inverter.ll_v / 1000.0:.8g} "
        f"kva={inverter.s_rated_va / 1000.0:.12g} pmpp={max(active, 0.0) / 1000.0:.12g} "
        f"irradiance=1 kvar={reactive / 1000.0:.12g} vminpu=0"
    ]


def _fault_bus(fault: StudyFault, topology: Topology) -> str | None:
    if fault.bus_id is None:
        return None
    if fault.bus_id in topology.buses:
        return fault.bus_id
    return topology.node_to_bus.get(fault.bus_id)


def _fault_commands(fault: StudyFault, bus_token: str) -> list[tuple[str, str]]:
    if fault.kind == "ll":
        nodes = [_PHASE_NODE[phase] for phase in fault.phases]
        name = f"Reactor.{_token('f', fault.id)}"
        return [
            (
                name,
                f"New {name} phases=1 bus1={bus_token}.{nodes[0]} "
                f"bus2={bus_token}.{nodes[1]} r={fault.r_ohm:.12g} x={fault.x_ohm:.12g}",
            )
        ]
    commands = []
    for phase in fault.phases:
        suffix = "" if fault.kind == "slg" else phase
        name = f"Reactor.{_token('f', fault.id + suffix)}"
        commands.append(
            (
                name,
                f"New {name} phases=1 bus1={bus_token}.{_PHASE_NODE[phase]} bus2={bus_token}.0 "
                f"r={fault.r_ohm:.12g} x={fault.x_ohm:.12g}",
            )
        )
    return commands


def _transformer_taps(session, registry: list[tuple[str, str]]) -> dict[str, float]:
    """Отпайка второй обмотки после Solve. Имена — исходные id, не внутренние токены."""
    wanted = {
        element.split(".", 1)[1]: equipment_id
        for element, equipment_id in registry
        if element.lower().startswith("transformer.")
    }
    found: dict[str, float] = {}
    for token, equipment_id in wanted.items():
        try:
            session.Text.Command(f"Transformer.{token}.wdg=2")
            session.Text.Command(f"? Transformer.{token}.tap")
            found[equipment_id] = float(str(session.Text.Result()).strip())
        except Exception:
            continue
    return found


def _execute(script: list[str], options: SolveOptions):
    session = _get_session()
    notes = []
    algorithm = "newton"
    iterations = 0
    converged = False
    body = [command for command in script if command != "Solve"]
    for candidate in ("newton", "normal"):
        # Несошедшийся Newton не оставляет отпайки и начальное состояние для Normal.
        for command in body:
            session.Text.Command(command)
        session.Text.Command(f"Set algorithm={candidate}")
        session.Text.Command("Solve")
        converged = bool(session.Solution.Converged())
        iterations = int(session.Solution.Iterations())
        notes.append({"algorithm": candidate, "converged": converged, "iterations": iterations})
        algorithm = candidate
        if converged:
            break
    return session, algorithm, iterations, converged, notes


def _ramp_load_setpoint(session, network: Network) -> list[dict[str, Any]]:
    """Доводит уставку с меньшего спроса. Плоский старт у носа кривой не доходит за 80 итераций."""
    targets = []
    for load in network.loads:
        for element, kilowatts, kilovars in _load_setpoints(load):
            targets.append((element.split(".", 1)[1], kilowatts, kilovars))
    if not targets:
        return []
    notes = []
    for fraction in (0.5, 0.8, 0.9, 0.96, 1.0):
        _set_load_fraction(session, targets, fraction)
        session.Text.Command("Set algorithm=newton")
        session.Text.Command("Solve")
        converged = bool(session.Solution.Converged())
        iterations = int(session.Solution.Iterations())
        notes.append({"continuation": fraction, "converged": converged, "iterations": iterations})
        if fraction < 1.0 and not converged:
            # A diagnostic must refer to the requested model, never the last
            # reduced-load continuation point.
            _set_load_fraction(session, targets, 1.0)
            session.Text.Command("Solve")
            notes.append({"continuation": 1.0, "restored_requested_load": True,
                          "converged": bool(session.Solution.Converged()),
                          "iterations": int(session.Solution.Iterations())})
            return notes
    if not bool(session.Solution.Converged()):
        for _ in range(8):
            session.Text.Command("Solve")
            converged = bool(session.Solution.Converged())
            iterations = int(session.Solution.Iterations())
            notes.append({"continuation": 1.0, "extra": True, "converged": converged, "iterations": iterations})
            if converged:
                break
    return notes


def _set_load_fraction(session, targets: list[tuple[str, float, float]], fraction: float) -> None:
    for token, kilowatts, kilovars in targets:
        session.Text.Command(f"Load.{token}.kW={kilowatts * fraction:.12g}")
        session.Text.Command(f"Load.{token}.kvar={kilovars * fraction:.12g}")


def solve_loads_incrementally(
    base: Network, edited: Network, options: SolveOptions | None = None, *, _locked: bool = False
) -> SolveResult:
    """Одна сборка схемы, затем только kW/kvar нагрузок и повторный Solve.

    Состав элементов не меняется. Если нагрузка перестала быть одним DSS-элементом,
    нужна полная сборка. Результат сверяется с холодным solve тем же допуском снаружи.
    """
    if not _locked:
        with _LOCK:
            result = solve_loads_incrementally(base, edited, options, _locked=True)
            result.diagnostics.pop("_topology_terminal_bus", None)
            return result
    if [item.id for item in base.loads] != [item.id for item in edited.loads]:
        raise ValueError("инкрементальное обновление требует те же идентификаторы нагрузок")
    for left, right in zip(base.loads, edited.loads, strict=True):
        if _load_names(left) != _load_names(right):
            raise ValueError(f"{left.id}: состав DSS-нагрузок изменился, нужна полная сборка")
    options = options or SolveOptions()
    topology = compile_topology(edited)
    script, registry, warnings, terminal_bus = _render(base, topology, options)
    notes: list[dict[str, Any]] = []
    session = None
    iterations = 0
    converged = False
    for candidate in ("newton", "normal"):
        session = _get_session()
        for command in script:
            if command == "Solve":
                break
            session.Text.Command(command)
        for load in edited.loads:
            for element, kilowatts, kilovars in _load_setpoints(load):
                token = element.split(".", 1)[1]
                session.Text.Command(f"Load.{token}.kW={kilowatts:.12g}")
                session.Text.Command(f"Load.{token}.kvar={kilovars:.12g}")
        session.Text.Command(f"Set algorithm={candidate}")
        session.Text.Command("Solve")
        converged = bool(session.Solution.Converged())
        iterations = int(session.Solution.Iterations())
        notes.append({"algorithm": candidate, "converged": converged, "iterations": iterations})
        if converged:
            break
    assert session is not None
    voltages, flows = _read(session, topology, registry)
    solved_taps = _transformer_taps(session, registry)
    status = "converged" if converged else "numerically_not_converged"
    return SolveResult(
        status=status,
        converged=converged,
        iterations=iterations,
        algorithm=notes[-1]["algorithm"] if notes else "normal",
        tolerance=options.tolerance,
        backend_name="opendss-dss-extensions",
        backend_version=backend_version(),
        voltages=voltages,
        flows=flows,
        islands=[
            IslandReport(
                id=island.id,
                bus_ids=list(island.bus_ids),
                source_ids=list(island.source_ids),
                energized=island.energized,
                reason=island.reason,
            )
            for island in topology.islands
        ],
        warnings=warnings,
        diagnostics={
            "attempts": notes,
            "incremental_loads": True,
            "solved_taps": solved_taps,
            "_topology_terminal_bus": terminal_bus,
        },
        script="\n".join(script),
        tap_positions={item.id: [winding.tap_pu for winding in item.windings] for item in edited.transformers},
    )


def _load_setpoints(load) -> list[tuple[str, float, float]]:
    names = _load_names(load)
    if len(names) == 1:
        return [(names[0], sum(load.p_w.values()) / 1000.0, sum(load.q_var.values()) / 1000.0)]
    return [
        (name, load.p_w[phase] / 1000.0, load.q_var[phase] / 1000.0)
        for phase, name in zip(load.phases, names, strict=True)
    ]


def _read(session, topology: Topology, registry: list[tuple[str, str]]):
    energized = {island.id for island in topology.islands if island.energized}
    bus_token = {bus_id: _token("b", bus_id) for bus_id in topology.buses}
    token_bus = {token: bus_id for bus_id, token in bus_token.items()}
    voltages: list[PhaseVoltage] = []
    present: set[tuple[str, str]] = set()
    for bus_id, bus in topology.buses.items():
        island_id = topology.bus_island[bus_id]
        base = ll_to_ln(bus.nominal_ll_v) if bus.nominal_ll_v else 1.0
        if island_id not in energized:
            for phase in bus.phases:
                voltages.append(
                    PhaseVoltage(
                        bus_id=bus_id,
                        phase=phase,
                        v_real_v=0.0,
                        v_imag_v=0.0,
                        v_base_ln_v=base,
                        island_id=island_id,
                        energized=False,
                    )
                )
            continue
        token = bus_token[bus_id]
        try:
            session.Circuit.SetActiveBus(token)
            nodes = [str(node) for node in session.Bus.Nodes()]
            raw = list(session.Bus.Voltages())
        except Exception:
            nodes, raw = [], []
        found = {}
        for index, node in enumerate(nodes):
            if index * 2 + 1 >= len(raw):
                break
            found[_NODE_PHASE.get(node, node)] = (raw[2 * index], raw[2 * index + 1])
        for phase in bus.phases:
            real, imag = found.get(phase, (0.0, 0.0))
            present.add((bus_id, phase))
            voltages.append(
                PhaseVoltage(
                    bus_id=bus_id,
                    phase=phase,
                    v_real_v=float(real),
                    v_imag_v=float(imag),
                    v_base_ln_v=base,
                    island_id=island_id,
                    energized=True,
                )
            )
    flows: list[ConductorFlow] = []
    seen_elements: set[str] = set()
    for dss_name, equipment_id in registry:
        if dss_name in seen_elements:
            continue
        seen_elements.add(dss_name)
        try:
            session.Circuit.SetActiveElement(dss_name)
        except Exception:
            continue
        bus_names = list(session.CktElement.BusNames())
        node_order = [str(node) for node in session.CktElement.NodeOrder()]
        powers = list(session.CktElement.Powers())
        currents = list(session.CktElement.Currents())
        n_terms = int(session.CktElement.NumTerminals())
        n_cond = int(session.CktElement.NumConductors())
        for terminal in range(n_terms):
            spec = bus_names[terminal] if terminal < len(bus_names) else ""
            token = spec.split(".")[0]
            bus_id = token_bus.get(token)
            for local in range(n_cond):
                cursor = terminal * n_cond + local
                if cursor >= len(node_order):
                    continue
                phase = _NODE_PHASE.get(node_order[cursor], node_order[cursor])
                flows.append(
                    ConductorFlow(
                        equipment_id=equipment_id,
                        terminal_index=terminal,
                        bus_id=bus_id,
                        phase=phase,
                        p_into_w=float(powers[2 * cursor]) * 1000.0 if 2 * cursor < len(powers) else 0.0,
                        q_into_var=float(powers[2 * cursor + 1]) * 1000.0 if 2 * cursor + 1 < len(powers) else 0.0,
                        i_into_real_a=float(currents[2 * cursor]) if 2 * cursor < len(currents) else 0.0,
                        i_into_imag_a=float(currents[2 * cursor + 1]) if 2 * cursor + 1 < len(currents) else 0.0,
                    )
                )
    return voltages, flows


def _pq_setpoint_error(
    network: Network, topology: Topology, flows: list[ConductorFlow], voltages: list[PhaseVoltage]
) -> float:
    energized = {island.id for island in topology.islands if island.energized}
    worst = 0.0
    # Index once. Scanning F flows and V voltages for each of L loads made
    # result qualification O(L*(F+V)) even for a sparse, easy electrical network.
    by_equipment: dict[str, list[ConductorFlow]] = {}
    by_bus: dict[str, list[PhaseVoltage]] = {}
    for flow in flows:
        by_equipment.setdefault(flow.equipment_id, []).append(flow)
    for voltage in voltages:
        by_bus.setdefault(voltage.bus_id, []).append(voltage)
    for load in network.loads:
        bus_id = topology.terminal_bus[load.terminal_id]
        if topology.bus_island[bus_id] not in energized or load.model != "constant_pq":
            continue
        if _load_left_pq_band(load, bus_id, by_bus.get(bus_id, [])):
            continue
        actual_p = 0.0
        actual_q = 0.0
        for flow in by_equipment.get(load.id, []):
            if flow.equipment_id == load.id and flow.phase in set(load.phases):
                actual_p += flow.p_into_w
                actual_q += flow.q_into_var
        requested_p = sum(load.p_w.values())
        requested_q = sum(load.q_var.values())
        worst = max(
            worst,
            abs(actual_p - requested_p) / network.s_base_va,
            abs(actual_q - requested_q) / network.s_base_va,
        )
    return worst


def _load_left_pq_band(load, bus_id: str, voltages: list[PhaseVoltage]) -> bool:
    """Ниже vminpu и выше vmaxpu OpenDSS сам переводит нагрузку в постоянный Z."""
    if load.rated_kv:
        rated_ln = load.rated_kv * 1000.0
        if not (load.connection == "wye" and len(load.phases) == 1):
            rated_ln /= math.sqrt(3.0)
    else:
        rated_ln = None
    for voltage in voltages:
        if voltage.bus_id != bus_id or voltage.phase not in load.phases or not voltage.energized:
            continue
        base = rated_ln or voltage.v_base_ln_v
        if not base:
            continue
        pu = abs(voltage.complex) / base
        if pu < load.vmin_pu or pu > load.vmax_pu:
            return True
    return False


def _enforce_forming_power_limit(session, network: Network, flows: list[ConductorFlow]) -> list[str]:
    """Линейная сеть: S пропорциональна квадрату ЭДС. Один источник — одна опора."""
    formers = [item for item in network.inverters if item.forming and item.forming_limit]
    if len(formers) != 1 or network.sources:
        return []
    inverter = formers[0]
    power = math.hypot(
        sum(flow.p_into_w for flow in flows if flow.equipment_id == inverter.id and flow.phase in "ABC"),
        sum(flow.q_into_var for flow in flows if flow.equipment_id == inverter.id and flow.phase in "ABC"),
    )
    if power <= inverter.s_rated_va * (1.0 + 1e-6) or power <= 0:
        return []
    scale = math.sqrt(inverter.s_rated_va / power)
    session.Text.Command(f"Edit Vsource.source pu={scale:.12g}")
    session.Text.Command("Solve")
    return [inverter.id]


def _forming_over_rating(network: Network, flows: list[ConductorFlow]) -> list[str]:
    over = []
    for inverter in network.inverters:
        if not inverter.forming:
            continue
        active = sum(flow.p_into_w for flow in flows if flow.equipment_id == inverter.id and flow.phase in "ABC")
        reactive = sum(flow.q_into_var for flow in flows if flow.equipment_id == inverter.id and flow.phase in "ABC")
        # Мощность в устройство положительна, когда сеть питает его. Источник отдаёт отрицательную.
        if math.hypot(active, reactive) > inverter.s_rated_va * (1.0 + 1e-6):
            over.append(inverter.id)
    return over


def _limited_generators(network: Network, flows: list[ConductorFlow]) -> list[str]:
    limited = []
    for generator in network.generators:
        if generator.mode != "pv" or generator.q_max_var is None or generator.q_min_var is None:
            continue
        reactive = 0.0
        for flow in flows:
            if flow.equipment_id == generator.id and flow.phase in {"A", "B", "C"}:
                reactive -= flow.q_into_var
        if reactive >= generator.q_max_var - max(1.0, 0.01 * abs(generator.q_max_var)):
            limited.append(generator.id)
        elif reactive <= generator.q_min_var + max(1.0, 0.01 * abs(generator.q_min_var)):
            limited.append(generator.id)
    return limited


def _kcl_residual(network: Network, voltages: list[PhaseVoltage], flows: list[ConductorFlow]) -> float:
    from power_grid_env.conventions import i_base

    sums: dict[tuple[str, str], complex] = {}
    bases = {(item.bus_id, item.phase): item for item in voltages}
    for flow in flows:
        if flow.bus_id is None or flow.phase not in {"A", "B", "C", "N"}:
            continue
        key = (flow.bus_id, flow.phase)
        if key not in bases or not bases[key].energized:
            continue
        sums[key] = sums.get(key, 0j) + flow.current
    worst = 0.0
    for (bus_id, phase), current in sums.items():
        base_v = bases[(bus_id, phase)].v_base_ln_v * math.sqrt(3.0)
        if base_v <= 0:
            continue
        normal = abs(current) / i_base(base_v, network.s_base_va)
        worst = max(worst, normal)
    return worst
