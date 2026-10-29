"""Предел тока, дрооп реактивной мощности и окно противоаварийного отключения.

Ток КЗ инвертора — заданный Isource, не ЭДС за сопротивлением.
Дрооп задаёт ЭДС до расчёта сети. Окно частоты смотрит измеренную мощность.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from power_grid_env.results import SolveOptions


def fault_current_setpoint(s_rated_va: float, ll_v: float, multiple: float) -> float:
    if s_rated_va <= 0 or ll_v <= 0 or multiple <= 0:
        raise ValueError("номинал, напряжение и кратность тока должны быть положительными")
    return multiple * s_rated_va / (math.sqrt(3.0) * ll_v)


def bus_voltage(emf: complex, z_source: complex, z_fault: complex, injected: complex) -> complex:
    """Напряжение шины: источник за Z и втекающий в шину ток инвертора, шунт Zf."""
    return (emf / z_source + injected) / (1.0 / z_source + 1.0 / z_fault)


def reactive_droop(e0: float, grid: float, impedance: complex, kq_v_per_var: float) -> tuple[float, float]:
    """E = E0 − kq Q. Q = 3 E (E − V) X / |Z|². Корень квадратного уравнения, не итерация."""
    if kq_v_per_var < 0 or e0 <= 0 or grid <= 0:
        raise ValueError("ЭДС, напряжение сети и коэффициент дроопа должны быть неотрицательными")
    reactance = float(impedance.imag)
    magnitude_sq = float(abs(impedance) ** 2)
    if kq_v_per_var == 0.0 or abs(reactance) < 1e-15:
        voltage = e0
    else:
        scale = kq_v_per_var * 3.0 * reactance / magnitude_sq
        discriminant = (1.0 - scale * grid) ** 2 + 4.0 * scale * e0
        voltage = (-(1.0 - scale * grid) + math.sqrt(discriminant)) / (2.0 * scale)
    current = (voltage - grid) / impedance
    reactive = 3.0 * float((voltage * np.conjugate(current)).imag)
    return voltage, reactive


def frequency_droop_hz(reference_hz: float, power_w: float, kf_hz_per_w: float) -> float:
    if kf_hz_per_w < 0 or reference_hz <= 0:
        raise ValueError("частота и статизм должны быть неотрицательными")
    return reference_hz - kf_hz_per_w * power_w


def anti_island_window(
    voltage_pu: float,
    frequency_hz: float,
    *,
    v_min: float,
    v_max: float,
    f_min: float,
    f_max: float,
    available: bool = True,
) -> dict[str, bool | str]:
    """Прямоугольное окно. На границе удержания отключения нет. Нет измерения — нет отключения."""
    if v_min <= 0 or f_min <= 0 or v_max <= v_min or f_max <= f_min:
        raise ValueError("окно напряжения и частоты должно быть положительным и невырожденным")
    if not available:
        return {"trip": False, "reason": "measurement_unavailable"}
    if voltage_pu < v_min:
        return {"trip": True, "reason": "undervoltage"}
    if voltage_pu > v_max:
        return {"trip": True, "reason": "overvoltage"}
    if frequency_hz < f_min:
        return {"trip": True, "reason": "underfrequency"}
    if frequency_hz > f_max:
        return {"trip": True, "reason": "overfrequency"}
    return {"trip": False, "reason": "inside"}


def qualify_fault_current() -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    z_source = 0.3 + 0.8j
    z_fault = 1.0 + 0j
    rated = 500_000.0
    multiple = 1.5
    setpoint = fault_current_setpoint(rated, 10_000.0, multiple)
    limited = _inverter_fault(z_source, z_fault, rated, 5_000.0, multiple, limited=True)
    free = _inverter_fault(z_source, z_fault, rated, 5_000.0, multiple, limited=False)
    emf = 10_000.0 / math.sqrt(3.0)
    expected = bus_voltage(emf, z_source, z_fault, setpoint + 0j)
    reasons = []
    if limited[2] != "converged" or free[2] != "converged":
        reasons.append("status")
    current_error = abs(limited[0] - setpoint) / setpoint
    voltage_error = abs(limited[1] - expected) / abs(expected)
    if current_error > 1e-4 or voltage_error > 1e-4:
        reasons.append("kcl")
    if abs(free[0] - setpoint) / setpoint < 0.2:
        reasons.append("unlimited")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "current_error": current_error,
        "voltage_error": voltage_error,
        "setpoint_a": setpoint,
        "unlimited_a": free[0],
    }


def qualify_droop() -> dict[str, Any]:
    grid = 10_000.0 / math.sqrt(3.0)
    impedance = 0.0002 + 2.0002j
    kq = 2.0e-4
    e0 = grid * 1.04
    voltage, reactive = reactive_droop(e0, grid, impedance, kq)
    held, held_q = reactive_droop(e0, grid, impedance, 0.0)
    measured = _droop_network(voltage)
    reasons = []
    if measured[1] != "converged":
        reasons.append("status")
    error = abs(-measured[0] - reactive) / max(abs(reactive), 1.0)
    if error > 1e-4:
        reasons.append("reactive")
    if not reactive < held_q:
        reasons.append("droop")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "reactive_error": error,
        "voltage_v": voltage,
        "held_voltage_v": held,
        "reactive_var": reactive,
    }


def qualify_anti_island() -> dict[str, Any]:
    kf = 1.0e-5
    light = _bus_power(80_000.0)
    heavy = _bus_power(120_000.0)
    light_hz = frequency_droop_hz(50.0, light[0], kf)
    heavy_hz = frequency_droop_hz(50.0, heavy[0], kf)
    hold = anti_island_window(1.0, light_hz, v_min=0.88, v_max=1.1, f_min=49.0, f_max=51.0)
    trip = anti_island_window(1.0, heavy_hz, v_min=0.88, v_max=1.1, f_min=49.0, f_max=51.0)
    low = anti_island_window(0.5, 50.0, v_min=0.88, v_max=1.1, f_min=49.0, f_max=51.0)
    high = anti_island_window(1.2, 50.0, v_min=0.88, v_max=1.1, f_min=49.0, f_max=51.0)
    blind = anti_island_window(0.5, 47.0, v_min=0.88, v_max=1.1, f_min=49.0, f_max=51.0, available=False)
    reasons = []
    if light[1] != "converged" or heavy[1] != "converged":
        reasons.append("status")
    if light[2] > 1e-4 or heavy[2] > 1e-4:
        reasons.append("power")
    if hold["trip"] or not trip["trip"] or trip["reason"] != "underfrequency":
        reasons.append("frequency")
    if low["reason"] != "undervoltage" or high["reason"] != "overvoltage" or blind["trip"]:
        reasons.append("window")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "light_hz": light_hz,
        "heavy_hz": heavy_hz,
        "power_error": max(light[2], heavy[2]),
    }


def _inverter_fault(
    z_source: complex,
    z_fault: complex,
    rated: float,
    active: float,
    multiple: float,
    *,
    limited: bool,
) -> tuple[float, complex, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.model import StudyFault
    from power_grid_env.topology import compile_topology

    builder = NetBuilder("iclip", "Ток", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", z_source.real, z_source.imag)
    builder.inverter("src", s_rated_va=rated, p_w=active, q_var=0.0, fault_current_multiple=multiple, inverter_id="pv")
    network = builder.build()
    network.sources[0].r0_ohm = z_source.real
    network.sources[0].x0_ohm = z_source.imag
    network.study_faults.append(StudyFault(id="fault", bus_id="src", kind="3ph", phases=["A", "B", "C"], r_ohm=z_fault.real))
    options = SolveOptions(inverter_current_limit_model=limited)
    solved = solve_network(network, options)
    if solved.status != "converged":
        return math.nan, 0j, solved.status
    bus = compile_topology(network).node_to_bus["src"]
    current = next(item.current for item in solved.flows if item.equipment_id == "pv" and item.bus_id == bus and item.phase == "A")
    voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    return abs(current), voltage, solved.status


def _droop_network(internal_v: float) -> tuple[float, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    builder = NetBuilder("droop", "Дрооп", 10.0, s_base_va=1.0e6)
    builder.node("grid")
    builder.node("inv")
    builder.source("grid", 1.0e-4, 1.0e-4, source_id="grid")
    builder.source("inv", 1.0e-4, 1.0e-4, source_id="inv", is_slack=False, ll_v=internal_v * math.sqrt(3.0))
    builder.line_z1("grid", "inv", 2.0j, line_id="line")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status
    reactive = sum(item.q_into_var for item in solved.flows if item.equipment_id == "inv" and item.phase in "ABC")
    return reactive, solved.status


def _bus_power(active_w: float) -> tuple[float, str, float]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    builder = NetBuilder("island", "Окно", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", 0.01, 0.02)
    builder.load("src", active_w, 0.0, load_id="load")
    network = builder.build()
    solved = solve_network(network)
    if solved.status != "converged":
        return math.nan, solved.status, 1.0
    measured = -sum(item.p_into_w for item in solved.flows if item.equipment_id == "source" and item.phase in "ABC")
    return measured, solved.status, abs(measured - active_w) / active_w
