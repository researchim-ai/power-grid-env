"""Hybrid voltage-contactor timers, continuous roots and safety precedence."""
from pathlib import Path
import pytest
import numpy as np
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.induction import InductionEvent
from power_grid_env.motor_contactor import MotorContactorControl


def example():
    s=StudySpec.model_validate_json(Path('cases/studies/operating_mixed_sag.json').read_text())
    s.induction.motors[0].contactor=MotorContactorControl(sense_node_id='src',dropout_voltage_pu=.65,pickup_voltage_pu=.85,dropout_delay_s=.08,pickup_delay_s=.12,minimum_off_time_s=.25,maximum_automatic_restarts=2)
    return s


def automatic(report):return [(e['kind'],e['time_s']) for e in report['event_log'] if e.get('cause') in {'undervoltage_timer','automatic_restart'}]


@pytest.mark.parametrize('step',[.037,.005])
def test_exact_deadlines_are_independent_of_output_grid(step):
    s=example();s.induction.step_s=step;r=run_study(s);assert r['completed'],r
    assert [k for k,t in automatic(r)]==['motor_open','motor_close']
    assert [t for k,t in automatic(r)]==pytest.approx([.28,.62],abs=1e-12)
    assert r['channels']['m.contactor_restart_count'][-1]==1
    for t in [.28,.62]:
        assert [side for time,side in zip(r['time_s'],r['sample_side']) if time==pytest.approx(t,abs=1e-12)]==['pre_event','post_event']


def test_short_sag_cancels_dropout_and_hysteresis_band_keeps_loss_memory():
    s=example();s.induction.events[1].time_s=.24;r=run_study(s);assert r['completed'] and not automatic(r)
    s=example();s.induction.events.insert(1,InductionEvent(time_s=.24,kind='source_voltage',equipment_id='source',value=.75))
    r=run_study(s);assert r['completed'];assert automatic(r)[0]==('motor_open',.28)
    assert not r['channels']['m.contactor_voltage_available'][next(i for i,t in enumerate(r['time_s']) if t==.28)]


def test_minimum_off_time_dominates_pickup_delay():
    s=example();s.induction.motors[0].contactor.minimum_off_time_s=.7;r=run_study(s);assert r['completed']
    assert automatic(r)[1][1]==pytest.approx(.98,abs=1e-12)


def test_manual_open_inhibits_pending_restart_and_manual_close_obeys_voltage():
    s=example();s.induction.events.extend([InductionEvent(time_s=.3,kind='motor_close',equipment_id='m'),InductionEvent(time_s=.4,kind='motor_open',equipment_id='m')])
    r=run_study(s);assert r['completed'],r
    assert any(e.get('outcome')=='blocked_undervoltage' for e in r['event_log'])
    assert [k for k,t in automatic(r)]==['motor_open']
    assert not r['channels']['m.connected'][-1]


def test_initially_open_motor_is_not_automatically_started():
    s=example();s.induction.operating_point=None;s.induction.motors[0].connected=False;s.induction.events=[]
    r=run_study(s);assert r['completed'] and max(r['channels']['m.connected'])==0 and not automatic(r)


def test_repeated_sags_exhaust_declared_restart_budget():
    s=example();s.induction.motors[0].contactor.maximum_automatic_restarts=1
    s.induction.events.extend([InductionEvent(time_s=.8,kind='source_voltage',equipment_id='source',value=.5),InductionEvent(time_s=1.,kind='source_voltage',equipment_id='source',value=1.)])
    r=run_study(s);assert r['completed'];assert [k for k,t in automatic(r)]==['motor_open','motor_close','motor_open']
    assert not r['channels']['m.connected'][-1] and r['channels']['m.contactor_restart_count'][-1]==1


def test_simultaneous_loss_at_restart_deadline_prevents_automatic_close():
    s=example();s.induction.events.extend([InductionEvent(time_s=.62,kind='source_voltage',equipment_id='source',value=.5),InductionEvent(time_s=.9,kind='source_voltage',equipment_id='source',value=1.)])
    r=run_study(s);assert r['completed'];assert [t for k,t in automatic(r) if k=='motor_close']==pytest.approx([1.02])


def test_continuous_voltage_root_and_cancellation_agree_with_independent_SI():
    from benchmarks.induction_reference import rotor_flux_group_reference
    s=StudySpec.model_validate_json(Path('cases/studies/induction_start.json').read_text());s.induction.duration_s=3.;s.induction.events=[]
    m=s.induction.motors[0];m.fan_torque_pu=.2;m.inertia_s=.1;s.network.sources[0].x_ohm*=30
    m.contactor=MotorContactorControl(sense_node_id='motor',dropout_voltage_pu=.78,pickup_voltage_pu=.9,dropout_delay_s=2.,pickup_delay_s=.1,minimum_off_time_s=.2,maximum_automatic_restarts=1)
    roots=[]
    for step in [.037,.005]:
        s.induction.step_s=step;r=run_study(s);assert r['completed'] and not automatic(r),r
        event=next(e for e in r['event_log'] if e['kind']=='contactor_voltage_available');t=event['time_s'];roots.append(t)
        sample=dict(time_s=[0.,t-.001,t,t+.001,3.],sample_side=['initial','regular','regular','regular','regular'])
        oracle=s.model_copy(deep=True);oracle.induction.motors[0].contactor=None
        values=rotor_flux_group_reference(oracle,sample)['m'][:,4]
        assert values[1]<.9<values[3] and abs(values[2]-.9)<1e-7
    assert abs(roots[0]-roots[1])<1e-7


def test_thermal_latch_precedes_pending_voltage_dropout_and_remains_dominant():
    s=StudySpec.model_validate_json(Path('cases/studies/thermal_cold_trip.json').read_text());s.induction.motors[0].contactor=example().induction.motors[0].contactor
    s.induction.motors[0].contactor.dropout_voltage_pu=.99;s.induction.motors[0].contactor.pickup_voltage_pu=1.01;s.induction.motors[0].contactor.dropout_delay_s=.4
    r=run_study(s);assert r['completed'];assert any(e['kind']=='thermal_trip' for e in r['event_log'])
    assert not r['channels']['m.connected'][-1] and not any(k=='motor_close' for k,t in automatic(r))


def test_transition_budget_is_failure_not_truncated_success():
    s=example();s.induction.maximum_contactor_transitions=1;r=run_study(s)
    assert not r['completed'] and r['status']=='contactor_control_failed' and 'budget' in r['solver_message']


def test_unknown_sensing_node_and_invalid_hysteresis_are_rejected():
    s=example();s.induction.motors[0].contactor.sense_node_id='missing';r=run_study(s);assert not r['completed']
    with pytest.raises(ValueError):MotorContactorControl.model_validate(dict(sense_node_id='src',dropout_voltage_pu=.9,pickup_voltage_pu=.8,dropout_delay_s=.1,pickup_delay_s=.1,minimum_off_time_s=0,maximum_automatic_restarts=1))


def test_staggered_group_matches_predeclared_actions_and_independent_SI():
    import json
    from benchmarks.motor_contactor_reference import compare
    item=json.loads(Path('cases/contactor/reference-matrix.json').read_text())['cases'][-1]
    s=StudySpec.model_validate_json(Path(item['case']).read_text());r=compare(s,item['expected_automatic_actions']);assert r['passed'],r


def test_voltage_recovery_at_dropout_deadline_has_priority_over_timer():
    s=example();s.induction.events[1].time_s=.28;r=run_study(s)
    assert r['completed'] and not automatic(r)


def test_controlled_bundle_replays_and_restart_counter_is_exact(tmp_path):
    from power_grid_env.motor_bundle import MotorReplayCriteria,export_motor_bundle,replay_motor_bundle
    criteria=MotorReplayCriteria.model_validate_json(Path('cases/replay/motor-start.json').read_text())
    root=tmp_path/'controller';export_motor_bundle(example(),criteria,root);r=replay_motor_bundle(root)
    assert r['repeatable'] and r['event_audit_matches']
    assert r['metrics']['m.contactor_restart_count']['absolute_tolerance']==0


def test_coupled_rms_shares_control_but_modal_analysis_rejects_hybrid_passport():
    s=StudySpec.model_validate_json(Path('cases/studies/coupled_hold.json').read_text())
    motor=s.coupled.motors[0];motor.contactor=example().induction.motors[0].contactor;motor.contactor.sense_node_id=s.network.connectivity_nodes[0].id
    s.coupled.events=[];s.coupled.duration_s=.3
    r=run_study(s);assert r['completed'] and motor.id+'.contactor_voltage_available' in r['channels']
    s.study='coupled_small_signal';r=run_study(s);assert not r['completed'] and r['reason']=='hybrid_contactor_modal_analysis_unsupported'


def test_manual_close_observes_simultaneous_source_loss_even_if_command_list_is_reversed():
    s=example();s.induction.operating_point=None;s.induction.motors[0].connected=False
    s.induction.events=[InductionEvent(time_s=.2,kind='motor_close',equipment_id='m'),InductionEvent(time_s=.2,kind='source_voltage',equipment_id='source',value=.5)]
    r=run_study(s);assert r['completed']
    e=next(e for e in r['event_log'] if e['kind']=='motor_close');assert e['outcome']=='blocked_undervoltage'
    assert not r['channels']['m.connected'][-1]
