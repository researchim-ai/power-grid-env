"""Десять расчётов с отдельной формулой и отрицательным случаем."""

import math

import numpy as np
import pytest

from power_grid_env.analysis import reject_gross_errors
from power_grid_env.converter_controls import qualify_anti_island, qualify_droop, qualify_fault_current
from power_grid_env.deep_bar import qualify as qualify_deep_bar
from power_grid_env.dynamics import equilibrium, feeder_restart, simulate_group_runup, simulate_smib
from power_grid_env.limit_mutations import qualify as qualify_mutations
from power_grid_env.sequence_fault import qualify_dyn11, qualify_earth, qualify_ynyn0
from power_grid_env.shedding import qualify as qualify_shedding


def test_ynyn0_slg_passes_the_source_zero_sequence():
    report = qualify_ynyn0()
    assert report["verdict"] == "pass", report
    assert report["current_error"] < 1e-5


def test_dyn11_blocks_zero_sequence_on_the_high_side():
    report = qualify_dyn11()
    assert report["verdict"] == "pass", report
    assert report["current_error"] < 1e-5
    assert report["hv_residual_a"] < 1e-2


def test_double_cage_matches_its_branches_and_raises_the_starting_torque():
    report = qualify_deep_bar()
    assert report["verdict"] == "pass", report
    assert report["power_error"] < 1e-9
    assert report["starting_ratio"] > 1.5
    assert report["inner_share"] < 0.5
    assert report["slot_field"] is False


def test_group_runup_waits_for_the_first_motor_and_refuses_the_stalled_one():
    emf = 400.0 + 0j
    impedance = 0.02 + 0.05j
    motors = [
        {"id": "m1", "priority": 1, "rotor_r": 0.2, "reactance": 1.5, "load_torque": 2000.0},
        {"id": "m2", "priority": 2, "rotor_r": 0.2, "reactance": 1.5, "load_torque": 2000.0},
        {"id": "m3", "priority": 3, "rotor_r": 0.2, "reactance": 1.5, "load_torque": 80_000.0},
    ]
    report = simulate_group_runup(
        emf,
        impedance,
        motors,
        minimum_v=50.0,
        release_slip=0.2,
        inertia_kgm2=0.2,
        omega_sync=2.0 * math.pi * 50.0,
        dt_s=0.004,
        t_end_s=8.0,
    )
    algebraic = feeder_restart(emf, impedance, motors, 50.0)
    assert report["started"] == ["m1", "m2"]
    assert algebraic["started"] == ["m1", "m2"]
    assert report["connected_at_s"]["m2"] > 0.05
    early = int(round(0.05 / 0.004))
    assert report["trace"][early, 1] == pytest.approx(1.0)
    assert report["slips"]["m1"] == pytest.approx(algebraic["slips"]["m1"], abs=1e-4)
    assert report["slips"]["m2"] == pytest.approx(algebraic["slips"]["m2"], abs=1e-4)
    assert math.isinf(report["connected_at_s"]["m3"])


def test_inverter_fault_current_obeys_kcl_and_the_unlimited_model_does_not():
    report = qualify_fault_current()
    assert report["verdict"] == "pass", report
    assert report["current_error"] < 1e-4
    assert report["voltage_error"] < 1e-4


def test_reactive_droop_matches_the_network_and_lowers_the_vars():
    report = qualify_droop()
    assert report["verdict"] == "pass", report
    assert report["reactive_error"] < 1e-4


def test_anti_island_window_uses_the_measured_power():
    report = qualify_anti_island()
    assert report["verdict"] == "pass", report
    assert report["power_error"] < 1e-4
    assert report["heavy_hz"] < 49.0 < report["light_hz"]


def test_underfrequency_shedding_keeps_the_critical_load_and_its_power():
    report = qualify_shedding()
    assert report["verdict"] == "pass", report
    assert report["shed"] == ["aux", "heat"]
    assert report["power_error"] < 1e-4


def test_directional_earth_matches_the_sequence_current():
    report = qualify_earth()
    assert report["verdict"] == "pass", report
    assert report["residual_error"] < 1e-5
    assert report["forward_reason"] == "forward"
    assert report["reverse_reason"] == "reverse"


def test_repeated_gross_error_removal_returns_the_state_and_stops_when_singular():
    cleaned = reject_gross_errors(np.array([5.0, 5.0, 40.0, -15.0]), np.ones((4, 1)), np.ones(4))
    assert cleaned["observable"] is True
    assert cleaned["removed"] == [2, 3]
    assert cleaned["estimate"][0] == pytest.approx(5.0)
    healthy = reject_gross_errors(np.array([5.0, 5.1, 4.9]), np.ones((3, 1)), np.ones(3))
    assert healthy["removed"] == []
    singular = reject_gross_errors(np.array([0.0, 10.0]), np.array([[1.0, 0.0], [1.0, 0.0]]), np.ones(2))
    assert singular["observable"] is False
    assert singular["estimate"] is None


def test_power_step_does_not_jump_the_angle_and_settles_at_the_new_equilibrium():
    omega = 2.0 * math.pi * 50.0

    def pm_of(time_s: float) -> float:
        return 0.4 if time_s < 1.0 else 0.7

    run = simulate_smib(
        pm=0.4,
        e_pu=1.2,
        v_pu=1.0,
        x_pu=0.4,
        inertia_s=3.0,
        damping=20.0,
        dt_s=0.02,
        t_end_s=25.0,
        pm_profile=pm_of,
        event_times=[1.0],
    )
    old_delta, _old_omega = equilibrium(0.4, 1.2, 1.0, 0.4, omega)
    new_delta, new_omega = equilibrium(0.7, 1.2, 1.0, 0.4, omega)
    assert abs(new_delta - old_delta) > 0.05
    index = int(np.argmin(np.abs(run["t"] - 1.0)))
    assert abs(run["delta"][index] - run["delta"][index - 1]) < 0.05
    assert run["delta"][-1] == pytest.approx(new_delta, abs=1e-3)
    assert run["omega"][-1] == pytest.approx(new_omega, rel=1e-4)


def test_voltage_reactive_and_active_limit_mutations_fail_the_audit():
    report = qualify_mutations()
    assert report["verdict"] == "pass", report
    assert report["global_optimum_claimed"] is False
    assert report["phase_power_error"] < 1e-4
