"""Численные проверки первой поставки. Пороги не ослабляются ради зелёного прогона."""

from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np

from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder
from power_grid_env.checks import (
    POWER_FLOOR_W,
    kcl_report,
    power_balance_report,
    voltage_error,
)
from power_grid_env.conventions import (
    complex_power,
    i_base,
    ll_to_ln,
    positive_sequence_phasor,
    sequence_impedances,
    to_sequence,
    z_base,
)
from power_grid_env.der import apparent_limit, cold_load_multiplier, update_soc
from power_grid_env.model import Network, StudyFault
from power_grid_env.reference import MNA, receiving_voltage_pq, thevenin_fault_current, zip_power
from power_grid_env.topology import compile_topology

VOLTAGE_PU = 1e-5
ANGLE_RAD = 1e-4
MISMATCH_PU = 1e-8


def run_numerical_suite() -> list[dict[str, Any]]:
    checks: list[tuple[str, Callable[[], None]]] = [
        ("conventions_power_and_bases", _conventions),
        ("two_bus_pq_closed_form", _two_bus_pq),
        ("unbalanced_constant_z_mna", _unbalanced_mna),
        ("kcl_and_balance", _balance),
        ("zip_formula", _zip),
        ("dyn11_phase_shift", _dyn11),
        ("delta_blocks_zero_sequence", _zero_sequence),
        ("open_phase", _open_phase),
        ("island_without_source", _dead_island),
        ("false_convergence_is_not_collapse", _false_convergence),
        ("three_phase_fault_thevenin", _fault),
        ("storage_soc_and_inverter_limit", _der),
        ("kron_matches_explicit_neutral", _kron),
    ]
    results = []
    for name, function in checks:
        try:
            detail = function() or "ok"
            results.append({"id": name, "passed": True, "detail": detail})
        except Exception as exc:
            results.append({"id": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"})
    return results


def _conventions() -> str:
    voltage = 100 + 0j
    current = 3 - 4j
    power = complex_power(voltage, current)
    if power != voltage * current.conjugate():
        raise AssertionError("S = V·conj(I)")
    if abs(z_base(12.47e3, 1e6) - (12.47e3**2) / 1e6) > 1e-9:
        raise AssertionError("Zbase")
    if abs(i_base(12.47e3, 1e6) - 1e6 / (math.sqrt(3) * 12.47e3)) > 1e-9:
        raise AssertionError("Ibase")
    phases = positive_sequence_phasor(1.0, 0.0)
    seq = to_sequence(phases)
    if abs(seq[1] - 1) > 1e-9 or abs(seq[0]) > 1e-9 or abs(seq[2]) > 1e-9:
        raise AssertionError(f"Fortescue {seq}")
    z1 = sequence_impedances(np.array([[1.2, 0.2, 0.2], [0.2, 1.2, 0.2], [0.2, 0.2, 1.2]]))[1]
    if abs(z1 - 1.0) > 1e-9:
        raise AssertionError("Z1")
    return "S, базы и прямая последовательность"


def _two_bus_pq() -> str:
    builder = NetBuilder("two-bus-pq", "Двухузловая PQ", 12.47, s_base_va=1e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 1e-4, 1e-4)
    z1 = complex(0.25, 0.5)
    builder.line_z1("src", "ld", z1, z_mutual=complex(0.05, 0.1), line_id="line")
    builder.load("ld", 900e3, 300e3, load_id="load")
    result = solve_network(builder.build())
    _require_converged(result)
    expected, _low = receiving_voltage_pq(ll_to_ln(12470), z1 + complex(1e-4, 1e-4), complex(300e3, 100e3))
    topology = compile_topology(builder.build())
    load_bus = topology.terminal_bus[next(load.terminal_id for load in builder.build().loads)]
    actual = _phase(result, load_bus, "A")
    mag_pu, ang = voltage_error(actual, expected, ll_to_ln(12470))
    if mag_pu > VOLTAGE_PU or abs(ang) > ANGLE_RAD:
        raise AssertionError(f"расхождение с квадратичной формулой {mag_pu} pu, {ang} rad")
    return f"dV={mag_pu:.3e} pu"


def _unbalanced_mna() -> str:
    builder = NetBuilder("unbalanced-z", "Несимметричная Z", 12.47)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.02, 0.06)
    builder.line_matrix(
        "src",
        "ld",
        [[0.3, 0.05, 0.04], [0.05, 0.32, 0.05], [0.04, 0.05, 0.28]],
        [[0.6, 0.1, 0.08], [0.1, 0.62, 0.1], [0.08, 0.1, 0.55]],
        phases="ABC",
        line_id="line",
    )
    builder.load(
        "ld",
        {"A": 80e3, "B": 30e3, "C": 10e3},
        {"A": 10e3, "B": 5e3, "C": 0.0},
        model="constant_z",
        load_id="load",
    )
    network = builder.build()
    result = solve_network(network)
    _require_converged(result)
    reference = _mna_constant_z(network)
    topology = compile_topology(network)
    load_bus = topology.terminal_bus[network.loads[0].terminal_id]
    worst = 0.0
    for phase in "ABC":
        actual = _phase(result, load_bus, phase)
        expected = reference[(load_bus, phase)]
        mag_pu, _ang = voltage_error(actual, expected, ll_to_ln(12470))
        worst = max(worst, mag_pu)
    if worst > VOLTAGE_PU:
        raise AssertionError(f"MNA {worst}")
    return f"max dV={worst:.3e} pu"


def _balance() -> str:
    builder = NetBuilder("balance", "Баланс", 12.47)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.01, 0.04)
    builder.line_z1("src", "ld", complex(0.2, 0.4), z_mutual=0.02 + 0.05j, b_phase=1e-5, line_id="line")
    builder.load("ld", 400e3, 80e3, load_id="load")
    builder.shunt("ld", 50, shunt_id="cap")
    result = solve_network(builder.build())
    _require_converged(result)
    residual = kcl_report(builder.build(), result)
    if residual["mismatch_pu"] > MISMATCH_PU and residual["mismatch_a"] > 1e-6:
        raise AssertionError(residual)
    balance = power_balance_report(result)
    if abs(balance["mismatch_w"]) > max(POWER_FLOOR_W, 1e-5 * 1e6):
        raise AssertionError(balance)
    return f"KCL {residual['mismatch_pu']:.3e} pu, баланс {balance['mismatch_w']:.3e} Вт"


def _zip() -> str:
    builder = NetBuilder("zip", "ZIP", 12.47)
    builder.node("src")
    builder.source("src", 0.5, 2.0)
    builder.load("src", 300e3, 90e3, model="zip", zip_coeffs=(0.2, 0.3, 0.5, 0.2, 0.3, 0.5), load_id="load")
    result = solve_network(builder.build())
    _require_converged(result)
    voltage = next(item for item in result.voltages if item.phase == "A")
    pu = abs(voltage.complex) / voltage.v_base_ln_v
    expected = zip_power(300e3 + 90e3j, pu, 0.2, 0.3, 0.5, 0.2, 0.3, 0.5)
    actual = sum((flow.power for flow in result.flows if flow.equipment_id == "load" and flow.phase in "ABC"), 0j)
    if abs(actual - expected) > 1e-3:
        raise AssertionError(f"{actual} != {expected}")
    return "ZIP совпал с определением"


def _dyn11() -> str:
    builder = NetBuilder("dyn11", "Dyn11", 12.47)
    builder.node("hv", 12.47)
    builder.node("lv", 0.48)
    builder.source("hv", 1e-4, 1e-4)
    builder.transformer(
        "hv",
        "lv",
        vector_group="Dyn11",
        kva=1000,
        hv_kv=12.47,
        lv_kv=0.48,
        x_pct=5,
        r_pct=0.01,
        hv_ground="not_applicable",
        lv_ground="solid",
    )
    result = solve_network(builder.build())
    _require_converged(result)
    hv = next(item for item in result.voltages if item.phase == "A" and abs(item.complex) > 1000)
    lv = next(item for item in result.voltages if item.phase == "A" and abs(item.complex) < 1000)
    shift = math.atan2(lv.v_imag_v, lv.v_real_v) - math.atan2(hv.v_imag_v, hv.v_real_v)
    shift = math.atan2(math.sin(shift), math.cos(shift))
    if abs(shift - math.radians(30)) > 1e-4:
        raise AssertionError(f"сдвиг {math.degrees(shift)}°")
    return "Dyn11: НН опережает ВН на 30°"


def _zero_sequence() -> str:
    builder = NetBuilder("zs", "YNd11", 12.47)
    builder.node("hv", 12.47)
    builder.node("lv", 4.16)
    builder.source("hv", 0.05, 0.5)
    builder.transformer(
        "hv",
        "lv",
        vector_group="YNd11",
        kva=5000,
        hv_kv=12.47,
        lv_kv=4.16,
        x_pct=6,
        r_pct=1,
        hv_ground="solid",
        lv_ground="not_applicable",
        transformer_id="transformer",
    )
    network = builder.build()
    network.study_faults.append(StudyFault(id="slg", bus_id="hv", kind="slg", phases=["A"], r_ohm=1.0))
    result = solve_network(network)
    _require_converged(result)
    topology = compile_topology(network)
    lv_bus = topology.terminal_bus[network.transformers[0].windings[1].terminal_id]
    total = sum(
        (flow.current for flow in result.flows if flow.equipment_id == "transformer" and flow.bus_id == lv_bus and flow.phase in "ABC"),
        0j,
    )
    if abs(total) > 1e-6:
        raise AssertionError(f"ток нулевой последовательности прошёл через треугольник: {total}")
    return "сумма линейных токов треугольника ~ 0"


def _open_phase() -> str:
    builder = NetBuilder("open", "Обрыв фазы", 12.47)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 1e-3, 1e-3)
    builder.line_z1("src", "ld", complex(0.2, 0.4), phases="AB", line_id="line")
    builder.load("ld", {"A": 40e3, "B": 40e3}, {"A": 0.0, "B": 0.0}, phases="AB", load_id="load")
    result = solve_network(builder.build())
    _require_converged(result)
    topology = compile_topology(builder.build())
    load_bus = topology.node_to_bus["ld"]
    phase_c = [item for item in result.voltages if item.bus_id == load_bus and item.phase == "C"]
    if phase_c and abs(phase_c[0].complex) > 1.0:
        raise AssertionError("фаза C присутствует на оборванном конце")
    return "фаза C на приёмном конце отсутствует"


def _dead_island() -> str:
    builder = NetBuilder("dead", "Остров без источника", 12.47)
    builder.node("a")
    builder.node("b")
    builder.line_z1("a", "b", complex(0.2, 0.4), line_id="line")
    builder.load("b", 100e3, 0, load_id="load")
    result = solve_network(builder.build())
    if result.status != "deenergized_no_source":
        raise AssertionError(result.status)
    if result.diagnostics.get("collapse") is not False:
        raise AssertionError("отсутствие источника названо коллапсом")
    if any(abs(item.complex) > 1e-6 for item in result.voltages):
        raise AssertionError("остров получил напряжение")
    return "deenergized_no_source"


def _false_convergence() -> str:
    builder = NetBuilder("heavy", "Несуществующая ветвь PQ", 12.47)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.01, 0.05)
    builder.line_z1("src", "ld", complex(5, 20), line_id="line")
    builder.load("ld", 20e6, 10e6, load_id="load")
    result = solve_network(builder.build())
    if result.status == "converged":
        raise AssertionError("уставки PQ не выполнены, но статус converged")
    if result.diagnostics.get("collapse"):
        raise AssertionError("несходимость записана как коллапс")
    if result.diagnostics.get("pq_setpoint_error_pu", 0) < 1e-3:
        raise AssertionError("ожидалась большая невязка PQ")
    return "numerically_not_converged, collapse=false"


def _fault() -> str:
    builder = NetBuilder("fault3", "Трёхфазное КЗ", 12.47)
    builder.node("src")
    builder.source("src", 0.1, 1.0)
    network = builder.build()
    network.study_faults.append(StudyFault(id="f", bus_id="src", kind="3ph", phases=["A", "B", "C"], r_ohm=2.0))
    result = solve_network(network)
    _require_converged(result)
    expected = thevenin_fault_current(ll_to_ln(12470), complex(0.1, 1.0), complex(0.1, 1.0), complex(0.1, 1.0), 2 + 0j, "3ph")
    voltage = expected * 2.0
    actual = next(item for item in result.voltages if item.phase == "A")
    mag_pu, _ang = voltage_error(actual.complex, voltage, ll_to_ln(12470))
    if mag_pu > VOLTAGE_PU:
        raise AssertionError(f"КЗ {mag_pu}")
    return f"dV={mag_pu:.3e} pu"


def _der() -> str:
    limited = apparent_limit(100.0, 50.0, 100.0, "P")
    if abs(limited[1]) > 1e-9:
        raise AssertionError("Q не обрезана при приоритете P")
    soc = update_soc(0.5, 10e3, 100e3 * 3600, 0.9, 0.9, 3600, 0.0, 1.0)
    # Разряд 10 кВт·ч из батареи с η=0.9 забирает 10/0.9 кВт·ч.
    expected = 0.5 - (10 / 0.9) / 100
    if abs(soc - expected) > 1e-9:
        raise AssertionError(f"SoC {soc} != {expected}")
    if abs(cold_load_multiplier(0.0, 2.5, 60.0) - 2.5) > 1e-12:
        raise AssertionError("cold load")
    return "предел S, SoC и cold-load"


def _kron() -> str:
    z_self = complex(0.4, 0.8)
    z_n = complex(0.6, 0.9)
    z_m = complex(0.05, 0.1)
    size = 4
    resistance = np.full((size, size), z_m.real)
    reactance = np.full((size, size), z_m.imag)
    np.fill_diagonal(resistance, z_self.real)
    np.fill_diagonal(reactance, z_self.imag)
    resistance[3, 3] = z_n.real
    reactance[3, 3] = z_n.imag
    explicit = NetBuilder("neutral", "Явная нейтраль", 0.48)
    explicit.node("src", 0.48, phases="ABCN")
    explicit.node("ld", 0.48, phases="ABCN")
    explicit.source("src", 1e-3, 1e-3)
    explicit.line_matrix(
        "src",
        "ld",
        resistance.tolist(),
        reactance.tolist(),
        phases="ABCN",
        line_id="line4",
    )
    explicit.load("ld", {"A": 20e3, "B": 0.0, "C": 0.0}, {"A": 0.0, "B": 0.0, "C": 0.0}, phases="ABC", load_id="load")
    explicit.neutral_ground("src", "solid")
    explicit.neutral_ground("ld", "solid")
    reduced = explicit.build().model_copy(deep=True)
    reduced.lines[0].kron_reduction = "allowed_solid_neutral"
    explicit_result = solve_network(explicit.build())
    reduced_result = solve_network(reduced)
    _require_converged(explicit_result)
    _require_converged(reduced_result)
    topology = compile_topology(explicit.build())
    bus = topology.node_to_bus["ld"]
    for phase in "ABC":
        left = _phase(explicit_result, bus, phase)
        right = _phase(reduced_result, bus, phase)
        mag_pu, _ang = voltage_error(left, right, ll_to_ln(480))
        if mag_pu > 5e-4:
            raise AssertionError(f"Kron фаза {phase}: {mag_pu}")
    return "Крон совпал с явной заземлённой нейтралью в пределах 5e-4 pu"


def _mna_constant_z(network: Network) -> dict[tuple[str, str], complex]:
    topology = compile_topology(network)
    mna = MNA(network.frequency_hz)
    for source in network.sources:
        bus = topology.terminal_bus[source.terminal_id]
        phases = positive_sequence_phasor(ll_to_ln(source.ll_v), source.angle_a_rad)
        z_source = complex(source.r_ohm, source.x_ohm)
        for index, phase in enumerate("ABC"):
            node = (bus, phase)
            mna.stamp_norton(node, 1 / z_source, phases[index] / z_source)
    for line in network.lines:
        left = topology.terminal_bus[line.terminal_from]
        right = topology.terminal_bus[line.terminal_to]
        impedance = np.asarray(line.r_ohm, float) + 1j * np.asarray(line.x_ohm, float)
        nodes_left = [(left, phase) for phase in line.phases if phase != "N"]
        nodes_right = [(right, phase) for phase in line.phases if phase != "N"]
        phase_index = [line.phases.index(phase) for phase in line.phases if phase != "N"]
        block = impedance[np.ix_(phase_index, phase_index)]
        mna.stamp_series(nodes_left, nodes_right, block)
    for load in network.loads:
        bus = topology.terminal_bus[load.terminal_id]
        vnom = ll_to_ln(topology.buses[bus].nominal_ll_v)
        for phase in load.phases:
            power = complex(load.p_w[phase], load.q_var[phase])
            if power == 0:
                continue
            mna.stamp_admittance_to_ground((bus, phase), np.conjugate(power) / (vnom**2))
    solved = mna.solve()
    return solved.voltages


def _phase(result, bus_id: str, phase: str) -> complex:
    voltage = next(item for item in result.voltages if item.bus_id == bus_id and item.phase == phase)
    return voltage.complex


def _require_converged(result) -> None:
    if result.status != "converged":
        raise AssertionError(f"{result.status}: {result.warnings[:2]} {result.diagnostics.get('backend_error')}")

