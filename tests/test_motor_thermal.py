"""Thermal history, continuous crossing, latching and independent SI reference."""
from pathlib import Path
import math
import json
import numpy as np
import pytest
from pydantic import ValidationError
from scipy.integrate import solve_ivp
from benchmarks.thermal_motor_reference import reference
from power_grid_env.motor_thermal import MotorThermalProtection, constant_current_level, constant_current_trip_time
from power_grid_env.induction_network import InductionNetwork
from power_grid_env.study_spec import StudySpec, run_study
from test_induction_study import example as unprotected


def example(name='cold_trip'):
    return StudySpec.model_validate_json(Path('cases/studies/thermal_'+name+'.json').read_text())


@pytest.mark.parametrize('current', [0., 1., 3.])
def test_replica_derivative_matches_exact_constant_current_heating_and_cooling(monkeypatch, current):
    s = example(); motor = s.induction.motors[0]
    motor.thermal.initial_level = .7
    model = InductionNetwork(s.network, s.induction)
    # Component boundary test: prescribe RMS current without claiming this is
    # a network operating point. Production derivative must use that same I.
    monkeypatch.setattr(model, 'electrical', lambda x: (np.ones(len(model.bus_names)), np.array([complex(current)]), np.array([0.]), 0.))
    initial = np.r_[np.zeros(3), .7]
    times = np.linspace(0., 2., 21)
    sol = solve_ivp(model.rhs, (0., 2.), initial, method='Radau', rtol=1e-10, atol=1e-12, t_eval=times)
    assert sol.success
    expected = [constant_current_level(.7, current, t, motor.thermal.time_constant_s) for t in times]
    assert sol.y[3] == pytest.approx(expected, abs=1e-9)


def test_analytic_trip_time_respects_warm_history_and_asymptotic_threshold():
    cold = constant_current_trip_time(0., 3., 4., 2.)
    warm = constant_current_trip_time(2., 3., 4., 2.)
    assert 0 < warm < cold
    assert constant_current_level(0., 3., cold, 2.) == pytest.approx(4.)
    assert constant_current_level(2., 3., warm, 2.) == pytest.approx(4.)
    assert constant_current_trip_time(0., 2., 4., 2.) is None
    assert constant_current_trip_time(1., 0., 4., 2.) is None
    assert constant_current_trip_time(4., 3., 4., 2.) == 0.
    with pytest.raises(ValueError):
        constant_current_level(0., 2., -1., 2.)
    with pytest.raises(ValidationError):
        MotorThermalProtection(time_constant_s=1., trip_level=2., initial_level=2.)


@pytest.mark.parametrize('name', ['cold_trip', 'warm_trip', 'no_trip', 'blocked_reclose'])
def test_thermal_hybrid_trajectory_matches_independent_flux_model(name):
    s = example(name); actual = run_study(s)
    assert actual['completed'], actual
    expected = reference(s, actual)
    for j, key, tol in [(0, 'speed_pu', 2e-5), (1, 'thermal_level', 2e-5), (2, 'current_pu', 1e-4), (3, 'bus_voltage_pu', 1e-5)]:
        assert np.max(abs(expected['values'][:, j]-actual['channels']['m.'+key])) < tol
    trip = actual['motor_summary']['m']['thermal']['trip_time_s']
    if name == 'no_trip':
        assert trip is expected['trip_time_s'] is None
        assert not actual['motor_summary']['m']['thermal']['latched']
        assert actual['motor_summary']['m']['connected_at_end']
    else:
        assert trip == pytest.approx(expected['trip_time_s'], abs=1e-6)
        assert actual['motor_summary']['m']['thermal']['latched']
        assert not actual['motor_summary']['m']['connected_at_end']
    assert actual['maximum_power_balance_residual_pu'] < 1e-8


def test_trip_cools_continuously_and_blocks_reclose_even_when_cool():
    s = example('blocked_reclose'); r = run_study(s)
    assert r['completed'], r
    ch = r['channels']; times = np.asarray(r['time_s'])
    trip = next(e for e in r['event_log'] if e['kind'] == 'thermal_trip')
    assert trip['time_s'] not in np.arange(0., s.induction.duration_s, s.induction.step_s)
    a, b = np.flatnonzero(times == trip['time_s'])
    assert [r['sample_side'][a], r['sample_side'][b]] == ['pre_event', 'post_event']
    assert ch['m.current_pu'][a] > 1. and ch['m.current_pu'][b] == 0.
    for key in ['speed_pu', 'e_real_pu', 'e_imag_pu', 'thermal_level']:
        assert ch['m.'+key][a] == ch['m.'+key][b]
    thermal = s.induction.motors[0].thermal
    cool = thermal.trip_level*np.exp(-(times[b:]-trip['time_s'])/thermal.time_constant_s)
    assert ch['m.thermal_level'][b:] == pytest.approx(cool, abs=1e-7)
    assert all(x == 0. for x in ch['m.current_pu'][b:])
    command = next(e for e in r['event_log'] if e['kind'] == 'motor_close')
    assert command['outcome'] == 'blocked_thermal_latch'
    assert ch['m.thermal_level'][-1] < thermal.trip_level
    assert next(e for e in r['event_log'] if e['kind'] == 'motor_open')['outcome'] == 'already_in_requested_state'


def test_warm_start_trips_earlier_than_cold_and_integration_step_refines():
    cold = run_study(example()); warm = run_study(example('warm_trip'))
    cold_time = cold['motor_summary']['m']['thermal']['trip_time_s']
    assert warm['motor_summary']['m']['thermal']['trip_time_s'] < cold_time
    s = example(); s.induction.step_s /= 2
    fine = run_study(s)
    assert fine['completed']
    assert fine['motor_summary']['m']['thermal']['trip_time_s'] == pytest.approx(cold_time, abs=1e-7)


def test_identical_group_trips_atomically_and_preserves_each_thermal_state():
    s = example(); other = s.induction.motors[0].model_copy(deep=True); other.id = 'm2'
    s.induction.motors.append(other)
    r = run_study(s); assert r['completed'], r
    trips = [e for e in r['event_log'] if e['kind'] == 'thermal_trip']
    assert {e['equipment_id'] for e in trips} == {'m', 'm2'}
    assert trips[0]['time_s'] == trips[1]['time_s']
    at = np.flatnonzero(np.array(r['time_s']) == trips[0]['time_s'])
    assert len(at) == 2
    assert r['channels']['m.thermal_level'] == pytest.approx(r['channels']['m2.thermal_level'], abs=1e-10)
    assert r['channels']['m.current_pu'][at[-1]] == r['channels']['m2.current_pu'][at[-1]] == 0.


def test_unprotected_group_member_stays_connected_when_other_trips():
    s = example(); other = s.induction.motors[0].model_copy(deep=True); other.id = 'm2'; other.thermal = None
    s.induction.motors.append(other)
    r = run_study(s); assert r['completed'], r
    assert len([e for e in r['event_log'] if e['kind'] == 'thermal_trip']) == 1
    assert not r['motor_summary']['m']['connected_at_end']
    assert r['motor_summary']['m2']['connected_at_end']
    assert 'm2.thermal_level' not in r['channels']


def test_disabled_thermal_replica_leaves_original_study_channels_unchanged():
    s = unprotected(); original = run_study(s)
    protected = s.model_copy(deep=True)
    protected.induction.motors[0].thermal = MotorThermalProtection(time_constant_s=1., trip_level=30.)
    r = run_study(protected); assert r['completed'], r
    for key in ['speed_pu', 'current_pu', 'bus_voltage_pu']:
        assert r['channels']['m.'+key] == pytest.approx(original['channels']['m.'+key], abs=1e-6)


def test_thermal_worker_schema_and_cli(tmp_path):
    s = example(); a = run_study(s); b = run_study(s, isolate=True, wall_timeout_s=30.)
    assert a['completed'] and b['completed'], b
    assert a['event_log'] == b['event_log']
    assert a['channels'] == b['channels']
    path = tmp_path/'thermal.json'; path.write_text(s.model_dump_json())
    from power_grid_env.cli import main
    assert main(['run-study', str(path), '--out', str(tmp_path/'out.json'), '--isolate', '--wall-timeout', '30']) == 0
    assert json.loads((tmp_path/'out.json').read_text())['completed']
