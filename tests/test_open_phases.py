"""Открытые этапы: вклад КЗ в сетке, Ib, CT/VT, грубая ошибка и старт RMS из режима."""

import math

import pytest

from power_grid_env.analysis import ac_opf_two_bus, gross_error, wls_dc
from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder
from power_grid_env.checks import kcl_report
from power_grid_env.dynamics import state_from_terminal_voltage
from power_grid_env.iec60909 import ac_decrement_factor, breaking_current, initial_symmetrical_three_phase
from power_grid_env.industrial_batch import qualify_mesh_fault
from power_grid_env.protection import critical_stays_supplied, residual_current, trip_from_secondary
import numpy as np


def test_mesh_fault_splits_the_current_between_two_sources():
    report = qualify_mesh_fault()
    assert report["verdict"] == "pass", report
    assert report["not_iec_60909"] is True
    assert report["max_voltage_error_pu"] < 1e-5
    assert report["max_current_error"] < 1e-5


def test_breaking_current_uses_mu_only_near_a_synchronous_machine():
    initial = initial_symmetrical_three_phase(10_000.0, 1.1, 0.05 + 0.4j)
    far, mu_far = breaking_current(initial, near_synchronous=False)
    assert mu_far == 1.0
    assert far == initial
    near, mu_near = breaking_current(initial, near_synchronous=True, short_circuit_over_rated=20.0, minimum_time_s=0.02)
    assert mu_near == pytest.approx(ac_decrement_factor(20.0, 0.02))
    assert mu_near < 1.0
    assert abs(near) == pytest.approx(abs(initial) * mu_near)
    assert ac_decrement_factor(0.01, 0.02) == 1.0
    with pytest.raises(ValueError, match="tmin"):
        ac_decrement_factor(2.0, 0.03)


def test_ct_blocks_a_trip_when_the_measurement_is_missing_and_earth_uses_residual():
    seen = trip_from_secondary(400.0, 200.0, 5.0, 6.0, available=True)
    assert seen["secondary"] == pytest.approx(10.0)
    assert seen["trip"] is True
    blind = trip_from_secondary(400.0, 200.0, 5.0, 6.0, available=False)
    assert blind["trip"] is False
    assert blind["reason"] == "measurement_unavailable"
    quiet = trip_from_secondary(40.0, 200.0, 5.0, 6.0, available=True)
    assert quiet["trip"] is False
    operator = np.exp(1j * 2.0 * math.pi / 3.0)
    balanced = residual_current([1.0 + 0.0j, operator**2, operator])
    assert abs(balanced) == pytest.approx(0.0, abs=1e-12)
    assert abs(residual_current([10.0 + 0.0j, 0.0j, 0.0j])) == pytest.approx(10.0)
    supplied = {"down": {"fault"}, "up": {"critical", "fault"}}
    assert critical_stays_supplied("down", supplied, "critical") is True
    assert critical_stays_supplied("up", supplied, "critical") is False


def test_a_gross_measurement_error_is_indexed_and_the_setpoint_is_resolved_on_the_phase_network():
    estimate = wls_dc(np.array([0.0, 0.0, 10.0]), np.ones((3, 1)), np.full(3, 0.01))
    flagged = gross_error(estimate["normalized"])
    assert flagged["index"] == 2
    clean = wls_dc(np.array([1.0, 1.01]), np.array([[1.0], [1.0]]), np.array([1.0, 1.0]))
    assert gross_error(clean["normalized"])["index"] is None
    assert gross_error(None)["observable"] is False

    opf = ac_opf_two_bus(load_p=0.5, load_q=0.1, x_pu=0.2, cost=10.0, pmax=1.0)
    assert opf["success"] is True
    assert opf["global_optimum_claimed"] is False
    power = opf["pg_pu"] * 1.0e6
    builder = NetBuilder("recheck", "Проверка", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.05, 0.2)
    builder.line_z1("src", "ld", 0.5 + 20.0j, line_id="line")
    builder.load("ld", power, 0.1e6, load_id="load")
    accepted = builder.build()
    solved = solve_network(accepted)
    assert solved.status == "converged"
    assert kcl_report(accepted, solved)["mismatch_pu"] < 1e-8
    current = max(abs(flow.current) for flow in solved.flows if flow.equipment_id == "line" and flow.phase in "ABC")
    assert current < accepted.lines[0].rating_a
    tight = accepted.model_copy(deep=True)
    tight.lines[0].rating_a = 1.0
    assert current > tight.lines[0].rating_a


def test_rms_starts_from_the_power_flow_voltage_without_a_time_step():
    state = state_from_terminal_voltage(0.6, 1.1, 1.0, 0.4, 5.0, 2.0, 2.0 * math.pi * 50.0)
    assert state["steps"] == 0.0
    assert state["residual_max"] < 1e-9
    assert state["omega_rad_s"] == pytest.approx(2.0 * math.pi * 50.0)
