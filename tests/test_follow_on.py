"""Вклад ветвей, отключающая способность, холодный пуск, оценка и скачок линии."""

import math

import numpy as np
import pytest

from power_grid_env.analysis import estimate_covariance
from power_grid_env.dynamics import equilibrium, simulate_smib
from power_grid_env.fault_envelope import waveform_kappa
from power_grid_env.follow_on import (
    qualify_branch_split,
    qualify_cold_load,
    qualify_phasor_waveform,
    qualify_source_outage,
    qualify_undervoltage,
    qualify_volt_var,
)
from power_grid_env.harmonic import thd_fundamental, thd_rms
from power_grid_env.iec60909 import peak_current, peak_factor
from power_grid_env.protection import breaker_can_interrupt


def test_two_lines_split_the_fault_current_by_their_impedances():
    report = qualify_branch_split()
    assert report["verdict"] == "pass", report
    assert report["current_error"] < 1e-5
    assert abs(report["left_a"] - report["right_a"]) / report["right_a"] > 0.05


def test_breaker_duty_uses_the_peak_and_refuses_a_missing_rating():
    symmetrical = 1000.0
    kappa = peak_factor(0.1)
    iec_peak = peak_current(symmetrical, kappa)
    waveform_peak = waveform_kappa(0.1, 1.0) * math.sqrt(2.0) * symmetrical
    assert waveform_peak == pytest.approx(iec_peak, rel=0.01)
    assert breaker_can_interrupt(iec_peak, iec_peak * 1.05)["can_interrupt"] is True
    assert breaker_can_interrupt(waveform_peak, iec_peak * 0.95)["can_interrupt"] is False
    assert breaker_can_interrupt(iec_peak, iec_peak, available=False)["reason"] == "rating_unavailable"


def test_cold_load_follows_the_exponential_and_the_source_supplies_it():
    report = qualify_cold_load()
    assert report["verdict"] == "pass", report
    assert report["power_error"] < 1e-4
    assert report["at_tau"] == pytest.approx(1.0 + 1.5 / math.e)


def test_volt_var_sets_the_reactive_power_from_the_measured_voltage():
    report = qualify_volt_var()
    assert report["verdict"] == "pass", report
    assert report["reactive_error"] < 1e-3
    assert report["expected_var"] < 0.0


def test_estimate_covariance_shrinks_when_a_second_measurement_is_added():
    single = estimate_covariance(np.array([[1.0]]), np.array([4.0]))
    paired = estimate_covariance(np.ones((2, 1)), np.array([4.0, 4.0]))
    uneven = estimate_covariance(np.ones((2, 1)), np.array([4.0, 16.0]))
    assert single is not None and paired is not None and uneven is not None
    assert single[0, 0] == pytest.approx(4.0)
    assert paired[0, 0] == pytest.approx(2.0)
    assert uneven[0, 0] == pytest.approx(1.0 / (1.0 / 4.0 + 1.0 / 16.0))
    assert estimate_covariance(np.array([[1.0, 0.0], [1.0, 0.0]]), np.ones(2)) is None


def test_removing_the_second_source_returns_the_single_source_current():
    report = qualify_source_outage()
    assert report["verdict"] == "pass", report
    assert report["current_error"] < 1e-4
    assert abs(report["with_extra_a"] - report["alone_a"]) / report["alone_a"] > 0.02


def test_phasor_current_matches_the_waveform_rms_and_the_cycle_energy():
    report = qualify_phasor_waveform()
    assert report["verdict"] == "pass", report
    assert report["rms_error"] < 1e-4
    assert report["network_error"] < 1e-4


def test_a_line_reactance_step_keeps_the_angle_continuous():
    omega = 2.0 * math.pi * 50.0

    def reactance(time_s: float) -> float:
        return 0.3 if time_s < 1.0 else 0.55

    run = simulate_smib(
        pm=0.4,
        e_pu=1.2,
        v_pu=1.0,
        x_pu=0.3,
        inertia_s=3.0,
        damping=20.0,
        dt_s=0.02,
        t_end_s=25.0,
        x_profile=reactance,
        event_times=[1.0],
    )
    old_angle, _speed = equilibrium(0.4, 1.2, 1.0, 0.3, omega)
    new_angle, new_speed = equilibrium(0.4, 1.2, 1.0, 0.55, omega)
    assert abs(new_angle - old_angle) > 0.05
    index = int(np.argmin(np.abs(run["t"] - 1.0)))
    assert abs(run["delta"][index] - run["delta"][index - 1]) < 0.05
    assert run["delta"][-1] == pytest.approx(new_angle, abs=1e-3)
    assert run["omega"][-1] == pytest.approx(new_speed, rel=1e-4)


def test_thd_changes_with_the_denominator_and_needs_the_fundamental():
    spectrum = [(1, 10.0), (5, 1.0), (7, 0.5)]
    fundamental = thd_fundamental(spectrum)
    rms = thd_rms(spectrum)
    assert fundamental == pytest.approx(math.sqrt(1.25) / 10.0)
    assert rms == pytest.approx(math.sqrt(1.25) / math.sqrt(101.25))
    assert fundamental != pytest.approx(rms)
    assert thd_fundamental([(1, 4.0)]) == 0.0
    with pytest.raises(ValueError):
        thd_fundamental([(5, 1.0)])


def test_undervoltage_waits_out_the_breaker_delay():
    report = qualify_undervoltage()
    assert report["verdict"] == "pass", report
    assert report["voltage_pu"] == pytest.approx(0.8, abs=1e-4)
