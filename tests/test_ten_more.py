"""Нейтраль, поверхностный эффект, спектр, ROCOF, приоритет и концы ветви."""

import math
from dataclasses import replace

import numpy as np
import pytest

from power_grid_env.analysis import weighted_residual_sum
from power_grid_env.conductor import skin_factor_bessel, skin_factor_kelvin
from power_grid_env.der import apparent_limit
from power_grid_env.dynamics import equilibrium, simulate_smib
from power_grid_env.follow_on import qualify_priority, qualify_volt_watt
from power_grid_env.harmonic import six_step_orders, six_step_thd, thd_fundamental
from power_grid_env.network_opf import (
    Branch,
    Bus,
    GeneratorSpec,
    OpfCase,
    _branch_mva,
    _injections,
    _ybus,
    audit_feasibility,
)
from power_grid_env.protection import rocof_trip
from power_grid_env.sequence_fault import qualify_neutral_reactor


def test_a_neutral_reactor_adds_three_times_its_impedance_to_the_slg_current():
    report = qualify_neutral_reactor()
    assert report["verdict"] == "pass", report
    assert report["current_error"] < 1e-4
    assert report["reactor_a"] < report["solid_a"] * 0.8


def test_round_wire_skin_effect_agrees_in_two_special_functions():
    low_r, low_x = skin_factor_kelvin(0.2)
    assert low_r == pytest.approx(1.0, abs=1e-4)
    assert low_x < 0.2
    for argument in (3.0, 8.0):
        assert skin_factor_kelvin(argument)[0] == pytest.approx(skin_factor_bessel(argument), rel=1e-8)
    high_r, high_x = skin_factor_kelvin(20.0)
    assert high_r == pytest.approx(20.0 / (2.0 * math.sqrt(2.0)), rel=0.05)
    assert high_r == pytest.approx(high_x, rel=0.05)
    with pytest.raises(ValueError):
        skin_factor_kelvin(0.0)


def test_six_step_spectrum_reaches_the_closed_form_thd():
    partial = thd_fundamental(six_step_orders(20001))
    assert partial == pytest.approx(six_step_thd(), abs=1e-4)
    assert six_step_thd() == pytest.approx(math.sqrt(math.pi**2 / 9.0 - 1.0))
    polluted = six_step_orders(13) + [(3, 0.2)]
    assert thd_fundamental(polluted) != pytest.approx(thd_fundamental(six_step_orders(13)))


def test_rocof_trips_only_on_a_fast_frequency_fall():
    falling = [(0.0, 50.0), (0.05, 49.9), (0.1, 49.8)]
    slow = [(0.0, 50.0), (0.1, 49.95)]
    rising = [(0.0, 50.0), (0.1, 50.2)]
    assert rocof_trip(falling, 1.0)["trip"] is True
    assert rocof_trip(slow, 1.0)["trip"] is False
    assert rocof_trip(rising, 1.0)["reason"] == "inside"
    assert rocof_trip(falling, 1.0, available=False)["reason"] == "measurement_unavailable"
    with pytest.raises(ValueError):
        rocof_trip([(0.1, 50.0), (0.0, 49.0)], 1.0)


def test_priority_clips_the_circle_and_the_network_takes_the_remaining_power():
    report = qualify_priority()
    assert report["verdict"] == "pass", report
    assert report["power_error"] < 1e-4
    assert apparent_limit(30_000.0, 40_000.0, 100_000.0, "Q") == (30_000.0, 40_000.0)


def test_opening_the_machine_terminal_raises_the_speed_by_the_inertial_rate():
    omega = 2.0 * math.pi * 50.0
    inertia = 4.0
    mechanical = 0.5

    def voltage(time_s: float) -> float:
        return 1.0 if time_s < 0.2 else 0.0

    run = simulate_smib(
        pm=mechanical,
        e_pu=1.1,
        v_pu=1.0,
        x_pu=0.4,
        inertia_s=inertia,
        dt_s=0.001,
        t_end_s=0.5,
        v_profile=voltage,
        event_times=[0.2],
    )
    held = simulate_smib(pm=mechanical, e_pu=1.1, v_pu=1.0, x_pu=0.4, inertia_s=inertia, dt_s=0.001, t_end_s=0.5)
    opened = int(np.argmin(np.abs(run["t"] - 0.2)))
    rate = omega / (2.0 * inertia) * mechanical
    assert abs(run["delta"][opened] - run["delta"][opened - 1]) < 1e-3
    assert run["omega"][-1] - run["omega"][opened] == pytest.approx(rate * 0.3, rel=1e-4)
    assert held["omega"][-1] == pytest.approx(omega, rel=1e-6)
    equilibrium(mechanical, 1.1, 1.0, 0.4, omega)


def test_an_overload_on_the_receiving_end_is_visible_while_the_sending_end_is_inside():
    branch = Branch(1, 2, 0.2, 0.02, 0.0, 10.0, 0.0, 0.0, 1, -30.0, 30.0)
    case = OpfCase(
        name="ends",
        base_mva=1.0,
        buses=[
            Bus(1, 3, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.5, 1.2),
            Bus(2, 1, 0.0, 0.0, 0.0, 0.0, 0.9, 0.0, 0.5, 1.2),
        ],
        generators=[GeneratorSpec(1, -20.0, 20.0, 0.0, 20.0, (0.0, 1.0, 0.0))],
        branches=[branch],
    )
    index = {1: 0, 2: 1}
    vm = np.array([1.0, 0.9])
    theta = np.zeros(2)
    active, reactive = _injections(vm, theta, _ybus(case, index))
    generation_p = np.array([float(active[0])])
    generation_q = np.array([float(reactive[0])])
    load = replace(case.buses[1], pd=float(-active[1]), qd=float(-reactive[1]))
    flows = _branch_mva(vm, theta, branch, index, 1.0)
    low, high = sorted(flows)
    assert high > low * 1.02
    rated = replace(branch, rate_a=0.5 * (low + high))
    tight = replace(case, buses=[case.buses[0], load], branches=[rated])
    rejected = audit_feasibility(tight, vm, theta, generation_p, generation_q)
    assert rejected["max_mismatch_pu"] < 1e-8
    assert rejected["flows_within_rating"] is False
    wide = replace(tight, branches=[replace(rated, rate_a=high * 1.1)])
    assert audit_feasibility(wide, vm, theta, generation_p, generation_q)["flows_within_rating"] is True


def test_volt_watt_scales_the_requested_power_once():
    report = qualify_volt_watt()
    assert report["verdict"] == "pass", report
    assert report["power_error"] < 1e-3
    assert report["expected_w"] == pytest.approx(20_000.0)


def test_a_locked_switch_refuses_to_move():
    from power_grid_env.build import NetBuilder
    from power_grid_env.scenarios import Scenario
    from power_grid_env.simulation import Engine

    builder = NetBuilder("lock", "Блокировка", 10.0)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.05, 0.2)
    builder.switch("src", "ld", switch_id="sw", closed=True, operate_time_s=0.0)
    builder.load("ld", 50_000.0, 0.0, load_id="load")
    engine = Engine(builder.build(), Scenario(id="lock", horizon_s=10, settle_s=100, control_interval_s=10))
    engine.reset()
    engine.net.switches[0].locked = True
    refused = engine.submit({"kind": "switch", "equipment_id": "sw", "command": "open"})
    engine.advance_to(0.01)
    assert refused["accepted"] is False
    assert refused["reason"] == "locked"
    assert engine.net.switches[0].closed is True
    engine.net.switches[0].locked = False
    accepted = engine.submit({"kind": "switch", "equipment_id": "sw", "command": "open"})
    engine.advance_to(0.02)
    assert accepted["accepted"] is True
    assert engine.net.switches[0].closed is False
    engine.net.switches[0].locked = True
    still = engine.submit({"kind": "switch", "equipment_id": "sw", "command": "close"})
    engine.advance_to(0.03)
    assert still["reason"] == "locked"
    assert engine.net.switches[0].closed is False


def test_exact_measurements_have_a_zero_weighted_residual():
    matrix = np.array([[1.0], [1.0]])
    exact = weighted_residual_sum(np.array([4.0, 4.0]), matrix, np.ones(2))
    biased = weighted_residual_sum(np.array([0.0, 2.0]), matrix, np.ones(2))
    assert exact == pytest.approx(0.0, abs=1e-12)
    assert biased == pytest.approx((0.0 - 2.0) ** 2 / 2.0)
    assert weighted_residual_sum(np.array([0.0, 1.0]), np.array([[1.0, 0.0], [1.0, 0.0]]), np.ones(2)) is None
