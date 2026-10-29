"""Коэффициент c, перенапряжение, гонка защит, lead-lag, остров и отпайка."""

import math

import numpy as np
import pytest

from power_grid_env.batch_ten import nearest_tap, qualify_island, qualify_line_loss, qualify_overvoltage, qualify_tap, qualify_voltage_factor
from power_grid_env.conventions import sequence_impedances
from power_grid_env.dynamics import lead_lag_step, limited_power_balance, simulate_governor_limit, simulate_lead_lag
from power_grid_env.protection import first_to_clear
from power_grid_env.reference import balanced_line_matrix


def test_voltage_factor_scales_the_current_and_the_network_stays_at_c_one():
    report = qualify_voltage_factor()
    assert report["verdict"] == "pass", report
    assert report["current_error"] < 1e-4
    assert report["c_max"] == 1.1
    assert report["c_min"] == 1.0


def test_overvoltage_waits_for_the_breaker_and_ignores_a_normal_voltage():
    report = qualify_overvoltage()
    assert report["verdict"] == "pass", report
    assert report["high_pu"] == pytest.approx(1.08, abs=1e-4)


def test_the_fuse_and_the_relay_race_on_the_same_ampere_squared_seconds():
    assert first_to_clear(100.0, 0.2, 3000.0) == "relay"
    assert first_to_clear(100.0, 0.2, 1000.0) == "fuse"
    assert first_to_clear(100.0, 0.2, 2000.0) == "same"
    assert first_to_clear(100.0, math.inf, 2000.0) == "fuse"


def test_lead_lag_matches_the_step_response_and_halves_with_the_step():
    lead, lag, command, horizon = 0.05, 0.2, 1.0, 0.08
    assert lead_lag_step(command, 0.0, lead, lag) == pytest.approx(lead / lag)
    assert lead_lag_step(command, 1.0e6, lead, lag) == pytest.approx(command, abs=1e-6)

    def state_at(dt_s: float) -> float:
        return float(simulate_lead_lag(command=command, lead_s=lead, lag_s=lag, dt_s=dt_s, t_end_s=horizon)[-1])

    exact = command * (1.0 - math.exp(-horizon / lag))
    fine = state_at(0.0005)
    coarse = abs(state_at(0.004) - exact)
    halfway = abs(state_at(0.002) - exact)
    assert coarse / halfway == pytest.approx(4.0, rel=0.25)
    assert fine == pytest.approx(exact, rel=1e-4)
    assert lead_lag_step(0.0, 1.0, lead, lag) == 0.0
    with pytest.raises(ValueError):
        lead_lag_step(1.0, 0.1, 0.1, 0.0)


def test_opening_the_utility_leaves_a_forming_island_and_drops_a_following_one():
    report = qualify_island()
    assert report["verdict"] == "pass", report
    assert report["forming_error"] < 1e-5
    assert report["grid_error"] < 1e-5
    assert report["dark_pu"] < 0.01


def test_the_nearest_tap_is_discrete_and_the_network_follows_it():
    report = qualify_tap()
    assert report["verdict"] == "pass", report
    assert report["voltage_error"] < 1e-4
    assert report["chosen_tap"] == 1.0
    assert report["global_optimum_claimed"] is False
    assert nearest_tap(1.0125, [0.975, 1.0, 1.025]) == 1.0


def test_governor_limit_caps_the_mechanical_power_and_keeps_synchronous_speed():
    balance = limited_power_balance(0.9, 0.4, 1.1, 1.0, 0.4, 2.0 * math.pi * 50.0)
    assert balance["limited"] is True
    assert balance["mechanical"] == 0.4
    assert balance["delta_rad"] == pytest.approx(math.asin(0.4 * 0.4 / 1.1))
    assert balance["omega_rad_s"] == pytest.approx(2.0 * math.pi * 50.0)
    run = simulate_governor_limit(
        pref=0.9, pmax=0.4, e_pu=1.1, v_pu=1.0, x_pu=0.4, inertia_s=4.0, dt_s=0.005, t_end_s=0.4, speed_offset=40.0
    )
    assert float(np.max(run["mechanical"])) <= 0.4 + 1e-9
    assert float(np.min(run["mechanical"])) < 0.4
    free = limited_power_balance(0.2, 0.4, 1.1, 1.0, 0.4, 2.0 * math.pi * 50.0)
    assert free["limited"] is False
    assert free["mechanical"] == 0.2
    with pytest.raises(ValueError):
        limited_power_balance(2.0, 2.0, 1.1, 1.0, 0.8, 1.0)


def test_line_loss_equals_three_i_squared_r():
    report = qualify_line_loss()
    assert report["verdict"] == "pass", report
    assert report["loss_error"] < 1e-4


def test_a_ct_compares_the_secondary_and_does_not_trip_without_a_measurement():
    from power_grid_env.protection import instrument_secondary, trip_from_secondary

    secondary = instrument_secondary(1500.0, 2000.0, 5.0)
    assert secondary == pytest.approx(3.75)
    assert trip_from_secondary(1500.0, 2000.0, 5.0, 2.5, available=True)["trip"] is True
    assert trip_from_secondary(1500.0, 2000.0, 5.0, 2.5, available=False)["trip"] is False
    quiet = trip_from_secondary(400.0, 2000.0, 5.0, 2.5, available=True)
    assert quiet["trip"] is False
    assert 400.0 > 2.5


def test_circulant_line_matrix_returns_the_requested_sequence_impedances():
    positive = 0.3 + 0.8j
    mutual = 0.05 + 0.1j
    resistance, reactance = balanced_line_matrix(positive, mutual, 3)
    zero, direct, negative = sequence_impedances(resistance + 1j * reactance)
    assert direct == pytest.approx(positive)
    assert negative == pytest.approx(positive)
    assert zero == pytest.approx(positive + 3.0 * mutual)
    skewed = np.array([[1.0, 0.1, 0.2], [0.1, 1.0, 0.1], [0.2, 0.1, 1.0]], dtype=complex)
    with pytest.raises(ValueError):
        sequence_impedances(skewed)
