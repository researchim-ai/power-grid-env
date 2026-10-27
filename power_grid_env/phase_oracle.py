"""Независимый линейный расчёт несимметричной четырёхпроводной схемы.

Это MNA на заданных импедансах, не обёртка OpenDSS. Пороги взяты из плана
до просмотра численной разницы: |V| 1e-5 о.е., угол 1e-4 рад, KCL 1e-8 о.е.
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
