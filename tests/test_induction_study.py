"""Dynamic motor physics, independent circuits, switching and worker contracts."""
from pathlib import Path
import json
import math
import numpy as np
import pytest
from pydantic import ValidationError
from scipy.optimize import brentq
from benchmarks.induction_reference import equivalent_circuit, rotor_flux_reference
from power_grid_env.induction import InductionMotor, InductionSettings, InductionEvent
from power_grid_env.induction_network import InductionNetwork
from power_grid_env.study_spec import StudySpec, run_study, run_study_document


def example(name='start'):
    return StudySpec.model_validate_json(Path(f'cases/studies/induction_{name}.json').read_text())


@pytest.mark.parametrize('slip', [0., .01, .05, .5, 1.])
def test_stationary_flux_matches_full_T_equivalent_circuit(slip):
    s = example(); m = s.induction.motors[0]
    model = InductionNetwork(s.network, s.induction)
    voltage_si, current_si, torque = equivalent_circuit(s, slip)
    i_base = m.rated_va/(math.sqrt(3)*m.rated_ll_v)
    current = current_si/i_base
    _, d, tau = m.constants(s.network.frequency_hz)
    e = 1j*d*current/(1+1j*2*math.pi*s.network.frequency_hz*tau*slip)
    state = np.array([e.real, e.imag, 1-slip])
    v, i, t, mismatch = model.electrical(state)
    assert i[0] == pytest.approx(current, abs=1e-10)
    assert v[model.motor_buses[0]] == pytest.approx(voltage_si/(m.rated_ll_v/math.sqrt(3)), abs=1e-10)
    assert t[0] == pytest.approx(torque, abs=1e-10)
    assert model.rhs(0., state)[:2] == pytest.approx([0., 0.], abs=1e-10)
    assert mismatch < 1e-10


@pytest.mark.parametrize('name', ['start', 'sag_restart', 'contactor_restart'])
def test_complete_trajectory_matches_independent_SI_rotor_flux(name):
    s = example(name); r = run_study(s)
    assert r['completed'], r
    expected = rotor_flux_reference(s, r)
    for column, key, atol in [(0, 'speed_pu', 2e-5), (1, 'e_real_pu', 2e-5), (2, 'e_imag_pu', 2e-5),
                              (3, 'current_pu', 1e-4), (4, 'bus_voltage_pu', 1e-5), (5, 'p_w', 100.), (6, 'q_var', 100.)]:
        assert np.max(abs(expected[:, column]-r['channels']['m.'+key])) < atol
    assert r['maximum_kcl_residual_pu'] < 1e-8
    assert r['maximum_power_balance_residual_pu'] < 1e-8
    assert r['motor_summary']['m']['final_speed_at_least_0_95']
    assert not r['industrial_profile_qualified'] and not r['field_validated']
    # At the settled endpoint the torque-speed equilibrium uses the full T circuit.
    target = brentq(lambda slip: equivalent_circuit(s, slip)[2]-.8*(1-slip)**2, .001, .1)
    assert r['channels']['m.slip'][-1] == pytest.approx(target, abs=1e-4)


def test_contactor_preserves_flux_speed_and_exact_fan_coast():
    s = example('contactor_restart'); r = run_study(s)
    times = np.array(r['time_s']); ch = r['channels']
    for event_time in (2., 2.3):
        i, j = np.flatnonzero(times == event_time)
        assert [r['sample_side'][i], r['sample_side'][j]] == ['pre_event', 'post_event']
        for key in ['speed_pu', 'e_real_pu', 'e_imag_pu']:
            assert ch['m.'+key][i] == ch['m.'+key][j]
    start = np.flatnonzero(times == 2.)[-1]
    end = np.flatnonzero(times == 2.3)[0]
    assert np.array(ch['m.current_pu'])[start:end+1] == pytest.approx(0., abs=1e-12)
    speed = ch['m.speed_pu'][start]
    m = s.induction.motors[0]
    coast = speed/(1+speed*m.fan_torque_pu/(2*m.inertia_s)*.3)
    assert ch['m.speed_pu'][end] == pytest.approx(coast, abs=1e-7)
    assert ch['m.current_pu'][end+1] > 1.


def test_system_base_invariance_and_unenergized_motor():
    s = example(); s.induction.rtol = 1e-9; s.induction.atol = 1e-11; a = run_study(s)
    other = s.model_copy(deep=True); other.network.s_base_va = 3e6
    b = run_study(other)
    assert a['completed'] and b['completed']
    for key in ['speed_pu', 'bus_voltage_pu', 'current_pu', 'p_w', 'q_var']:
        assert a['channels']['m.'+key] == pytest.approx(b['channels']['m.'+key], rel=1e-8, abs=1e-6)
    other.induction.motors[0].connected = False
    off = run_study(other)
    assert off['completed']
    assert np.array(off['channels']['m.speed_pu']) == pytest.approx(0.)
    assert np.array(off['channels']['m.current_pu']) == pytest.approx(0.)
    assert not off['motor_summary']['m']['connected_at_end']


@pytest.mark.parametrize('group', ['YNyn0', 'Dyn11', 'YNd1', 'Dyn5'])
def test_multivoltage_group_snapshot_matches_independent_phase_backend(group):
    from test_multivoltage_rms import example as multi
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.topology import compile_topology
    s = example(); n = multi(group).network
    gen = n.generators.pop()
    n.terminals = [t for t in n.terminals if t.id != gen.terminal_id]
    n.loads[0].model = 'constant_z'
    s.network = n
    s.induction.duration_s = .02
    first = s.induction.motors[0]; first.node_id = 'load'
    second = first.model_copy(deep=True); second.id = 'm2'; second.rated_va = 5e5
    s.induction.motors.append(second)
    r = run_study(s); assert r['completed'], r
    # A flux-free energized motor is rs+jX'. Represent this independent
    # snapshot as a passive load in the full ABC OpenDSS backend.
    load = n.loads[0]
    for m in s.induction.motors:
        x, _, _ = m.constants(n.frequency_hz)
        power = m.rated_va/np.conj(m.stator_r_pu+1j*x)
        for phase in 'ABC':
            load.p_w[phase] += power.real/3
            load.q_var[phase] += power.imag/3
    phase = solve_network(n); assert phase.status == 'converged', phase.diagnostics
    for v in phase.voltages:
        if v.phase == 'A':
            assert r['channels'][v.bus_id+'.v'][0] == pytest.approx(abs(v.complex)/v.v_base_ln_v, abs=1e-5)
            actual = r['channels'][v.bus_id+'.a'][0]
            assert abs(np.angle(np.exp(1j*(actual-np.angle(v.complex))))) < 1e-4


def test_staggered_group_start_uses_shared_network_voltage():
    s = example(); s.induction.duration_s = 6.; s.network.lines[0].x_ohm = (np.array(s.network.lines[0].x_ohm)*15).tolist()
    m2 = s.induction.motors[0].model_copy(deep=True); m2.id = 'm2'
    s.induction.motors.append(m2)
    together = run_study(s); assert together['completed']
    s.induction.motors[1].connected = False
    s.induction.events = [InductionEvent(time_s=3., kind='motor_close', equipment_id='m2')]
    staggered = run_study(s); assert staggered['completed']
    assert min(staggered['channels']['m.bus_voltage_pu']) > min(together['channels']['m.bus_voltage_pu'])+.01
    assert staggered['channels']['m.bus_voltage_pu'] == staggered['channels']['m2.bus_voltage_pu']
    assert staggered['motor_summary']['m2']['final_speed_at_least_0_95']
    assert not together['motor_summary']['m']['final_speed_at_least_0_95']
    from benchmarks.induction_reference import rotor_flux_group_reference
    independent = rotor_flux_group_reference(s, staggered)
    for motor in s.induction.motors:
        assert np.max(abs(independent[motor.id][:, 0]-staggered['channels'][motor.id+'.speed_pu'])) < 2e-5


def test_rejected_scope_and_passport_never_runs_integrator(monkeypatch):
    import power_grid_env.induction_network as module
    monkeypatch.setattr(module, 'solve_ivp', lambda *a, **k: pytest.fail('must reject before integration'))
    s = example(); s.induction.motors[0].node_id = 'absent'
    assert run_study(s)['reason'] == 'induction_scope'
    s = example(); s.induction.motors[0].rated_ll_v = 11000.
    assert run_study(s)['reason'] == 'induction_scope'
    s = example(); s.induction.duration_s = 6.; s.network.lines[0].x_ohm[0][0] += .1
    assert run_study(s)['reason'] == 'induction_scope'
    s = example(); s.induction.step_s = 1e-5
    assert run_study(s)['reason'] == 'induction_scope'
    s = example(); s.induction.events = [InductionEvent(time_s=1., kind='motor_close', equipment_id='m')]
    assert run_study(s)['reason'] == 'induction_scope'
    s = example(); s.induction = None
    assert run_study(s)['reason'] == 'induction_settings_required'
    s = example(); s.study = 'power_flow'; s.backend = 'opendss'
    assert run_study(s)['reason'] == 'induction_settings_only_for_motor_start'
    payload = example().model_dump(mode='json'); payload['induction']['motors'][0]['rotor_r_pu'] = float('nan')
    assert run_study_document(payload)['reason'] == 'schema'
    with pytest.raises(ValidationError):
        InductionEvent(time_s=1., kind='source_voltage', equipment_id='source', value=-1)
    with pytest.raises(ValidationError):
        InductionSettings(motors=[example().induction.motors[0]]*2)


def test_worker_cli_deadline_cancellation_and_recovery(tmp_path):
    s = example('sag_restart'); direct = run_study(s)
    isolated = run_study(s, isolate=True, wall_timeout_s=30.)
    assert isolated['completed'], isolated
    assert isolated['channels'] == direct['channels']
    assert isolated['execution']['mode'] == 'isolated_process'
    assert run_study(s, wall_timeout_s=.0001)['status'] == 'timeout'
    assert run_study(s, cancelled=lambda: True)['status'] == 'cancelled'
    from power_grid_env.cli import main
    path = tmp_path/'spec.json'; path.write_text(s.model_dump_json())
    assert main(['run-study', str(path), '--out', str(tmp_path/'result.json'), '--isolate', '--wall-timeout', '30']) == 0
    assert json.loads((tmp_path/'result.json').read_text())['completed']


def test_numerical_failure_is_not_a_success(monkeypatch):
    import power_grid_env.induction_network as module
    def failed(*args, **kwargs):
        raise ValueError('test integration failure')
    monkeypatch.setattr(module, 'solve_ivp', failed)
    result = run_study(example())
    assert result['solved'] and not result['completed']
    assert result['status'] == 'numerically_not_converged'
    assert 'channels' not in result


def test_source_and_motor_channels_follow_device_ids_without_collision():
    s = example(); s.induction.duration_s = .05
    original = run_study(s); assert original['completed']
    source = s.network.sources[0]
    terminal = next(t for t in s.network.terminals if t.id == source.terminal_id)
    source.id = 'feed'; terminal.equipment_id = 'feed'
    s.induction.motors[0].id = 'source'
    renamed = run_study(s); assert renamed['completed'], renamed
    assert renamed['channels']['source.p_w'] == original['channels']['m.p_w']
    assert renamed['channels']['feed.p_w'] == original['channels']['source.p_w']
    assert renamed['mapping']['motor_buses']['source'] == original['mapping']['motor_buses']['m']


def test_lossless_line_accepts_sequence_roundoff_but_rejects_negative_resistance():
    s = example(); s.induction.duration_s = .02
    s.network.lines[0].r_ohm = np.zeros((3, 3)).tolist()
    s.network.lines[0].x_ohm = (10*np.eye(3)).tolist()
    # Complex projection can produce a real part around -1e-16 for j10.
    result = run_study(s); assert result['completed'], result
    s.network.lines[0].r_ohm = (-.1*np.eye(3)).tolist()
    rejected = run_study(s)
    assert rejected['reason'] == 'induction_scope'
