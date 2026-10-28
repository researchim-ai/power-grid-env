"""Двигатель на фазной сети, удаление грубой ошибки и междуфазный ток IEC."""

import math

import numpy as np
import pytest

from power_grid_env.analysis import reject_gross_error
from power_grid_env.iec60909 import initial_symmetrical_line_to_line, initial_symmetrical_three_phase
from power_grid_env.motor_network import qualify_motor_on_feeder


def test_motor_impedance_matches_the_phase_solver():
    report = qualify_motor_on_feeder()
    assert report["verdict"] == "pass", report
    assert report["voltage_error_pu"] < 1e-5
    assert report["pair_voltage_error_pu"] < 1e-5


def test_one_gross_error_is_removed_and_the_estimate_returns():
    truth = 1.0
    measurements = np.array([1.0, 1.01, 0.99, 6.0])
    matrix = np.ones((4, 1))
    variances = np.full(4, 0.01)
    cleaned = reject_gross_error(measurements, matrix, variances)
    assert cleaned["removed"] == 3
    assert cleaned["observable"] is True
    assert cleaned["estimate"][0] == pytest.approx(truth, abs=0.02)
    consistent = reject_gross_error(np.array([1.0, 1.01, 0.99]), np.ones((3, 1)), np.full(3, 0.25))
    assert consistent["removed"] is None


def test_line_to_line_iec_current_is_root_three_over_two_of_the_three_phase():
    impedance = 0.2 + 0.5j
    factor = 1.1
    nominal = 10_000.0
    three = initial_symmetrical_three_phase(nominal, factor, impedance)
    pair = initial_symmetrical_line_to_line(nominal, factor, impedance, impedance)
    assert abs(pair) / abs(three) == pytest.approx(math.sqrt(3.0) / 2.0)
