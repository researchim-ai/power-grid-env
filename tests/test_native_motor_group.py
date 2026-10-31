"""Native group boundary, mixed-machine physics and one-motor compatibility."""
from pathlib import Path
import pytest
import numpy as np
from benchmarks.automatic_motor_group import example
from benchmarks.automatic_group_native import compare,command_oracle
from benchmarks.andes_group_motor_reference import scope,native_reference
from benchmarks.andes_joint_double_cage import sample_native


def bare(name):
    spec=example(name);s=spec.coupled if spec.coupled else spec.induction
    for m in s.motors:m.contactor=None;m.thermal=None
    return spec


@pytest.mark.parametrize('name',['grid_staggered','generator_feedback','grid_thermal_near'])
def test_native_group_physics_under_independent_decisions(name,tmp_path):
    r=compare(example(name),steps=(.00025,.0000625),out=tmp_path/name)
    assert r['passed'] and r['convergence_passed'],r
    assert r['motor_count']==3 and not r['field_validated'] and not r['native_closed_loop_group_controller_qualified']
    for step in r['steps']:
        assert step['maximum_accepted_algebraic_residual_pu']<=1e-8
        assert step['retained_samples']<step['native_steps_executed']


def test_native_group_one_motor_limit_matches_existing_adapter():
    from benchmarks.automatic_coupled_motor import example as single_example
    from benchmarks.andes_joint_motor_reference import native_reference as single_native
    s=single_example('basic');s.coupled.motors[0].contactor=None;s.coupled.motors[0].thermal=None
    old=single_native(s,.00025,terminal_adapter=True);new=native_reference(s,.00025)
    assert old['completed'] and new['completed'],(old,new)
    assert set(old['channels'])==set(new['channels'])
    for key,values in new['channels'].items():
        expected=sample_native(old,new['time_s'],new['sample_side'],key)
        assert np.max(abs(expected-values))<(1. if key.endswith(('.p_w','.q_var')) else 1e-6),key
    assert new['stock_generated_cache_unchanged'] and new['terminal_adaptation']['differential_equations_unchanged']


@pytest.mark.parametrize('change',['source_phases','terminal_phases','motor_bus','voltage_base','line_asymmetry','controller','event_target','charging'])
def test_unsupported_native_group_scope_is_explicit(change):
    s=bare('generator_staggered')
    if change=='source_phases':s.network.sources[0].phase_pu={'A':.9,'B':1.,'C':1.}
    elif change=='terminal_phases':s.network.terminals[0].phases=['A','B']
    elif change=='motor_bus':s.coupled.motors[0].node_id='grid'
    elif change=='voltage_base':s.network.generators[0].ll_v=11000.
    elif change=='line_asymmetry':s.network.lines[0].r_ohm[1][1]+=.1
    elif change=='controller':s.coupled.motors[0].contactor=example('generator_staggered').coupled.motors[0].contactor
    elif change=='event_target':s.coupled.events[0].equipment_id='missing'
    else:s.network.lines[0].b_siemens[0][0]=.001
    with pytest.raises(ValueError):scope(s)


def test_group_thermal_commands_are_targeted_and_do_not_replay_blocked_close():
    s=example('generator_thermal_tie')
    events=[dict(time_s=.01,kind='thermal_trip',equipment_id='m'),dict(time_s=.01,kind='thermal_trip',equipment_id='m2'),dict(time_s=.6,kind='motor_close',equipment_id='m2',outcome='blocked_thermal_latch')]
    native,p=command_oracle(s,events)
    assert all(m.contactor is None and m.thermal is None for m in native.coupled.motors)
    assert {e.equipment_id for e in native.coupled.events if e.kind=='motor_open'}=={'m','m2'}
    assert not any(e.kind=='motor_close' for e in native.coupled.events)
    assert .6 in p['noop_source_voltage_sampling_markers_s'] and not p['production_decisions_used']


def test_native_failure_cannot_become_physics_success(monkeypatch):
    monkeypatch.setattr('benchmarks.automatic_group_native.native_reference',lambda *a,**k:dict(completed=False,status='native_worker_failed'))
    r=compare(example('grid_staggered'),steps=(.00025,.000125))
    assert not r['passed'] and not r['convergence_passed']


def test_cold_native_group_respects_initial_static_q_limit():
    from power_grid_env.study_spec import StudySpec
    s=StudySpec.model_validate_json(Path('cases/studies/automatic_group_generator_cold_q_limit.json').read_text())
    for m in s.coupled.motors:m.contactor=None;m.thermal=None
    r=native_reference(s,.00025)
    assert not r['completed'] and r['status']=='native_worker_failed'
    assert 'native group generator initial Q limit' in r['log_tail']


@pytest.mark.parametrize('step',[float('nan'),0.,.02])
def test_native_step_budget_is_checked_before_launch(step):
    with pytest.raises(ValueError):native_reference(bare('grid_staggered'),step)


@pytest.mark.parametrize('output_step',[1e-12,1e-308,1e-5,float('inf')])
def test_output_sample_budget_fails_before_worker_launch(output_step,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('worker launched before sample-budget rejection')
    monkeypatch.setattr('benchmarks.andes_group_motor_reference.subprocess.Popen',forbidden)
    spec=bare('grid_scale32');spec.induction.step_s=output_step
    with pytest.raises(ValueError,match='output sample budget'):native_reference(spec,.00025)
