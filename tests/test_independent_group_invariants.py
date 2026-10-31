"""Adversarial checks of controller independence and equipment identity."""
import pytest
from benchmarks.automatic_motor_group import example,compare
from benchmarks.double_cage_reference import reference
from benchmarks.independent_group_contactor import run as hybrid
from benchmarks.group_si_generator import Generator
from power_grid_env.study_spec import run_study


def settings(spec):return spec.coupled if spec.coupled else spec.induction


def oracle(spec,sample):
    return reference(spec,sample,hybrid_runner=hybrid,generator_factory=Generator if spec.coupled else None)


@pytest.mark.parametrize('name',['grid_staggered','generator_staggered'])
def test_independent_group_never_calls_production_models_or_reads_their_commands(name,monkeypatch):
    spec=example(name);actual=run_study(spec)
    assert actual['completed']
    sample=dict(time_s=actual['time_s'],sample_side=actual['sample_side'],
                channels={'forged_channel':[999.]},event_log=[dict(kind='motor_open',equipment_id='not-a-motor',time_s=-999.)])
    def forbidden(*args,**kwargs):raise AssertionError('production physics/control entered independent oracle')
    monkeypatch.setattr('power_grid_env.motor_contactor.MotorContactorBank',forbidden)
    monkeypatch.setattr('power_grid_env.induction_network.InductionNetwork',forbidden)
    monkeypatch.setattr('power_grid_env.coupled_rms.CoupledNetwork',forbidden)
    monkeypatch.setattr('benchmarks.double_cage_reference.run_study',forbidden)
    result=oracle(spec,sample)
    actions=[e for e in result['independent_event_log'] if e.get('cause')=='automatic_restart']
    assert {e['equipment_id'] for e in actions}=={'m','m2','m3'}
    assert all(result['control_channels'][m.id+'.contactor_restart_count'][-1]==1 for m in settings(spec).motors)


@pytest.mark.parametrize('name',['grid_feedback','generator_feedback'])
def test_group_roots_are_independent_of_output_grid(name):
    spec=example(name);settings(spec).step_s=.037
    r=compare(spec);assert r['passed'],r
    if name=='generator_feedback':
        events=r['expected_events']
        # Restart of m depresses the shared bus continuously between the
        # external voltage steps. The other pending drives must restart later.
        unavailable=[e for e in events if e['kind']=='contactor_voltage_unavailable' and e['equipment_id']=='m2' and .62<e['time_s']<.75]
        assert len(unavailable)==1
        assert unavailable[0]['time_s'] not in [e.time_s for e in settings(spec).events]
        next_m2=next(e for e in events if e['equipment_id']=='m2' and e.get('cause')=='automatic_restart')
        assert next_m2['time_s']>.75


@pytest.mark.parametrize('name',['grid_staggered','generator_staggered'])
def test_equipment_identity_survives_state_reordering(name):
    spec=example(name);settings(spec).motors.reverse()
    r=compare(spec);assert r['passed'],r


@pytest.mark.parametrize('name',['grid_staggered','generator_staggered'])
def test_uncontrolled_member_still_participates_in_common_circuit(name):
    spec=example(name);settings(spec).motors[1].contactor=None
    r=compare(spec);assert r['passed'],r
    assert not any(e.get('equipment_id')=='m2' and e.get('cause') in {'automatic_restart','undervoltage_timer'} for e in r['expected_events'])


@pytest.mark.parametrize('name',['grid_staggered','generator_staggered'])
def test_group_transition_budget_is_a_failure_in_both_implementations(name):
    spec=example(name);settings(spec).maximum_contactor_transitions=1
    actual=run_study(spec);assert actual['completed'] is False and actual['status']=='contactor_control_failed'
    with pytest.raises(ValueError,match='transition budget'):
        oracle(spec,dict(time_s=[0.,1.2],sample_side=['initial','regular']))


@pytest.mark.parametrize('change',['sensing_node','event_target','line_asymmetry','terminal_phases'])
def test_unsupported_group_input_is_not_silently_substituted(change):
    spec=example('grid_staggered')
    if change=='sensing_node':spec.induction.motors[0].contactor.sense_node_id='missing'
    elif change=='event_target':spec.induction.events[0].equipment_id='missing'
    elif change=='line_asymmetry':spec.network.lines[0].r_ohm[1][1]+=.1
    else:spec.network.terminals[0].phases=['A','B']
    with pytest.raises(ValueError):oracle(spec,{})


def test_near_thermal_events_cannot_be_coalesced_in_comparison_clock():
    spec=example('grid_thermal_near');actual=run_study(spec);assert actual['completed']
    pairs=[i for i,side in enumerate(actual['sample_side']) if side=='pre_event']
    # Remove one thermal pre/post pair; other pairs remain so this is a
    # corrupted event-aligned trace, not a legitimate regular-only request.
    index=next(i for i in pairs if 0<actual['time_s'][i]<.2)
    sample={k:[v for j,v in enumerate(actual[k]) if j not in {index,index+1}] for k in ['time_s','sample_side']}
    with pytest.raises(ValueError,match='event clock mismatch'):oracle(spec,sample)
