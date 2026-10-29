"""Закрытие объявленных ворот: машина в КЗ, карта луча, PSS1A, паз, привод, корпус."""

import math

import numpy as np
import pytest

from power_grid_env.analysis import dc_opf, seeded_bad_data
from power_grid_env.conductor import slot_field_analytic, slot_field_grid
from power_grid_env.coordination import qualify_feeder_map
from power_grid_env.dynamics import drive_current, drive_torque, pss1a_step, release_status
from power_grid_env.further import qualify_machine_follow
from power_grid_env.harmonic import fourier_fundamental


def test_machine_current_decays_inside_the_fault():
    report = qualify_machine_follow()
    assert report["verdict"] == "pass", report
    assert report["voltage_error"] < 1e-4
    assert report["early_a"] > report["late_a"]


def test_declared_feeder_map_covers_location_source_load_and_settings():
    report = qualify_feeder_map()
    assert report["verdict"] == "pass", report
    assert report["current_error"] < 1e-4
    assert report["cells"] == 12
    assert report["plant_wide_map"] is False


def test_pss1a_washout_matches_the_exponential_and_the_ceiling_holds():
    assert pss1a_step(0.2, gain=1.0, washout_s=1.0, dt_s=0.001) == pytest.approx(math.exp(-0.2), rel=1e-3)
    assert pss1a_step(0.05, gain=50.0, washout_s=1.0, ceiling=1.5, dt_s=0.001) == pytest.approx(1.5)
    status = release_status()
    assert status["andes_integrated"] is False
    assert status["andes_status"] in {"installed", "not_installed"}
    assert status["industrial_rms_qualified"] is False


def test_slot_grid_approaches_the_analytic_field():
    depth = 2.0
    fraction = 0.5
    expected = slot_field_analytic(depth, fraction)
    coarse = slot_field_grid(depth, 16)[8]
    fine = slot_field_grid(depth, 32)[16]
    coarse_error = abs(coarse - expected)
    fine_error = abs(fine - expected)
    assert fine_error < coarse_error
    assert fine_error / abs(expected) < 0.02


def test_drive_current_follows_the_first_order_law_and_torque_is_clipped():
    assert drive_current(10.0, 0.0, 0.2, 20.0) == 0.0
    assert drive_current(10.0, 1.0e6, 0.2, 20.0) == pytest.approx(10.0, abs=1e-6)
    assert drive_current(10.0, 0.2, 0.2, 20.0) == pytest.approx(10.0 * (1.0 - math.exp(-1.0)))
    assert drive_torque(30.0, 2.0, 12.0) == pytest.approx(24.0)
    assert drive_torque(4.0, 2.0, 12.0) == pytest.approx(8.0)


def test_full_cycle_fourier_rejects_a_short_window_and_ignores_offset():
    frequency = 50.0
    step = 1.0e-4
    count = int(round(1.0 / (frequency * step)))
    time = np.arange(count) * step
    amplitude = 10.0
    wave = amplitude * np.sin(2.0 * math.pi * frequency * time)
    ready = fourier_fundamental(wave, step, frequency)
    assert ready is not None
    assert abs(ready) == pytest.approx(amplitude / math.sqrt(2.0), rel=1e-6)
    assert fourier_fundamental(wave[: count // 2], step, frequency) is None
    offset = fourier_fundamental(wave + 4.0, step, frequency)
    assert offset is not None
    assert abs(offset) == pytest.approx(abs(ready), rel=1e-6)


def test_seeded_bad_data_finds_every_injected_error_and_dc_optimum_is_global():
    report = seeded_bad_data(24, seed=7)
    assert report["missed"] == 0
    assert report["false_removals"] == 0
    assert report["enterprise_corpus"] is False
    solved = dc_opf(np.array([10.0, 30.0]), np.array([0.0, 0.0]), np.array([1.0, 1.0]), 0.8)
    assert solved["success"] is True
    assert solved["global_optimum_claimed"] is True
    assert solved["convex"] is True
