"""Сравнение главной цепочки площадки с независимым симметричным Ньютоном и короткий пилот.

Симметричная цепочка не заменяет фазный режим. Натурные записи сюда не подставляются.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from power_grid_env.backend_opendss import solve_network
from power_grid_env.model import Network
from power_grid_env.symmetric_ac import newton_power_flow
from power_grid_env.topology import compile_topology


def evidence_slots() -> dict[str, str]:
    return {
        "measured": "пусто: в репозитории нет записи партнёра",
        "estimated": "оценка состояния на модели",
        "fitted": "пусто: параметры не подгонялись под осциллограмму",
    }


def compare_main_path(network: Network, *, tolerance_pu: float = 0.02) -> dict[str, Any]:
    """Прямая последовательность главной цепочки против фазного OpenDSS.

    Конденсатор входит в Ybus как шунт. Генерация и нагрузка — постоянные мощности.
    Резервный ввод за разомкнутым аппаратом в эту цепочку не входит.
    """
    solved = solve_network(network)
    reference = _ladder(network)
    phase = _phase_a(network, solved)
    errors = {node: abs(reference[node] - phase[node]) for node in reference if node in phase}
    worst = max(errors.values()) if errors else 1.0
    return {
        "opendss_status": solved.status,
        "ladder_converged": reference["converged"],
        "worst_pu": worst,
        "within_tolerance": solved.status == "converged" and bool(reference["converged"]) and worst <= tolerance_pu,
        "errors_pu": {key: value for key, value in errors.items() if key != "converged"},
        "tolerance_pu": tolerance_pu,
    }


def run_pilot(network: Network) -> dict[str, Any]:
    """Один шаг удержания при известном КЗ на кабеле и продолжение из JSON-снимка."""
    from power_grid_env.scenarios import Scenario
    from power_grid_env.simulation import Engine

    scenario = Scenario(
        id="plant-pilot",
        horizon_s=60,
        control_interval_s=60,
        settle_s=30,
        fault_line_id="cable",
        fault_disclosed=True,
        fault_r_ohm=1.0,
        v_min_pu=0.9,
        v_max_pu=1.1,
        description="пилот: известное КЗ кабеля 10 кВ, шаг удержания",
    )
    engine = Engine(network, scenario)
    engine.reset(seed=1)
    step = engine.step_control({"kind": "hold"}, origin="controller")
    snapshot = json.loads(json.dumps(engine.snapshot()))
    resumed = Engine(network, scenario)
    resumed.restore(snapshot)
    return {
        "solve_status": step["solve_status"],
        "time_s": step["time_s"],
        "resumed_time_s": resumed.time_s,
        "trace_steps": len(engine.trace),
        "trace": engine.trace,
        "evidence": evidence_slots(),
    }


def _phase_a(network: Network, solved) -> dict[str, float]:
    topology = compile_topology(network)
    found: dict[str, float] = {}
    for node_id, bus_id in topology.node_to_bus.items():
        for voltage in solved.voltages:
            if voltage.bus_id == bus_id and voltage.phase == "A" and voltage.energized and voltage.v_base_ln_v:
                found[node_id] = abs(voltage.complex) / voltage.v_base_ln_v
    return found


def _ladder(network: Network) -> dict[str, Any]:
    s_base = network.s_base_va
    source = next(item for item in network.sources if item.id == "source")
    z_source = (source.r_ohm + 1j * source.x_ohm) / (source.ll_v**2 / s_base)
    t_hv = next(item for item in network.transformers if item.id == "t110")
    t_lv = next(item for item in network.transformers if item.id == "t04")
    cable = next(item for item in network.lines if item.id == "cable")
    feeder = next(item for item in network.lines if item.id == "mcc_feeder")
    order = [
        ("bus110", z_source),
        ("bus10", _transformer_pu(t_hv, s_base)),
        ("section", _line_pu(network, cable, "bus10", s_base)),
        ("lv", _transformer_pu(t_lv, s_base)),
        ("mcc", _line_pu(network, feeder, "lv", s_base)),
    ]
    size = len(order) + 1
    y_bus = np.zeros((size, size), dtype=np.complex128)
    for index, (_name, impedance) in enumerate(order):
        admit = 1.0 / impedance
        y_bus[index, index] += admit
        y_bus[index + 1, index + 1] += admit
        y_bus[index, index + 1] -= admit
        y_bus[index + 1, index] -= admit
    shunt = next(item for item in network.shunts if item.id == "bank")
    q_cap = len(shunt.phases) * (shunt.ll_v / 3**0.5) ** 2 * shunt.b_siemens_per_phase
    y_bus[2, 2] += 1j * (q_cap / s_base)
    p_spec = np.zeros(size)
    q_spec = np.zeros(size)
    p_spec[3] += _watts(network, "roof") / s_base
    p_spec[5] += _watts(network, "aux") / s_base
    p_spec[5] += _watts(network, "bess") / s_base
    load = next(item for item in network.loads if item.id == "plant")
    p_spec[5] -= sum(load.p_w.values()) / s_base
    q_spec[5] -= sum(load.q_var.values()) / s_base
    solved = newton_power_flow(y_bus, p_spec, q_spec, v_slack=1.0)
    voltages = solved["voltage"]
    assert isinstance(voltages, np.ndarray)
    result: dict[str, Any] = {"converged": bool(solved["converged"])}
    for index, (name, _impedance) in enumerate(order):
        result[name] = float(voltages[index + 1])
    return result


def _transformer_pu(transformer, s_base: float) -> complex:
    rated = transformer.windings[0].rated_va
    return complex(transformer.load_loss_pct, transformer.x_hl_pct) / 100.0 * (s_base / rated)


def _line_pu(network: Network, line, node_id: str, s_base: float) -> complex:
    node = next(item for item in network.connectivity_nodes if item.id == node_id)
    level = next(item for item in network.voltage_levels if item.id == node.voltage_level_id)
    ohms = line.r_ohm[0][0] + 1j * line.x_ohm[0][0]
    return ohms / (level.nominal_ll_v**2 / s_base)


def run_restoration(network: Network, *, steps: int = 6) -> dict[str, Any]:
    """Эвристика изолирует кабель и включает резервный ввод. Это авторский пилот, не чужая сеть."""
    from power_grid_env.heuristic import choose_action
    from power_grid_env.scenarios import Scenario
    from power_grid_env.simulation import Engine, agent_model

    scenario = Scenario(
        id="plant-restore",
        horizon_s=60.0 * steps,
        control_interval_s=60.0,
        settle_s=30.0,
        fault_line_id="cable",
        fault_disclosed=True,
        fault_r_ohm=1.0,
        v_min_pu=0.9,
        v_max_pu=1.1,
        description="изолировать кабель 10 кВ и запитать сборку от резерва",
    )
    engine = Engine(network, scenario)
    engine.reset(seed=1)
    public = agent_model(network, scenario).model_dump(mode="json")
    actions = []
    for _ in range(steps):
        if engine.terminated:
            break
        action = choose_action(engine.observation(), public, respect_rating=True)
        actions.append(action)
        engine.step_control(action, origin="controller")
    closed = {switch.id: switch.closed for switch in engine.net.switches}
    return {
        "actions": actions,
        "closed": closed,
        "mcc_pu": _node_pu(engine, "mcc"),
        "solve_status": None if engine.last_result is None else engine.last_result.status,
        "trace_steps": len(engine.trace),
    }


def _node_pu(engine: Any, node_id: str) -> float | None:
    result = engine.last_result
    if result is None:
        return None
    topology = compile_topology(engine.net)
    bus_id = topology.node_to_bus.get(node_id)
    for voltage in result.voltages:
        if voltage.bus_id == bus_id and voltage.phase == "A" and voltage.energized and voltage.v_base_ln_v:
            return abs(voltage.complex) / voltage.v_base_ln_v
    return None


def _watts(network: Network, equipment_id: str) -> float:
    for generator in network.generators:
        if generator.id == equipment_id:
            return generator.p_w
    for storage in network.storages:
        if storage.id == equipment_id:
            return storage.p_w
    for inverter in network.inverters:
        if inverter.id == equipment_id:
            return inverter.p_w
    raise KeyError(equipment_id)
