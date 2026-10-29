"""Одно включение RL: фазор сети, действующий ток и мгновенная цепь.

Натурным измерением расчёт не становится. Полный EMT сети здесь не считается.
"""

from __future__ import annotations

import math
from typing import Any

from power_grid_env.dynamics import ac_component
from power_grid_env.fault_envelope import (
    accept_or_restore,
    boundary_midpoint_error,
    closed_form,
    energy_imbalance,
    steady_current,
    trapezoid,
)

LN = 10_000.0 / math.sqrt(3.0)
LOAD_Z = 2.0 + 1.0j
SOURCE_Z = 0.05 + 0.1j
FREQUENCY = 50.0


def qualify_event_levels() -> dict[str, Any]:
    """Ток включения объясняется сетью, установившимся действующим значением и огибающей."""
    measured = _network_current()
    total = SOURCE_Z + LOAD_Z
    expected = abs(LN / total)
    terminal = abs(LN * LOAD_Z / total)
    steady_rms = terminal / abs(LOAD_Z)
    offset = steady_current(0.0, terminal, LOAD_Z.real, LOAD_Z.imag, FREQUENCY)
    coarse = abs(trapezoid(0.02, 2.0e-4, terminal, LOAD_Z.real, LOAD_Z.imag, FREQUENCY, offset) - steady_current(0.02, terminal, LOAD_Z.real, LOAD_Z.imag, FREQUENCY))
    fine = abs(trapezoid(0.02, 1.0e-4, terminal, LOAD_Z.real, LOAD_Z.imag, FREQUENCY, offset) - steady_current(0.02, terminal, LOAD_Z.real, LOAD_Z.imag, FREQUENCY))
    hold = boundary_midpoint_error(1.0e-3, FREQUENCY, "hold")
    linear = boundary_midpoint_error(1.0e-3, FREQUENCY, "linear")
    linear_fine = boundary_midpoint_error(5.0e-4, FREQUENCY, "linear")
    rejected, rejected_flag = accept_or_restore(offset, 0.0, 1.0e-4, terminal, LOAD_Z.real, LOAD_Z.imag, FREQUENCY, accept=False)
    accepted, accepted_flag = accept_or_restore(offset, 0.0, 1.0e-4, terminal, LOAD_Z.real, LOAD_Z.imag, FREQUENCY, accept=True)
    late = ac_component(20.0, 1.4 * steady_rms, 1.2 * steady_rms, steady_rms, 0.05, 0.8)
    early = ac_component(0.0, 1.4 * steady_rms, 1.2 * steady_rms, steady_rms, 0.05, 0.8)
    energy = energy_imbalance(0.02, 1.0e-4, terminal, LOAD_Z.real, LOAD_Z.imag, FREQUENCY)
    reasons = []
    if measured[1] != "converged":
        reasons.append("status")
    if abs(measured[0] - expected) / expected > 1e-4:
        reasons.append("network")
    if abs(steady_rms - expected) / expected > 1e-12:
        reasons.append("mapping")
    if abs(closed_form(0.0, terminal, LOAD_Z.real, LOAD_Z.imag, FREQUENCY)) > 1e-12:
        reasons.append("zero_state")
    explained = _explained_transient(0.01, terminal, offset)
    if abs(explained - closed_form(0.01, terminal, LOAD_Z.real, LOAD_Z.imag, FREQUENCY)) > 1e-9:
        reasons.append("offset")
    if not fine < coarse or coarse / fine < 3.0 or coarse / fine > 5.0:
        reasons.append("step")
    if not linear < hold or not linear_fine < linear / 2.0:
        reasons.append("boundary")
    if rejected != offset or rejected_flag or not accepted_flag:
        reasons.append("rollback")
    if abs(accepted - trapezoid(1.0e-4, 1.0e-4, terminal, LOAD_Z.real, LOAD_Z.imag, FREQUENCY, offset)) > 1e-9:
        reasons.append("accepted_step")
    if abs(late - steady_rms) / steady_rms > 1e-9 or not early > late:
        reasons.append("rms")
    if abs(energy) > 1e-6:
        reasons.append("energy")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "current_error": abs(measured[0] - expected) / expected,
        "step_ratio": coarse / fine if fine else math.inf,
        "boundary_ratio": linear_fine / linear,
        "energy_imbalance_j": energy,
        "field_status": "waiting_for_data",
        "full_emt": False,
        "synthetic_is_field": False,
    }


def _explained_transient(time_s: float, terminal: float, initial: float) -> float:
    omega = 2.0 * math.pi * FREQUENCY
    tau = LOAD_Z.imag / (omega * LOAD_Z.real)
    periodic = steady_current(time_s, terminal, LOAD_Z.real, LOAD_Z.imag, FREQUENCY)
    return periodic - initial * math.exp(-time_s / tau)


def _network_current() -> tuple[float, str]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder

    power = 3.0 * (LN**2 / LOAD_Z.conjugate())
    builder = NetBuilder("couple", "Связка", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.source("src", SOURCE_Z.real, SOURCE_Z.imag)
    builder.load("src", float(power.real), float(power.imag), model="constant_z", load_id="load")
    solved = solve_network(builder.build())
    if solved.status != "converged":
        return math.nan, solved.status
    current = abs(next(item.current for item in solved.flows if item.equipment_id == "load" and item.phase == "A"))
    return current, solved.status
