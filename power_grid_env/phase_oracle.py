"""Независимые эталоны стационарной фазной сети.

Четырёхпроводные случаи, в том числе плавающая нейтраль, и петля из двух
источников считаются методом узловых потенциалов. ZIP и предел Q считаются своим
Ньютоном. Dyn11 с отпайкой считается прямой последовательностью. Это не обёртка
OpenDSS. Пороги зафиксированы до просмотра разницы: |V| 1e-5 о.е., угол 1e-4 рад,
KCL 1e-8 о.е.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from power_grid_env.build import NetBuilder
from power_grid_env.checks import kcl_report
from power_grid_env.conventions import SOLID_NEUTRAL_R_OHM, i_base
from power_grid_env.model import GroundingSpec, NeutralGround, Provenance

VOLTAGE_PU = 1e-5
ANGLE_RAD = 1e-4
KCL_PU = 1e-8
POWER_RELATIVE = 1e-5
POWER_FLOOR_W = 1e-3
CURRENT_RELATIVE = 1e-5
CURRENT_FLOOR_A = 1e-6

_A = np.exp(1j * 2.0 * math.pi / 3.0)


def unbalanced_network():
    return _build()


def _build():
    builder = NetBuilder("phase-oracle", "Несимметрия", 10.0, s_base_va=1.0e6)
    builder.node("src", phases="ABCN")
    builder.node("ld", phases="ABCN")
    builder.source("src", 0.02, 0.06)
    z = _line_impedance()
    builder.line_matrix(
        "src",
        "ld",
        z.real.tolist(),
        z.imag.tolist(),
        phases="ABCN",
        line_id="feeder",
    )
    builder.load(
        "ld",
        {"A": 30_000.0, "B": 10_000.0, "C": 20_000.0},
        {"A": 0.0, "B": 5_000.0, "C": -2_000.0},
        model="constant_z",
        load_id="load",
    )
    builder.neutral_ground("src", "solid", ground_id="src-n")
    network = builder.build()
    network.sources[0].r0_ohm = 0.02
    network.sources[0].x0_ohm = 0.06
    network.neutral_grounds.append(
        NeutralGround(
            id="ld-n",
            connectivity_node_id="ld",
            grounding=GroundingSpec(kind="impedance", r_ohm=2.0, x_ohm=0.5),
            provenance=Provenance(source="phase-oracle", quality="nameplate", note="явный импеданс нейтрали"),
        )
    )
    return network


def _line_impedance() -> np.ndarray:
    self_z = [0.30 + 0.40j, 0.35 + 0.25j, 0.20 + 0.45j, 0.50 + 0.30j]
    mutual = 0.02 + 0.03j
    neutral = 0.05 + 0.08j
    matrix = np.zeros((4, 4), dtype=np.complex128)
    for index, value in enumerate(self_z):
        matrix[index, index] = value
    for row in range(3):
        for column in range(row + 1, 3):
            matrix[row, column] = matrix[column, row] = mutual
        matrix[row, 3] = matrix[3, row] = neutral
    return matrix


def reference_solution(network) -> dict[str, Any]:
    """Узловые напряжения от ЭДС за импедансом источника. Нейтраль источника не идеальный нуль."""
    vln = network.sources[0].ll_v / math.sqrt(3.0)
    emf = np.array([vln, vln * np.exp(-1j * 2.0 * math.pi / 3.0), vln * np.exp(1j * 2.0 * math.pi / 3.0)])
    zs = network.sources[0].r_ohm + 1j * network.sources[0].x_ohm
    z_line = _line_impedance()
    y_line = np.linalg.inv(z_line)
    loads = {phase: network.loads[0].p_w[phase] + 1j * network.loads[0].q_var[phase] for phase in "ABC"}
    z_load = {phase: vln**2 / np.conj(power) for phase, power in loads.items()}
    z_source_n = SOLID_NEUTRAL_R_OHM
    ground = next(item for item in network.neutral_grounds if item.id == "ld-n")
    z_load_n = ground.grounding.r_ohm + 1j * ground.grounding.x_ohm
    # Порядок: src A B C N, ld A B C N.
    admit = np.zeros((8, 8), dtype=np.complex128)
    inject = np.zeros(8, dtype=np.complex128)
    admit[:4, :4] += y_line
    admit[:4, 4:] -= y_line
    admit[4:, :4] -= y_line
    admit[4:, 4:] += y_line
    for index, voltage in enumerate(emf):
        admit[index, index] += 1.0 / zs
        inject[index] += voltage / zs
    admit[3, 3] += 1.0 / z_source_n
    for index, phase in enumerate("ABC"):
        admit[4 + index, 4 + index] += 1.0 / z_load[phase]
    admit[7, 7] += 1.0 / z_load_n
    voltage = np.linalg.solve(admit, inject)
    current = y_line @ (voltage[:4] - voltage[4:])
    return {"voltage": voltage, "line_current_from": current, "vln": vln, "z_line": z_line, "z_load": z_load}


def qualify(network=None) -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.topology import compile_topology

    network = _build() if network is None else network
    solved = solve_network(network)
    reference = reference_solution(network)
    topology = compile_topology(network)
    names = ("src", "ld")
    phases = ("A", "B", "C", "N")
    reasons = []
    voltage_errors = []
    angle_errors = []
    for node_index, node_id in enumerate(names):
        bus_id = topology.node_to_bus[node_id]
        for phase_index, phase in enumerate(phases):
            expected = reference["voltage"][node_index * 4 + phase_index]
            actual = _voltage(solved, bus_id, phase)
            base = reference["vln"]
            voltage_errors.append(abs(abs(actual) - abs(expected)) / base)
            if abs(expected) / base > 1e-3 and abs(actual) / base > 1e-3:
                angle_errors.append(abs(_wrap(np.angle(actual) - np.angle(expected))))
    current_errors = []
    power_errors = []
    bus_from = topology.node_to_bus["src"]
    for index, phase in enumerate(phases):
        expected_i = reference["line_current_from"][index]
        actual_i = _flow_current(solved, "feeder", bus_from, phase)
        current_errors.append(abs(actual_i - expected_i))
        expected_v = reference["voltage"][index]
        expected_s = expected_v * np.conj(expected_i)
        actual_s = _flow_power(solved, "feeder", bus_from, phase)
        power_errors.append(abs(actual_s.real - expected_s.real))
        power_errors.append(abs(actual_s.imag - expected_s.imag))
    sequence_errors = _sequence_errors(solved, topology, reference)
    neutral_errors = _derived_voltage_errors(solved, topology, reference)
    loss_error = _loss_error(solved, reference)
    kcl = kcl_report(network, solved)["mismatch_pu"]
    s_base = network.s_base_va
    i_ref = i_base(network.sources[0].ll_v, s_base)
    if max(voltage_errors) > VOLTAGE_PU:
        reasons.append("voltage")
    if angle_errors and max(angle_errors) > ANGLE_RAD:
        reasons.append("angle")
    if any(error > max(CURRENT_FLOOR_A, CURRENT_RELATIVE * i_ref) for error in current_errors):
        reasons.append("current")
    if any(error > max(POWER_FLOOR_W, POWER_RELATIVE * s_base) for error in power_errors):
        reasons.append("power")
    if max(sequence_errors) > VOLTAGE_PU:
        reasons.append("sequence")
    if max(neutral_errors) > VOLTAGE_PU:
        reasons.append("neutral_or_line_voltage")
    if loss_error > max(POWER_FLOOR_W, POWER_RELATIVE * s_base):
        reasons.append("losses")
    if kcl > KCL_PU:
        reasons.append("kcl")
    if solved.status != "converged":
        reasons.append("status")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "evidence": "independent_mna",
        "max_voltage_error_pu": max(voltage_errors),
        "max_angle_error_rad": max(angle_errors) if angle_errors else 0.0,
        "max_current_error_a": max(current_errors),
        "max_power_error_va": max(power_errors),
        "max_sequence_error_pu": max(sequence_errors),
        "max_neutral_or_line_voltage_error_pu": max(neutral_errors),
        "loss_error_w": loss_error,
        "kcl_pu": kcl,
        "neutral_voltage_pu": abs(reference["voltage"][7]) / reference["vln"],
        "status": solved.status,
    }


def _voltage(solved, bus_id: str, phase: str) -> complex:
    found = next(item for item in solved.voltages if item.bus_id == bus_id and item.phase == phase)
    return found.complex


def _flow_current(solved, equipment_id: str, bus_id: str, phase: str) -> complex:
    found = next(
        item
        for item in solved.flows
        if item.equipment_id == equipment_id and item.bus_id == bus_id and item.phase == phase and item.terminal_index == 0
    )
    return found.current


def _flow_power(solved, equipment_id: str, bus_id: str, phase: str) -> complex:
    found = next(
        item
        for item in solved.flows
        if item.equipment_id == equipment_id and item.bus_id == bus_id and item.phase == phase and item.terminal_index == 0
    )
    return found.power


def _derived_voltage_errors(solved, topology, reference) -> list[float]:
    """Фаза–нейтраль и линейное напряжение. База нейтрали не равна фазной базе."""
    errors = []
    for node_id, offset in (("src", 0), ("ld", 4)):
        bus_id = topology.node_to_bus[node_id]
        actual = [_voltage(solved, bus_id, phase) for phase in "ABCN"]
        expected = reference["voltage"][offset : offset + 4]
        for index in range(3):
            errors.append(abs(abs((actual[index] - actual[3]) - (expected[index] - expected[3])) / reference["vln"]))
        for left, right in ((0, 1), (1, 2), (2, 0)):
            errors.append(abs(abs((actual[left] - actual[right]) - (expected[left] - expected[right])) / (reference["vln"] * math.sqrt(3.0))))
    return errors


def _loss_error(solved, reference) -> float:
    current = reference["line_current_from"]
    drop = reference["voltage"][:4] - reference["voltage"][4:]
    expected = float(np.real(np.sum(drop * np.conj(current))))
    actual = sum(flow.p_into_w for flow in solved.flows if flow.equipment_id == "feeder")
    return abs(actual - expected)


def _sequence_errors(solved, topology, reference) -> list[float]:
    bus_id = topology.node_to_bus["ld"]
    actual = np.array([_voltage(solved, bus_id, phase) for phase in "ABC"])
    expected = reference["voltage"][4:7]
    return [abs(left - right) / reference["vln"] for left, right in zip(_fortescue(actual), _fortescue(expected), strict=True)]


def _fortescue(phase: np.ndarray) -> np.ndarray:
    # a = exp(j120°). Положительная последовательность: B отстаёт от A на 120°.
    return np.array(
        [
            (phase[0] + phase[1] + phase[2]) / 3.0,
            (phase[0] + _A * phase[1] + _A**2 * phase[2]) / 3.0,
            (phase[0] + _A**2 * phase[1] + _A * phase[2]) / 3.0,
        ]
    )


def _wrap(value: float) -> float:
    return float(np.angle(np.exp(1j * value)))


def mesh_two_source_network():
    """Петля из трёх шин и два источника напряжения. Нагрузка — постоянный Z."""
    builder = NetBuilder("mesh-oracle", "Петля", 10.0, s_base_va=1.0e6)
    builder.node("a")
    builder.node("b")
    builder.node("c")
    builder.source("a", 0.01, 0.05, source_id="left")
    builder.source("b", 0.01, 0.05, source_id="right", angle_rad=0.002, is_slack=False)
    builder.line_z1("a", "b", 0.08 + 0.15j, line_id="ab")
    builder.line_z1("b", "c", 0.05 + 0.12j, line_id="bc")
    builder.line_z1("c", "a", 0.06 + 0.10j, line_id="ca")
    builder.load("c", 100_000.0, 20_000.0, model="constant_z", load_id="load")
    network = builder.build()
    for source in network.sources:
        source.r0_ohm = source.r_ohm
        source.x0_ohm = source.x_ohm
    return network


def mesh_reference(network) -> dict[str, complex]:
    """Прямая последовательность. Фазы B и C — поворот на ∓120°."""
    vln = network.sources[0].ll_v / math.sqrt(3.0)
    by_id = {item.id: item for item in network.sources}
    left = by_id["left"]
    right = by_id["right"]
    emf = {
        "a": vln * np.exp(1j * left.angle_a_rad),
        "b": vln * np.exp(1j * right.angle_a_rad),
    }
    zs = {
        "a": left.r_ohm + 1j * left.x_ohm,
        "b": right.r_ohm + 1j * right.x_ohm,
    }
    lines = {item.id: item.r_ohm[0][0] + 1j * item.x_ohm[0][0] for item in network.lines}
    load = network.loads[0]
    power = (sum(load.p_w.values()) + 1j * sum(load.q_var.values())) / 3.0
    z_load = vln**2 / np.conj(power)
    names = ("a", "b", "c")
    index = {name: position for position, name in enumerate(names)}
    admit = np.zeros((3, 3), dtype=np.complex128)
    inject = np.zeros(3, dtype=np.complex128)
    for name, voltage in emf.items():
        slot = index[name]
        admit[slot, slot] += 1.0 / zs[name]
        inject[slot] += voltage / zs[name]
    admit[index["c"], index["c"]] += 1.0 / z_load
    ends = {"ab": ("a", "b"), "bc": ("b", "c"), "ca": ("c", "a")}
    for line_id, (left_id, right_id) in ends.items():
        y = 1.0 / lines[line_id]
        i = index[left_id]
        j = index[right_id]
        admit[i, i] += y
        admit[j, j] += y
        admit[i, j] -= y
        admit[j, i] -= y
    positive = np.linalg.solve(admit, inject)
    lag = np.exp(-1j * 2.0 * math.pi / 3.0)
    lead = np.exp(1j * 2.0 * math.pi / 3.0)
    phases = {}
    for position, name in enumerate(names):
        value = positive[position]
        phases[name] = {"A": value, "B": value * lag, "C": value * lead}
    return {"positive": {name: positive[index[name]] for name in names}, "phase": phases, "vln": vln}


def qualify_mesh(network=None) -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.topology import compile_topology

    network = mesh_two_source_network() if network is None else network
    solved = solve_network(network)
    reference = mesh_reference(network)
    topology = compile_topology(network)
    voltage_errors = []
    angle_errors = []
    for name, phases in reference["phase"].items():
        bus_id = topology.node_to_bus[name]
        for phase, expected in phases.items():
            actual = _voltage(solved, bus_id, phase)
            voltage_errors.append(abs(abs(actual) - abs(expected)) / reference["vln"])
            if abs(expected) > 1.0:
                angle_errors.append(abs(_wrap(np.angle(actual) - np.angle(expected))))
    kcl = kcl_report(network, solved)["mismatch_pu"]
    lag = np.exp(-1j * 2.0 * math.pi / 3.0)
    lead = np.exp(1j * 2.0 * math.pi / 3.0)
    rotation = {"A": 1.0 + 0.0j, "B": lag, "C": lead}
    ends = {"ab": ("a", "b"), "bc": ("b", "c"), "ca": ("c", "a")}
    lines = {item.id: item.r_ohm[0][0] + 1j * item.x_ohm[0][0] for item in network.lines}
    current_errors = []
    loss_expected = 0.0
    loss_actual = 0.0
    for line_id, (left_id, right_id) in ends.items():
        positive = (reference["positive"][left_id] - reference["positive"][right_id]) / lines[line_id]
        loss_expected += 3.0 * float(np.real((reference["positive"][left_id] - reference["positive"][right_id]) * np.conj(positive)))
        loss_actual += sum(flow.p_into_w for flow in solved.flows if flow.equipment_id == line_id)
        for phase, turn in rotation.items():
            for bus_id, sign in ((topology.node_to_bus[left_id], 1.0), (topology.node_to_bus[right_id], -1.0)):
                actual = next(
                    item.current
                    for item in solved.flows
                    if item.equipment_id == line_id and item.bus_id == bus_id and item.phase == phase
                )
                current_errors.append(abs(actual - sign * positive * turn))
    by_id = {item.id: item for item in network.sources}
    source_nodes = {"left": "a", "right": "b"}
    source_power = {}
    source_power_error = {}
    vln = reference["vln"]
    for source_id, node_id in source_nodes.items():
        source = by_id[source_id]
        emf = vln * np.exp(1j * source.angle_a_rad)
        zs = source.r_ohm + 1j * source.x_ohm
        terminal = reference["positive"][node_id]
        expected_w = 3.0 * float(np.real(terminal * np.conj(-(emf - terminal) / zs)))
        actual_w = sum(flow.p_into_w for flow in solved.flows if flow.equipment_id == source_id and flow.phase in "ABC")
        source_power[source_id] = actual_w
        source_power_error[source_id] = abs(actual_w - expected_w)
    i_tolerance = max(CURRENT_FLOOR_A, CURRENT_RELATIVE * i_base(network.sources[0].ll_v, network.s_base_va))
    power_tolerance = max(POWER_FLOOR_W, POWER_RELATIVE * network.s_base_va)
    reasons = []
    if solved.status != "converged":
        reasons.append("status")
    if max(voltage_errors) > VOLTAGE_PU:
        reasons.append("voltage")
    if max(angle_errors) > ANGLE_RAD:
        reasons.append("angle")
    if max(current_errors) > i_tolerance:
        reasons.append("current")
    if any(error > power_tolerance for error in source_power_error.values()):
        reasons.append("source_power")
    if abs(loss_actual - loss_expected) > power_tolerance:
        reasons.append("losses")
    if kcl > KCL_PU:
        reasons.append("kcl")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "evidence": "independent_mna",
        "max_voltage_error_pu": max(voltage_errors),
        "max_angle_error_rad": max(angle_errors),
        "max_current_error_a": max(current_errors),
        "source_power_error_w": source_power_error,
        "loss_error_w": abs(loss_actual - loss_expected),
        "kcl_pu": kcl,
        "source_power_w": source_power,
        "status": solved.status,
    }


def zip_network():
    builder = NetBuilder("zip-oracle", "ZIP", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.02, 0.08)
    builder.line_z1("src", "ld", 8.0 + 16.0j, line_id="line")
    builder.load(
        "ld",
        180_000.0,
        40_000.0,
        model="zip",
        zip_coeffs=(0.2, 0.3, 0.5, 0.25, 0.25, 0.5),
        load_id="load",
    )
    network = builder.build()
    network.sources[0].r0_ohm = network.sources[0].r_ohm
    network.sources[0].x0_ohm = network.sources[0].x_ohm
    return network


def zip_reference(network) -> complex:
    """Скалярный Ньютон прямой последовательности. Не использует OpenDSS."""
    vln = network.sources[0].ll_v / math.sqrt(3.0)
    emf = vln * np.exp(1j * network.sources[0].angle_a_rad)
    zs = network.sources[0].r_ohm + 1j * network.sources[0].x_ohm
    line = network.lines[0]
    z_line = line.r_ohm[0][0] + 1j * line.x_ohm[0][0]
    z_total = zs + z_line
    load = network.loads[0]
    p0 = sum(load.p_w.values()) / 3.0
    q0 = sum(load.q_var.values()) / 3.0
    voltage = emf * 0.97

    def power(value: complex) -> complex:
        magnitude = abs(value) / vln
        active = load.zip_z * magnitude**2 + load.zip_i * magnitude + load.zip_p
        reactive = load.zip_zq * magnitude**2 + load.zip_iq * magnitude + load.zip_pq
        return active * p0 + 1j * reactive * q0

    def residual(value: complex) -> complex:
        return (emf - value) / z_total - np.conj(power(value)) / np.conj(value)

    for _ in range(25):
        mismatch = residual(voltage)
        if abs(mismatch) < 1e-8:
            break
        step = 1e-6 * max(1.0, abs(voltage))
        d_real = (residual(voltage + step) - mismatch) / step
        d_imag = (residual(voltage + 1j * step) - mismatch) / step
        jacobian = np.array([[d_real.real, d_imag.real], [d_real.imag, d_imag.imag]])
        delta = np.linalg.solve(jacobian, np.array([mismatch.real, mismatch.imag]))
        voltage = voltage - (delta[0] + 1j * delta[1])
    else:
        raise RuntimeError("Ньютон ZIP не сошёлся")
    return voltage


def constant_pq_reference(network) -> complex:
    """Та же схема, если доли Z и I обнулены и остаётся только постоянная мощность."""
    cloned = network.model_copy(deep=True)
    load = cloned.loads[0]
    load.zip_z = load.zip_i = 0.0
    load.zip_p = 1.0
    load.zip_zq = load.zip_iq = 0.0
    load.zip_pq = 1.0
    return zip_reference(cloned)


def qualify_zip(network=None) -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.topology import compile_topology

    network = zip_network() if network is None else network
    solved = solve_network(network)
    expected = zip_reference(network)
    topology = compile_topology(network)
    bus_id = topology.node_to_bus["ld"]
    actual = _voltage(solved, bus_id, "A")
    vln = network.sources[0].ll_v / math.sqrt(3.0)
    voltage_error = abs(abs(actual) - abs(expected)) / vln
    angle_error = abs(_wrap(np.angle(actual) - np.angle(expected)))
    kcl = kcl_report(network, solved)["mismatch_pu"]
    reasons = []
    if solved.status != "converged":
        reasons.append("status")
    if voltage_error > VOLTAGE_PU:
        reasons.append("voltage")
    if angle_error > ANGLE_RAD:
        reasons.append("angle")
    if kcl > KCL_PU:
        reasons.append("kcl")
    pq_voltage = constant_pq_reference(network)
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "evidence": "independent_newton",
        "max_voltage_error_pu": voltage_error,
        "max_angle_error_rad": angle_error,
        "kcl_pu": kcl,
        "voltage_pu": abs(actual) / vln,
        "pq_voltage_pu": abs(pq_voltage) / vln,
        "status": solved.status,
    }


def pv_limit_network():
    """Радиальная схема, где уставка напряжения требует больше Q, чем позволяет предел."""
    builder = NetBuilder("pv-limit", "Предел Q", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("g")
    builder.source("src", 0.05, 0.2)
    builder.line_z1("src", "g", 2.0 + 4.0j, line_id="line")
    builder.load("g", 200_000.0, 80_000.0, model="constant_pq", load_id="load")
    builder.generator("g", 40_000.0, mode="pv", v_set_pu=1.0, q_min_var=-5_000.0, q_max_var=5_000.0, generator_id="gen")
    network = builder.build()
    network.sources[0].r0_ohm = network.sources[0].r_ohm
    network.sources[0].x0_ohm = network.sources[0].x_ohm
    return network


def _series(network) -> tuple[complex, complex]:
    source = network.sources[0]
    vln = source.ll_v / math.sqrt(3.0)
    emf = vln * np.exp(1j * source.angle_a_rad)
    line = network.lines[0]
    z_total = source.r_ohm + 1j * source.x_ohm + line.r_ohm[0][0] + 1j * line.x_ohm[0][0]
    return emf, z_total


def _newton_balance(emf: complex, z_total: complex, drawn) -> complex:
    voltage = emf * 0.95

    def residual(value: complex) -> complex:
        return (emf - value) / z_total - np.conj(drawn(value)) / np.conj(value)

    for _ in range(25):
        mismatch = residual(voltage)
        if abs(mismatch) < 1e-9:
            return voltage
        step = 1e-6 * max(1.0, abs(voltage))
        d_real = (residual(voltage + step) - mismatch) / step
        d_imag = (residual(voltage + 1j * step) - mismatch) / step
        jacobian = np.array([[d_real.real, d_imag.real], [d_real.imag, d_imag.imag]])
        delta = np.linalg.solve(jacobian, np.array([mismatch.real, mismatch.imag]))
        voltage = voltage - (delta[0] + 1j * delta[1])
    raise RuntimeError("Ньютон баланса узла не сошёлся")


def pv_limit_reference(network) -> dict[str, float | complex]:
    """На пределе машина отдаёт ровно Qmax. Напряжение уже не равно уставке."""
    emf, z_total = _series(network)
    load = network.loads[0]
    generator = network.generators[0]
    p_phase = (sum(load.p_w.values()) - generator.p_w) / 3.0
    q_phase = (sum(load.q_var.values()) - generator.q_max_var) / 3.0
    limited = _newton_balance(emf, z_total, lambda _voltage: p_phase + 1j * q_phase)
    vln = network.sources[0].ll_v / math.sqrt(3.0)
    angle = -0.01

    def active(theta: float) -> float:
        voltage = vln * np.exp(1j * theta)
        return float(np.real(voltage * np.conj((emf - voltage) / z_total)))

    for _ in range(25):
        mismatch = active(angle) - p_phase
        if abs(mismatch) < 1e-8:
            break
        slope = (active(angle + 1e-7) - active(angle)) / 1e-7
        angle -= mismatch / slope
    else:
        raise RuntimeError("угол уставки PV не сошёлся")
    held = vln * np.exp(1j * angle)
    delivered = held * np.conj((emf - held) / z_total)
    q_required = sum(load.q_var.values()) - 3.0 * float(np.imag(delivered))
    return {"voltage": limited, "vln": vln, "q_required_var": q_required}


def qualify_pv_limit(network=None) -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.topology import compile_topology

    network = pv_limit_network() if network is None else network
    solved = solve_network(network)
    reference = pv_limit_reference(network)
    topology = compile_topology(network)
    actual = _voltage(solved, topology.node_to_bus["g"], "A")
    expected = reference["voltage"]
    vln = float(reference["vln"])
    voltage_error = abs(abs(actual) - abs(expected)) / vln
    angle_error = abs(_wrap(np.angle(actual) - np.angle(expected)))
    q_into = sum(flow.q_into_var for flow in solved.flows if flow.equipment_id == "gen" and flow.phase in "ABC")
    q_error = abs((-q_into) - network.generators[0].q_max_var)
    kcl = kcl_report(network, solved)["mismatch_pu"]
    power_tolerance = max(POWER_FLOOR_W, POWER_RELATIVE * network.s_base_va)
    reasons = []
    if solved.status != "converged":
        reasons.append("status")
    if voltage_error > VOLTAGE_PU:
        reasons.append("voltage")
    if angle_error > ANGLE_RAD:
        reasons.append("angle")
    if q_error > power_tolerance:
        reasons.append("q_limit")
    if abs(actual) / vln > network.generators[0].v_set_pu - 1e-4:
        reasons.append("still_regulating")
    if reference["q_required_var"] <= network.generators[0].q_max_var:
        reasons.append("limit_not_binding")
    if kcl > KCL_PU:
        reasons.append("kcl")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "evidence": "independent_newton",
        "max_voltage_error_pu": voltage_error,
        "max_angle_error_rad": angle_error,
        "q_error_var": q_error,
        "q_required_var": reference["q_required_var"],
        "voltage_pu": abs(actual) / vln,
        "kcl_pu": kcl,
        "status": solved.status,
    }


def floating_neutral_network():
    """Четырёхпроводная линия. Звезда нагрузки сидит на нейтрали, у нейтрали нет земли."""
    builder = NetBuilder("float-n", "Плавающая нейтраль", 10.0, s_base_va=1.0e6)
    builder.node("src", phases="ABCN")
    builder.node("ld", phases="ABCN")
    builder.source("src", 0.02, 0.06)
    impedance = _line_impedance().copy()
    impedance[3, 3] = 40.0 + 10.0j
    builder.line_matrix("src", "ld", impedance.real.tolist(), impedance.imag.tolist(), phases="ABCN", line_id="feeder")
    builder.load(
        "ld",
        {"A": 30_000.0, "B": 10_000.0, "C": 20_000.0},
        {"A": 0.0, "B": 5_000.0, "C": -2_000.0},
        model="constant_z",
        ground="ungrounded",
        load_id="load",
    )
    builder.neutral_ground("src", "solid", ground_id="src-n")
    network = builder.build()
    network.sources[0].r0_ohm = 0.02
    network.sources[0].x0_ohm = 0.06
    return network


def floating_reference(network) -> dict[str, Any]:
    """Нагрузка включена между фазой и нейтралью. У нейтрали приёмника нет проводимости на землю."""
    vln = network.sources[0].ll_v / math.sqrt(3.0)
    emf = np.array([vln, vln * np.exp(-1j * 2.0 * math.pi / 3.0), vln * np.exp(1j * 2.0 * math.pi / 3.0)])
    zs = network.sources[0].r_ohm + 1j * network.sources[0].x_ohm
    line = network.lines[0]
    z_line = np.array(line.r_ohm, dtype=float) + 1j * np.array(line.x_ohm, dtype=float)
    y_line = np.linalg.inv(z_line)
    loads = {phase: network.loads[0].p_w[phase] + 1j * network.loads[0].q_var[phase] for phase in "ABC"}
    z_load = {phase: vln**2 / np.conj(power) for phase, power in loads.items()}
    admit = np.zeros((8, 8), dtype=np.complex128)
    inject = np.zeros(8, dtype=np.complex128)
    admit[:4, :4] += y_line
    admit[:4, 4:] -= y_line
    admit[4:, :4] -= y_line
    admit[4:, 4:] += y_line
    for index, voltage in enumerate(emf):
        admit[index, index] += 1.0 / zs
        inject[index] += voltage / zs
    admit[3, 3] += 1.0 / SOLID_NEUTRAL_R_OHM
    for index, phase in enumerate("ABC"):
        y = 1.0 / z_load[phase]
        phase_node = 4 + index
        admit[phase_node, phase_node] += y
        admit[7, 7] += y
        admit[phase_node, 7] -= y
        admit[7, phase_node] -= y
    voltage = np.linalg.solve(admit, inject)
    current = y_line @ (voltage[:4] - voltage[4:])
    return {"voltage": voltage, "line_current_from": current, "vln": vln}


def qualify_floating(network=None) -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.topology import compile_topology

    network = floating_neutral_network() if network is None else network
    solved = solve_network(network)
    reference = floating_reference(network)
    topology = compile_topology(network)
    voltage_errors = []
    angle_errors = []
    for node_index, node_id in enumerate(("src", "ld")):
        bus_id = topology.node_to_bus[node_id]
        for phase_index, phase in enumerate("ABCN"):
            expected = reference["voltage"][node_index * 4 + phase_index]
            actual = _voltage(solved, bus_id, phase)
            voltage_errors.append(abs(abs(actual) - abs(expected)) / reference["vln"])
            if abs(expected) > 1.0 and abs(actual) > 1.0:
                angle_errors.append(abs(_wrap(np.angle(actual) - np.angle(expected))))
    bus_from = topology.node_to_bus["src"]
    current_errors = [
        abs(_flow_current(solved, "feeder", bus_from, phase) - reference["line_current_from"][index])
        for index, phase in enumerate("ABCN")
    ]
    loss_error = _loss_error(solved, reference)
    kcl = kcl_report(network, solved)["mismatch_pu"]
    i_tolerance = max(CURRENT_FLOOR_A, CURRENT_RELATIVE * i_base(network.sources[0].ll_v, network.s_base_va))
    power_tolerance = max(POWER_FLOOR_W, POWER_RELATIVE * network.s_base_va)
    neutral_pu = abs(reference["voltage"][7]) / reference["vln"]
    reasons = []
    if solved.status != "converged":
        reasons.append("status")
    if max(voltage_errors) > VOLTAGE_PU:
        reasons.append("voltage")
    if max(angle_errors) > ANGLE_RAD:
        reasons.append("angle")
    if max(current_errors) > i_tolerance:
        reasons.append("current")
    if loss_error > power_tolerance:
        reasons.append("losses")
    if kcl > KCL_PU:
        reasons.append("kcl")
    if neutral_pu < 0.01:
        reasons.append("neutral_not_floating")
    if any(item.id == "ld-n" for item in network.neutral_grounds):
        reasons.append("load_neutral_grounded")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "evidence": "independent_mna",
        "max_voltage_error_pu": max(voltage_errors),
        "max_angle_error_rad": max(angle_errors),
        "max_current_error_a": max(current_errors),
        "loss_error_w": loss_error,
        "kcl_pu": kcl,
        "neutral_voltage_pu": neutral_pu,
        "status": solved.status,
    }


def dyn11_network():
    """Dyn11 с явным сопротивлением КЗ и отпайкой НН. Намагничивание и ppm обнулены."""
    builder = NetBuilder("dyn11-oracle", "Dyn11", 10.0, s_base_va=1.0e6)
    builder.node("hv", 10.0)
    builder.node("lv", 0.4)
    builder.source("hv", 0.05, 0.2)
    builder.transformer(
        "hv",
        "lv",
        vector_group="Dyn11",
        kva=1000,
        hv_kv=10.0,
        lv_kv=0.4,
        x_pct=6.0,
        r_pct=1.0,
        hv_ground="not_applicable",
        lv_ground="solid",
        transformer_id="transformer",
    )
    builder.load("lv", 300_000.0, 80_000.0, model="constant_z", load_id="load")
    network = builder.build()
    network.sources[0].r0_ohm = network.sources[0].r_ohm
    network.sources[0].x0_ohm = network.sources[0].x_ohm
    transformer = network.transformers[0]
    transformer.ppm_antifloat = 0.0
    transformer.windings[1].tap_pu = 1.025
    return network


def dyn11_reference(network) -> dict[str, Any]:
    """Прямая последовательность. Отпайка увеличивает число витков своей обмотки. НН опережает на 30°."""
    source = network.sources[0]
    transformer = network.transformers[0]
    vln_hv = transformer.windings[0].nominal_ll_v / math.sqrt(3.0)
    vln_lv = transformer.windings[1].nominal_ll_v / math.sqrt(3.0)
    emf = (source.ll_v / math.sqrt(3.0)) * np.exp(1j * source.angle_a_rad)
    z_source = source.r_ohm + 1j * source.x_ohm
    z_base = transformer.windings[0].nominal_ll_v**2 / transformer.windings[0].rated_va
    z_hv = (transformer.load_loss_pct + 1j * transformer.x_hl_pct) / 100.0 * z_base
    current_ratio = (vln_lv / vln_hv) * (transformer.windings[1].tap_pu / transformer.windings[0].tap_pu)
    shift = np.exp(1j * math.radians(30.0))
    coupling = current_ratio * shift
    load = network.loads[0]
    power = (sum(load.p_w.values()) + 1j * sum(load.q_var.values())) / 3.0
    z_load = vln_lv**2 / np.conj(power)
    series = z_source + z_hv
    v_lv = emf * coupling / (1.0 + (abs(coupling) ** 2) * series / z_load)
    i_hv = v_lv * np.conj(coupling) / z_load
    v_hv = emf - i_hv * z_source
    i_lv = v_lv / z_load
    lag = np.exp(-1j * 2.0 * math.pi / 3.0)
    lead = np.exp(1j * 2.0 * math.pi / 3.0)
    turns = {"A": 1.0 + 0.0j, "B": lag, "C": lead}
    return {
        "hv": {phase: v_hv * turn for phase, turn in turns.items()},
        "lv": {phase: v_lv * turn for phase, turn in turns.items()},
        "i_hv": {phase: i_hv * turn for phase, turn in turns.items()},
        "i_lv_into": {phase: -i_lv * turn for phase, turn in turns.items()},
        "loss_w": 3.0 * float(np.real(z_hv) * abs(i_hv) ** 2),
        "vln_hv": vln_hv,
        "shift_rad": float(_wrap(np.angle(v_lv) - np.angle(v_hv))),
    }


def qualify_dyn11(network=None) -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.topology import compile_topology

    network = dyn11_network() if network is None else network
    solved = solve_network(network)
    reference = dyn11_reference(network)
    topology = compile_topology(network)
    buses = {"hv": topology.node_to_bus["hv"], "lv": topology.node_to_bus["lv"]}
    voltage_errors = []
    angle_errors = []
    current_errors = []
    for side, currents in (("hv", reference["i_hv"]), ("lv", reference["i_lv_into"])):
        for phase, expected in reference[side].items():
            actual = _voltage(solved, buses[side], phase)
            voltage_errors.append(abs(abs(actual) - abs(expected)) / reference["vln_hv"])
            angle_errors.append(abs(_wrap(np.angle(actual) - np.angle(expected))))
            found = next(
                item.current
                for item in solved.flows
                if item.equipment_id == "transformer" and item.bus_id == buses[side] and item.phase == phase
            )
            current_errors.append(abs(found - currents[phase]))
    loss_actual = sum(flow.p_into_w for flow in solved.flows if flow.equipment_id == "transformer")
    loss_error = abs(loss_actual - reference["loss_w"])
    kcl = kcl_report(network, solved)["mismatch_pu"]
    i_tolerance = max(CURRENT_FLOOR_A, CURRENT_RELATIVE * i_base(network.sources[0].ll_v, network.s_base_va))
    power_tolerance = max(POWER_FLOOR_W, POWER_RELATIVE * network.s_base_va)
    reasons = []
    if solved.status != "converged":
        reasons.append("status")
    if max(voltage_errors) > VOLTAGE_PU:
        reasons.append("voltage")
    if max(angle_errors) > ANGLE_RAD:
        reasons.append("angle")
    if max(current_errors) > i_tolerance:
        reasons.append("current")
    if loss_error > power_tolerance:
        reasons.append("losses")
    if kcl > KCL_PU:
        reasons.append("kcl")
    if abs(reference["shift_rad"] - math.radians(30.0)) > math.radians(2.0):
        reasons.append("shift_not_dyn11")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "evidence": "independent_sequence",
        "max_voltage_error_pu": max(voltage_errors),
        "max_angle_error_rad": max(angle_errors),
        "max_current_error_a": max(current_errors),
        "loss_error_w": loss_error,
        "kcl_pu": kcl,
        "shift_deg": math.degrees(reference["shift_rad"]),
        "status": solved.status,
    }

