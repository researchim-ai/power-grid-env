"""Регуляторы ANDES и структурные схемы турбины и PSS."""

import numpy as np
import pytest
from scipy import signal

from power_grid_env.andes_rms import qualify_regulated_plant
from power_grid_env.dynamics import pss1a_step, tgov1_power


def test_kundur_machines_keep_the_rotor_law_through_events():
    report = qualify_regulated_plant()
    assert report["verdict"] == "pass", report
    assert report["machines"] == 4
    assert report["init_residual"] < 1e-6
    assert report["governor_error"] < 1e-6
    assert report["exciter_move"] > 1e-3
    assert report["area_speed_gap"] > 1e-3
    assert report["voltage_ratio"] < 0.8
    assert report["pss_error"] < 1e-6
    assert report["industrial_rms_qualified"] is False
    assert report["declared_model"] == "kundur_four_machine"


def test_tgov1_step_matches_the_transfer_function():
    droop, valve, lead, lag = 0.05, 0.5, 0.2, 2.0
    times = np.linspace(0.0, 0.5, 501)
    deviation = np.zeros_like(times)
    deviation[1:] = 0.01
    replay = tgov1_power(1.0 + deviation, times[1] - times[0], droop, valve, lead, lag, 0.0)
    numerator = np.array([-(lead / droop), -(1.0 / droop)])
    denominator = np.polymul(np.array([valve, 1.0]), np.array([lag, 1.0]))
    _time, reference, _state = signal.lsim((numerator, denominator), deviation, times)
    assert np.max(np.abs(replay - reference)) < 1e-4


def test_pss1a_with_unequal_blocks_matches_the_transfer_function():
    washout, lead, lag, lead2, lag2 = 1.0, 0.3, 0.8, 0.4, 1.2
    times = np.linspace(0.0, 0.3, 601)
    command = np.ones_like(times)
    command[0] = 0.0
    numerator = np.array([washout, 0.0])
    denominator = np.array([washout, 1.0])
    for zero, pole in ((lead, lag), (lead2, lag2)):
        numerator = np.polymul(numerator, np.array([zero, 1.0]))
        denominator = np.polymul(denominator, np.array([pole, 1.0]))
    _time, reference, _state = signal.lsim((numerator, denominator), command, times)
    simulated = pss1a_step(0.3, gain=1.0, washout_s=washout, lead_s=lead, lag_s=lag, lead2_s=lead2, lag2_s=lag2, dt_s=0.0005)
    assert simulated == pytest.approx(float(reference[-1]), abs=1e-4)
