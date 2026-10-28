"""Горизонт, IEC 60909 subset, объявленный спектр и карта селективности."""

import math

import pytest

from power_grid_env.build import NetBuilder
from power_grid_env.controls import integrate_temperature, temperature_constant_r
from power_grid_env.conventions import ll_to_ln
from power_grid_env.harmonic import forced_current, nodal_current, resonance_moves_with_capacitance
from power_grid_env.horizon import replay_matches, run_horizon
from power_grid_env.iec60909 import (
    initial_symmetrical_single_line_to_ground,
    initial_symmetrical_three_phase,
    peak_factor,
    voltage_factor,
)
from power_grid_env.model import HarmonicComponent
from power_grid_env.protection import clearing_times
from power_grid_env.reference import thevenin_fault_current
from power_grid_env.scope import compile_scope


def test_seven_and_thirty_day_horizons_keep_the_energy_integral():
    week = replay_matches(7)
    month = run_horizon(30)
    assert week["verdict"] == "pass", week
    assert month["verdict"] == "pass", month
    assert month["not_a_feeder_study"] is True
    assert month["trace_len"] == 2
    assert month["dropped_trace_steps"] == 28
    assert month["critical_demand_wh"] == pytest.approx(100_000.0 * 30 * 24)


def test_thermal_step_error_halves_when_the_step_halves():
    analytic = temperature_constant_r(60.0, 20.0, 15.0, 100.0, 0.2, 0.05, 2000.0)
    coarse = abs(integrate_temperature(60.0, 0.2, 20.0, 15.0, 100.0, 0.2, 0.0, 20.0, 0.05, 2000.0) - analytic)
    fine = abs(integrate_temperature(60.0, 0.1, 20.0, 15.0, 100.0, 0.2, 0.0, 20.0, 0.05, 2000.0) - analytic)
    assert fine < 0.6 * coarse


def test_iec_factor_changes_the_current_and_is_not_the_faulted_network():
    impedance = 0.05 + 0.4j
    nominal = 10_000.0
    factor = voltage_factor(nominal, maximum=True)
    iec = initial_symmetrical_three_phase(nominal, factor, impedance)
    faulted = thevenin_fault_current(ll_to_ln(nominal), impedance, impedance, impedance, 0.0, "3ph")
    assert factor == 1.1
    assert voltage_factor(400.0, maximum=True) == 1.05
    assert abs(iec) == pytest.approx(abs(faulted) * factor)
    assert peak_factor(0.0) == pytest.approx(2.0)
    assert peak_factor(100.0) == pytest.approx(1.02, abs=1e-6)
    zero = 0.2 + 1.2j
    single = initial_symmetrical_single_line_to_ground(nominal, factor, impedance, impedance, zero)
    assert single == pytest.approx(3.0 * factor * nominal / (math.sqrt(3.0) * (impedance + impedance + zero)))
    with pytest.raises(ValueError, match="R/X"):
        peak_factor(-0.1)


def test_clearing_map_orders_the_downstream_relay_and_is_silent_below_pickup():
    stages = [("near", 1.0, "definite", 0.2), ("far", 1.0, "definite", 0.5)]
    order = clearing_times(4.0, stages)
    assert order[0][0] == "near"
    assert order[0][1] < order[1][1]
    silent = clearing_times(0.2, stages)
    assert all(math.isinf(item[1]) for item in silent)


def test_declared_spectrum_unblocks_harmonic_and_matches_the_nodal_rlc():
    voltage = 100.0 + 0.0j
    formula = forced_current(voltage, 2.0, 10.0e-3, 100.0e-6, 50.0)
    nodal = nodal_current(voltage, 2.0, 10.0e-3, 100.0e-6, 50.0)
    assert abs(formula - nodal) / abs(formula) < 1e-9
    assert resonance_moves_with_capacitance(10.0e-3, 100.0e-6) == pytest.approx(math.sqrt(2.0))
    builder = NetBuilder("harm", "Спектр", 10.0)
    builder.node("src")
    builder.source("src", 0.1, 0.2)
    network = builder.build()
    assert compile_scope(network, "harmonic")["ok"] is False
    network.harmonics.append(HarmonicComponent(order=5, current_a=2.0, quality="declared"))
    scoped = compile_scope(network, "harmonic")
    assert scoped["ok"] is True
    assert any("объявлен" in item for item in scoped["warnings"])
