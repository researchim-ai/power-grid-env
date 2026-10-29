"""Напряжения здоровых фаз, несимметрия, статизм, шунт и угол передачи.

Формулы записаны отдельно от фазного решения.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from power_grid_env.conventions import A_OPERATOR

LN = 10_000.0 / math.sqrt(3.0)


def slg_phase_voltages(
    emf: complex,
    z1: complex,
    z0: complex,
    z_fault: complex,
) -> tuple[complex, complex, complex, complex]:
    """Напряжения фаз при КЗ фазы A. Z2 = Z1. Реактор и источник уже в Z0."""
    current = emf / (2.0 * z1 + z0 + 3.0 * z_fault)
    positive = emf - z1 * current
    negative = -z1 * current
    zero = -z0 * current
    operator = A_OPERATOR
    phase_a = zero + positive + negative
    phase_b = zero + operator**2 * positive + operator * negative
    phase_c = zero + operator * positive + operator**2 * negative
    return phase_a, phase_b, phase_c, 3.0 * current


def current_unbalance(phase_currents: list[complex]) -> float:
    """|I2| / |I1|. Для одной фазы отношение равно 1, для симметрии — нулю."""
    if len(phase_currents) != 3:
        raise ValueError("несимметрия считается по трём фазным токам")
    zero, positive, negative = _components(*phase_currents)
    if abs(positive) < 1e-12:
        raise ValueError("ток прямой последовательности слишком мал")
    return abs(negative) / abs(positive)


def sequence_power(voltages: list[complex], currents: list[complex]) -> complex:
    """3 (V0 I0* + V1 I1* + V2 I2*). Совпадает с суммой фазных мощностей."""
    terms = 0j
    for voltage, current in zip(_components(*voltages), _components(*currents), strict=True):
        terms += voltage * np.conjugate(current)
    return 3.0 * terms


def droop_share(power_w: float, droop_hz_per_w: list[float]) -> list[float]:
    """Доля обратно пропорциональна статизму. Сумма долей равна мощности нагрузки."""
    if power_w < 0 or len(droop_hz_per_w) < 1 or any(item <= 0 for item in droop_hz_per_w):
        raise ValueError("мощность неотрицательна, статизмы положительны")
    weights = [1.0 / item for item in droop_hz_per_w]
    total = sum(weights)
    return [power_w * weight / total for weight in weights]


def qualify_healthy_phases() -> dict[str, Any]:
    z1 = 0.2 + 0.5j
    z0 = 8.0 + 20.0j
    z_fault = 0.2 + 0.0j
    expected = slg_phase_voltages(LN, z1, z0, z_fault)
    equal = slg_phase_voltages(LN, z1, z1, z_fault)
    measured = _slg_voltages(z1, z0, z_fault.real)
    reasons = []
    if measured[1] != "converged":
        reasons.append("status")
    errors = [abs(measured[0][index] - expected[index]) / LN for index in range(3)]
    if max(errors) > 1e-4:
        reasons.append("voltage")
    if abs(expected[1]) < 1.2 * LN or abs(equal[1]) > 1.05 * LN:
        reasons.append("rise")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "voltage_error": max(errors),
        "healthy_pu": abs(expected[1]) / LN,
    }


def qualify_regulation() -> dict[str, Any]:
    z_line = 0.4 + 2.0j
    lagging = _receiving_voltage(z_line, 400_000.0, 200_000.0)
    leading = _receiving_voltage(z_line, 400_000.0, -200_000.0)
    reasons = []
    if lagging[1] != "converged" or leading[1] != "converged":
        reasons.append("status")
    if max(lagging[2], leading[2]) > 1e-5:
        reasons.append("divider")
    if abs(lagging[0]) >= abs(leading[0]):
        reasons.append("power_factor")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "divider_error": max(lagging[2], leading[2]),
    }


def qualify_unbalance() -> dict[str, Any]:
    single = _phase_currents(False)
    balanced = _phase_currents(True)
    reasons = []
    if single[1] != "converged" or balanced[1] != "converged":
        reasons.append("status")
    single_ratio = current_unbalance(single[0])
    balanced_ratio = current_unbalance(balanced[0])
    phase_power = sum(voltage * np.conjugate(current) for voltage, current in zip(single[2], single[0], strict=True))
    sequence = sequence_power(single[2], single[0])
    if abs(single_ratio - 1.0) > 1e-3 or balanced_ratio > 1e-3:
        reasons.append("ratio")
    if abs(sequence - phase_power) / max(abs(phase_power), 1.0) > 1e-6:
        reasons.append("power")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "single_ratio": single_ratio,
        "balanced_ratio": balanced_ratio,
    }


def qualify_share() -> dict[str, Any]:
    total = 300_000.0
    share = droop_share(total, [2.0, 1.0])
    equal = droop_share(total, [1.0, 1.0])
    measured = _shared_source(share)
    reasons = []
    if abs(share[0] - 100_000.0) > 1e-6 or abs(share[1] - 200_000.0) > 1e-6:
        reasons.append("split")
    if abs(equal[0] - equal[1]) > 1e-6:
        reasons.append("equal")
    if measured[1] != "converged":
        reasons.append("status")
    if measured[2] > 1e-3:
        reasons.append("balance")
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "source_error": measured[2], "share_w": share}


def qualify_shunt() -> dict[str, Any]:
    bare = _receiving_voltage(0.8 + 2.0j, 300_000.0, 40_000.0)
    with_shunt = _shunt_case()
    reasons = []
    if bare[1] != "converged" or with_shunt[1] != "converged":
        reasons.append("status")
    if with_shunt[2] > 1e-3:
        reasons.append("reactive")
    if abs(with_shunt[0]) <= abs(bare[0]):
        reasons.append("rise")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "reactive_error": with_shunt[2],
    }


def qualify_angle() -> dict[str, Any]:
    z_line = 0.1 + 1.5j
    z_load = 40.0 + 8.0j
    expected = LN * z_load / (z_line + z_load)
    measured = _receiving_voltage(z_line, float(3.0 * (LN**2 / np.conj(z_load)).real), float(3.0 * (LN**2 / np.conj(z_load)).imag))
    reasons = []
    if measured[1] != "converged":
        reasons.append("status")
    error = abs(math.atan2(measured[0].imag, measured[0].real) - math.atan2(expected.imag, expected.real))
    if error > 1e-4:
        reasons.append("angle")
    if measured[2] > 1e-5:
        reasons.append("voltage")
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "angle_error": error}


def _components(first: complex, second: complex, third: complex) -> tuple[complex, complex, complex]:
    operator = A_OPERATOR
    zero = (first + second + third) / 3.0
    positive = (first + operator * second + operator**2 * third) / 3.0
    negative = (first + operator**2 * second + operator * third) / 3.0
    return zero, positive, negative


def _slg_voltages(z1: complex, z0: complex, fault_r: float) -> tuple[list[complex], str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.model import StudyFault
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("healthy", "Здоровые", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", z1.real, z1.imag)
    network = builder.build()
    network.sources[0].r0_ohm = z0.real
    network.sources[0].x0_ohm = z0.imag
    network.study_faults.append(StudyFault(id="slg", bus_id="src", kind="slg", phases=["A"], r_ohm=fault_r))
    solved = solve_network(network)
    if solved.status != "converged":
        return [0j, 0j, 0j], solved.status
    bus = compile_topology(network).node_to_bus["src"]
    voltages = [
        next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == phase) for phase in "ABC"
    ]
    return voltages, solved.status


def _receiving_voltage(z_line: complex, active_w: float, reactive_var: float) -> tuple[complex, str, float]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    phase = (active_w + 1j * reactive_var) / 3.0
    z_load = LN**2 / np.conj(phase)
    expected = LN * z_load / (1.0e-5 + 1.0e-5j + z_line + z_load)
    builder = NetBuilder("reg", "Падение", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 1.0e-5, 1.0e-5)
    builder.line_z1("src", "ld", z_line, line_id="line")
    builder.load("ld", active_w, reactive_var, model="constant_z", load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return 0j, solved.status, 1.0
    bus = compile_topology(network).node_to_bus["ld"]
    voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    return voltage, solved.status, abs(voltage - expected) / LN


def _phase_currents(balanced: bool) -> tuple[list[complex], str, list[complex]]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("unb", "Несимметрия", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 0.05, 0.1)
    if balanced:
        builder.load("src", 300_000.0, 0.0, load_id="load")
    else:
        builder.load("src", 100_000.0, 0.0, phases="A", load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return [0j, 0j, 0j], solved.status, [0j, 0j, 0j]
    bus = compile_topology(network).node_to_bus["src"]
    currents = [
        next(item.current for item in solved.flows if item.equipment_id == "source" and item.bus_id == bus and item.phase == phase)
        for phase in "ABC"
    ]
    voltages = [
        next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == phase) for phase in "ABC"
    ]
    return currents, solved.status, voltages


def _shared_source(share: list[float]) -> tuple[float, str, float]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    builder = NetBuilder("share", "Статизм", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 0.01, 0.02)
    builder.generator("src", share[0], generator_id="g1")
    builder.generator("src", share[1], generator_id="g2")
    builder.load("src", sum(share), 0.0, load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status, 1.0
    residual = -sum(item.p_into_w for item in solved.flows if item.equipment_id == "source" and item.phase in "ABC")
    return residual, solved.status, abs(residual) / sum(share)


def _shunt_case() -> tuple[complex, str, float]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("shunt", "Конденсатор", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 1.0e-5, 1.0e-5)
    builder.line_z1("src", "ld", 0.8 + 2.0j, line_id="line")
    builder.load("ld", 300_000.0, 40_000.0, model="constant_z", load_id="load")
    builder.shunt("ld", 150.0, shunt_id="bank")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return 0j, solved.status, 1.0
    bus = compile_topology(network).node_to_bus["ld"]
    voltage = next(item for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    nominal = abs(voltage.complex) / voltage.v_base_ln_v
    reactive = sum(item.q_into_var for item in solved.flows if item.equipment_id == "bank" and item.phase in "ABC")
    expected = -150_000.0 * nominal**2
    return voltage.complex, solved.status, abs(reactive - expected) / 150_000.0
