"""Параллельное отключение, огибающая тока КЗ и частота малых качаний."""

import math

import numpy as np
import pytest

from power_grid_env.analysis import reject_gross_error
from power_grid_env.contingency import qualify_parallel_outage
from power_grid_env.dynamics import equilibrium, natural_frequency, simulate_smib
from power_grid_env.fault_envelope import closed_form, step_ratio, thermal_rms, trapezoid


def test_parallel_outage_follows_the_divider_and_flags_the_remaining_line():
    report = qualify_parallel_outage()
    assert report["verdict"] == "pass", report
    assert report["voltage_error_pu"] < 1e-5
    assert report["current_error"] < 1e-5
    assert report["post_to_pre"] != pytest.approx(2.0, rel=0.02)


def test_fault_envelope_matches_the_closed_form_and_halves_in_order_two():
    exact = closed_form(0.02, 5773.5, 0.35, 0.8, 50.0)
    numeric = trapezoid(0.02, 1.0e-5, 5773.5, 0.35, 0.8, 50.0)
    assert abs(numeric - exact) / max(1.0, abs(exact)) < 1e-4
    assert closed_form(0.0, 5773.5, 0.35, 0.8, 50.0) == pytest.approx(0.0, abs=1e-9)
    assert step_ratio() == pytest.approx(4.0, rel=0.25)


def test_thermal_rms_matches_the_integral_of_the_envelope():
    samples = np.linspace(0.0, 0.1, 20_001)
    values = np.array([closed_form(float(stamp), 5773.5, 0.35, 0.8, 50.0) for stamp in samples])
    quadrature = math.sqrt(float(np.trapezoid(values * values, samples)) / 0.1)
    assert thermal_rms(0.1, 5773.5, 0.35, 0.8, 50.0) == pytest.approx(quadrature, rel=1e-6)
    steady = 5773.5 / math.hypot(0.35, 0.8)
    assert thermal_rms(0.02, 5773.5, 0.35, 0.8, 50.0) > steady


def test_small_swing_period_matches_the_linearised_frequency():
    pm, emf, voltage, reactance, inertia = 0.5, 1.1, 1.0, 0.3, 4.0
    omega_s = 2.0 * math.pi * 50.0
    delta_eq, omega_eq = equilibrium(pm, emf, voltage, reactance, omega_s)
    expected = 2.0 * math.pi / natural_frequency(delta_eq, emf, voltage, reactance, inertia, omega_s)
    trace = simulate_smib(
        pm=pm,
        e_pu=emf,
        v_pu=voltage,
        x_pu=reactance,
        inertia_s=inertia,
        damping=0.0,
        delta0=delta_eq + 0.02,
        omega0=omega_eq,
        dt_s=0.001,
        t_end_s=2.0,
    )
    deviation = trace["delta"] - delta_eq
    crossings = []
    for index in range(1, len(deviation)):
        if deviation[index - 1] <= 0.0 < deviation[index]:
            span = deviation[index] - deviation[index - 1]
            fraction = -deviation[index - 1] / span
            crossings.append(float(trace["t"][index - 1]) + fraction * 0.001)
    assert len(crossings) >= 2
    period = crossings[1] - crossings[0]
    assert period == pytest.approx(expected, rel=0.01)


def test_a_second_gross_error_remains_after_one_removal():
    measurements = np.array([1.0, 1.0, 5.0, 9.0])
    matrix = np.ones((4, 1))
    variances = np.full(4, 0.01)
    once = reject_gross_error(measurements, matrix, variances)
    assert once["removed"] is not None
    keep = [index for index in range(4) if index != once["removed"]]
    again = reject_gross_error(measurements[keep], matrix[keep], variances[keep])
    assert again["removed"] is not None
