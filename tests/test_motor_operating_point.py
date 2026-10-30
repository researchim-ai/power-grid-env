"""Loaded branch initialization, load laws and independent physical circuit closure."""
from pathlib import Path
import json
import numpy as np
import pytest
from pydantic import ValidationError
from benchmarks.induction_reference import equivalent_circuit, rotor_flux_group_reference
from power_grid_env.induction import MotorMechanicalLoad, MotorOperatingPoint
from power_grid_env.motor_thermal import MotorThermalProtection, constant_current_level
from power_grid_env.study_spec import StudySpec, run_study


def example(name='fan_hold'):
    return StudySpec.model_validate_json(Path('cases/studies/operating_'+name+'.json').read_text())


@pytest.mark.parametrize('name', ['fan_hold', 'mixed_sag', 'load_step', 'group_sag', 'thermal_hold', 'contactor_restart'])
def test_loaded_trajectory_matches_independent_T_circuit_and_SI_flux(name):
    spec = example(name); report = run_study(spec)
    assert report['completed'], report
    assert report['operating_point']['dynamic_residual_per_s'] < 1e-8
    assert report['operating_point']['connected_machine_spectral_abscissa_per_s'] < 0
    expected = rotor_flux_group_reference(spec, report)
    for motor in spec.induction.motors:
        for i, key, limit in [(0,'speed_pu',2e-5),(1,'e_real_pu',2e-5),(2,'e_imag_pu',2e-5),
                              (3,'current_pu',1e-4),(4,'bus_voltage_pu',1e-5),(5,'p_w',100.),(6,'q_var',100.)]:
            assert np.max(abs(expected[motor.id][:,i]-report['channels'][motor.id+'.'+key])) < limit
    assert report['maximum_kcl_residual_pu'] < 1e-8
    assert report['maximum_power_balance_residual_pu'] < 1e-8


def test_loaded_machine_holds_without_artificial_runup_and_matches_circuit():
    s = example(); r = run_study(s); assert r['completed']
    for key in ['speed_pu','current_pu','bus_voltage_pu','e_real_pu','e_imag_pu']:
        values = np.array(r['channels']['m.'+key])
        assert np.max(abs(values-values[0])) < 1e-8
    assert r['channels']['m.speed_pu'][0] > .95
    slip = r['channels']['m.slip'][0]
    _, _, torque = equivalent_circuit(s, slip)
    assert torque == pytest.approx(s.induction.motors[0].fan_torque_pu*(1-slip)**2, abs=1e-8)


def test_zero_load_boundary_root_is_verified_at_synchronous_speed():
    s = example(); s.induction.motors[0].fan_torque_pu = 0.
    r = run_study(s); assert r['completed'], r
    assert r['operating_point']['dynamic_residual_per_s'] < 1e-8
    assert r['channels']['m.speed_pu'] == pytest.approx(np.ones(len(r['time_s'])), abs=1e-8)


def test_explicit_load_law_is_dissipative_and_legacy_fan_cannot_be_double_counted():
    load = MotorMechanicalLoad(viscous_torque_pu=.2, quadratic_torque_pu=.4, friction_torque_pu=.2, smoothing_speed_pu=.01)
    assert load.torque(0.) == 0.
    for speed in [-2., -.1, -.001, .001, .1, 2.]:
        assert load.torque(speed)*speed > 0
        assert load.torque(-speed) == pytest.approx(-load.torque(speed))
    assert load.torque(1.) == pytest.approx(.8)
    with pytest.raises(ValidationError):
        MotorMechanicalLoad(friction_torque_pu=.1)
    with pytest.raises(ValidationError):
        MotorMechanicalLoad(smoothing_speed_pu=.1)
    s = example(); payload = s.model_dump(mode='json')
    payload['induction']['motors'][0]['mechanical_load'] = load.model_dump()
    with pytest.raises(ValidationError):
        StudySpec.model_validate(payload)
    del payload['induction']['motors'][0]['fan_torque_pu']
    assert StudySpec.model_validate(payload).induction.motors[0].mechanical_load == load


def test_torque_step_changes_acceleration_without_resetting_states():
    r = run_study(example('load_step')); assert r['completed']
    before, after = np.flatnonzero(np.asarray(r['time_s']) == .2)
    for key in ['speed_pu','e_real_pu','e_imag_pu','current_pu']:
        assert r['channels']['m.'+key][before] == r['channels']['m.'+key][after]
    assert r['channels']['m.load_torque_pu'][after] == pytest.approx(r['channels']['m.load_torque_pu'][before]*1.25)
    assert r['channels']['m.speed_pu'][-1] < r['channels']['m.speed_pu'][0]
    assert r['channels']['m.current_pu'][-1] > r['channels']['m.current_pu'][0]


def test_thermal_passport_and_equilibrium_are_distinct_explicit_initializations():
    s = example('thermal_hold'); r = run_study(s); assert r['completed']
    initial = r['channels']['m.current_pu'][0]**2
    assert r['channels']['m.thermal_level'] == pytest.approx(np.full(len(r['time_s']),initial), abs=1e-8)
    s.induction.operating_point.thermal_state = 'passport'
    r = run_study(s); assert r['completed']
    assert r['channels']['m.thermal_level'][0] == 0.
    expected = [constant_current_level(0., np.sqrt(initial), t, 1.) for t in r['time_s']]
    assert r['channels']['m.thermal_level'] == pytest.approx(expected, abs=1e-7)
    assert r['operating_point']['thermal_residual_per_s']['m'] > 0


def test_unsatisfied_operating_point_and_initial_thermal_limit_reject_before_integration(monkeypatch):
    import power_grid_env.induction_network as solver
    monkeypatch.setattr(solver, 'solve_ivp', lambda *a, **kw: pytest.fail('invalid initialization must not integrate'))
    s = example(); s.induction.motors[0].fan_torque_pu = 1000.
    failed = run_study(s)
    assert failed['status'] == 'initialization_failed'
    assert failed['reason'] == 'no_qualified_operating_point_found'
    assert failed['operating_point']['best_speed_derivative_residual_per_s'] > 1.
    s = example('thermal_hold'); s.induction.motors[0].thermal.trip_level = .1
    assert run_study(s)['reason'] == 'initial_thermal_limit'
    s = example(); s.induction.motors[0].connected = False
    assert run_study(s)['reason'] == 'no_connected_motor_for_operating_point'


def test_bounded_search_failure_is_explicit(monkeypatch):
    import power_grid_env.motor_operating_point as module
    monkeypatch.setattr(module,'MAX_EVALUATIONS',1)
    r = run_study(example())
    assert not r['completed'] and r['reason'] == 'operating_point_search_failed'


def test_disconnected_member_stays_at_rest_and_system_base_does_not_change_operating_point():
    s = example(); m = s.induction.motors[0].model_copy(deep=True); m.id='off'; m.connected=False
    s.induction.motors.append(m)
    a = run_study(s); assert a['completed'], a
    assert a['channels']['off.speed_pu'] == pytest.approx(np.zeros(len(a['time_s'])))
    assert a['channels']['off.current_pu'] == pytest.approx(np.zeros(len(a['time_s'])))
    s.network.s_base_va = 3e6
    b = run_study(s); assert b['completed'], b
    assert a['operating_point']['speed_pu'] == pytest.approx(b['operating_point']['speed_pu'], abs=1e-8)
    assert a['operating_point']['bus_voltage_pu'] == pytest.approx(b['operating_point']['bus_voltage_pu'], abs=1e-8)


@pytest.mark.parametrize('group', ['Dyn11','YNd1','Dyn5'])
def test_loaded_multivoltage_snapshot_matches_independent_phase_backend(group):
    from test_multivoltage_rms import example as multi
    from power_grid_env.backend_opendss import solve_network
    s = example(); n = multi(group).network
    gen = n.generators.pop(); n.terminals = [t for t in n.terminals if t.id != gen.terminal_id]
    n.loads[0].model = 'constant_z'; s.network=n; s.induction.motors[0].node_id='load'
    r = run_study(s); assert r['completed'],r
    vpu = r['channels']['m.bus_voltage_pu'][0]
    power = complex(r['channels']['m.p_w'][0],r['channels']['m.q_var'][0])/vpu**2
    for phase in 'ABC':
        n.loads[0].p_w[phase] += power.real/3; n.loads[0].q_var[phase] += power.imag/3
    abc = solve_network(n); assert abc.status=='converged',abc.diagnostics
    for voltage in abc.voltages:
        if voltage.phase=='A':
            assert r['channels'][voltage.bus_id+'.v'][0] == pytest.approx(abs(voltage.complex)/voltage.v_base_ln_v,abs=1e-5)
            angle = r['channels'][voltage.bus_id+'.a'][0]
            assert abs(np.angle(np.exp(1j*(angle-np.angle(voltage.complex))))) < 1e-4


def test_loaded_worker_and_cli_match_direct(tmp_path):
    s=example('group_sag');a=run_study(s);b=run_study(s,isolate=True,wall_timeout_s=30.)
    assert a['completed'] and b['completed'],b
    assert a['channels'] == b['channels']
    assert a['operating_point'] == b['operating_point']
    from power_grid_env.cli import main
    path=tmp_path/'study.json';path.write_text(s.model_dump_json())
    assert main(['run-study',str(path),'--out',str(tmp_path/'out.json'),'--isolate','--wall-timeout','30']) == 0
    assert json.loads((tmp_path/'out.json').read_text())['completed']
