"""Зоны дистанции, потолок возбуждения, рамка пуска, реактор и LODF."""

import math

import numpy as np
import pytest

from power_grid_env.analysis import dc_power_flow, line_flow_pu, lodf
from power_grid_env.der import capability_q
from power_grid_env.dynamics import field_command, field_response, soft_start_fraction
from power_grid_env.further import qualify_capability, qualify_lossless, qualify_reactor, qualify_soft_start, qualify_zones
from power_grid_env.protection import distance_zones, reconnect_allowed


def test_zone1_is_faster_and_zone2_sees_the_remote_fault():
    report = qualify_zones()
    assert report["verdict"] == "pass", report
    assert report["impedance_error"] < 1e-4
    assert report["close_zone"] == "zone1"
    assert report["remote_zone"] == "zone2"
    assert report["dynamic_qualified"] is False
    with pytest.raises(ValueError):
        distance_zones(1j, 1j, zone1_ohm=2.0, zone2_ohm=1.0, line_angle_rad=1.0, half_width_rad=0.3, zone1_s=0.1, zone2_s=0.2)


def test_exciter_ceiling_keeps_the_field_below_the_limit():
    assert field_command(2.2, 1.0, 1.0, 1.5) == pytest.approx(1.2)
    capped = field_command(2.2, 0.5, 1.0, 1.5)
    assert capped == 1.5
    limited = field_response(1.0, capped, 1.0, 0.2)
    unlimited = field_response(1.0, 1.7, 1.0, 0.2)
    assert limited == pytest.approx(1.5 + (1.0 - 1.5) * math.exp(-1.0 / 0.2))
    assert limited < 1.5
    assert unlimited > limited
    with pytest.raises(ValueError):
        field_command(1.0, 1.0, 0.0, 1.0)


def test_soft_start_torque_follows_the_square_of_the_voltage_fraction():
    report = qualify_soft_start()
    assert report["verdict"] == "pass", report
    assert report["current_ratio"] == pytest.approx(2.0, rel=1e-4)
    assert soft_start_fraction(0.0, 2.0) == 0.0
    with pytest.raises(ValueError):
        soft_start_fraction(-0.1, 1.0)


def test_a_reactor_absorbs_vars_and_lowers_the_voltage():
    report = qualify_reactor()
    assert report["verdict"] == "pass", report
    assert report["reactive_error"] < 1e-3


def test_reconnect_needs_an_unbroken_healthy_window():
    assert reconnect_allowed([True, True, True, True], 0.1, 0.5) is False
    assert reconnect_allowed([True] * 5, 0.1, 0.5) is True
    assert reconnect_allowed([True, False, True, True, True, True], 0.1, 0.5) is False
    assert reconnect_allowed([False] + [True] * 5, 0.1, 0.5) is True
    with pytest.raises(ValueError):
        reconnect_allowed([True], 0.1, 0.0)


def test_parallel_line_outage_transfers_all_of_its_flow():
    assert lodf(0.5, 0.5) == pytest.approx(1.0)
    reactance = 0.2
    both = np.array([[10.0, -10.0], [-10.0, 10.0]])
    theta = dc_power_flow(both, np.array([1.0, -1.0]), slack=0)
    assert line_flow_pu(theta, 0, 1, reactance) == pytest.approx(0.5)
    single = np.array([[5.0, -5.0], [-5.0, 5.0]])
    after = dc_power_flow(single, np.array([1.0, -1.0]), slack=0)
    assert line_flow_pu(after, 0, 1, reactance) == pytest.approx(0.5 + lodf(0.5, 0.5) * 0.5)
    with pytest.raises(ValueError):
        lodf(1.0, 0.4)


def test_the_field_line_binds_before_the_capability_circle():
    report = qualify_capability()
    assert report["verdict"] == "pass", report
    assert capability_q(60_000.0, 100_000.0, 40_000.0, 0.0) == pytest.approx(40_000.0)
    assert capability_q(60_000.0, 100_000.0, 200_000.0, 0.0) == pytest.approx(80_000.0)
    assert capability_q(150_000.0, 100_000.0, 50_000.0, 0.0) == 0.0


def test_temperature_slope_matches_the_closed_form_at_the_first_instant():
    from power_grid_env.controls import temperature_constant_r

    start = 20.0
    ambient = 15.0
    current = 100.0
    resistance = 0.2
    thermal = 0.05
    capacity = 2000.0
    step = 0.01
    assert temperature_constant_r(0.0, start, ambient, current, resistance, thermal, capacity) == start
    later = temperature_constant_r(step, start, ambient, current, resistance, thermal, capacity)
    rise = current**2 * resistance * thermal
    slope = (ambient + rise - start) / (thermal * capacity)
    assert (later - start) / step == pytest.approx(slope, rel=1e-3)


def test_a_wrapped_angle_stays_inside_half_a_turn():
    from power_grid_env.conventions import align_angle

    assert align_angle(math.pi + 0.2, 0.0) == pytest.approx(-math.pi + 0.2)
    assert align_angle(0.3, 0.1) == pytest.approx(0.2)


def test_lossless_line_power_is_the_sine_of_the_angle():
    report = qualify_lossless()
    assert report["verdict"] == "pass", report
    assert report["power_error"] < 1e-4
