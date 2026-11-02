"""Engineering acceptance is distinct from successful numerical integration."""
import copy
import numpy as np
import pytest
from benchmarks.generator_excitation_cases import example
from power_grid_env.motor_assessment import MotorAcceptanceCriteria,assess
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash


def fixture():
    s=example('hold');s.coupled.duration_s=2.
    r=dict(completed=True,status='completed',code_sha256=package_fingerprint(),network_sha256=canonical_hash(s.network),settings=s.coupled.model_dump(mode='json'),
           time_s=[0.,.5,1.,1.5,2.],sample_side=['initial','regular','regular','regular','regular'],channels={},event_log=[])
    for m in s.coupled.motors:
        for k,v in dict(speed_pu=[0.,.3,.9,.97,1.],current_pu=[5.,3.,1.,.5,.5],bus_voltage_pu=[.75,.8,.95,1.,1.],connected=[1.]*5).items():r['channels'][m.id+'.'+k]=v
        if m.thermal:r['channels'][m.id+'.thermal_latched']=[0.]*5
    return s,r


def test_declared_startup_policy_accepts_sustained_speed_without_guessing_inrush_limit():
    s,r=fixture();a=assess(s,r,MotorAcceptanceCriteria())
    assert a['passed'] and a['calculation_completed']
    assert all(m['maximum_sampled_current_pu']==5. for m in a['motors'].values())


def test_momentary_speed_threshold_does_not_satisfy_settling_window():
    s,r=fixture()
    for m in s.coupled.motors:r['channels'][m.id+'.speed_pu']=[0.,.9,1.,.94,.96]
    a=assess(s,r,MotorAcceptanceCriteria(settling_window_s=.6))
    assert not a['passed'] and a['calculation_completed'] and a['status']=='criteria_not_met'


@pytest.mark.parametrize('kw,reason',[({'minimum_sampled_voltage_pu':.8},'sampled_voltage_below_declared_minimum'),({'maximum_sampled_current_pu':4.},'sampled_current_above_declared_maximum'),({'settling_window_s':3.},'insufficient_horizon_for_settling_window')])
def test_project_limits_are_explicit(kw,reason):
    s,r=fixture();a=assess(s,r,MotorAcceptanceCriteria(**kw));assert not a['passed']
    assert all(reason in row['failure_reasons'] for row in a['motors'].values())


@pytest.mark.parametrize('defect',['stale_code','wrong_settings','nonfinite','shape','unpaired','duplicate','discrete'])
def test_invalid_evidence_rejected(defect):
    s,r=fixture();gid=s.coupled.motors[0].id
    if defect=='stale_code':r['code_sha256']='0'*64
    elif defect=='wrong_settings':r['settings']['duration_s']=3.
    elif defect=='nonfinite':r['channels'][gid+'.speed_pu'][-1]=float('nan')
    elif defect=='shape':r['channels'][gid+'.current_pu'].pop()
    elif defect=='unpaired':r['sample_side'][1]='pre_event'
    elif defect=='duplicate':r['time_s'][2]=r['time_s'][1]
    else:r['channels'][gid+'.connected'][-1]=.5
    with pytest.raises(ValueError):assess(s,r,MotorAcceptanceCriteria())


def test_failed_calculation_cannot_be_reported_as_a_failed_physical_start():
    s,r=fixture();r.update(completed=False,status='runtime_limit_exceeded',reason='rhs_evaluations')
    a=assess(s,r,MotorAcceptanceCriteria());assert not a['calculation_completed'] and a['status']=='calculation_failed' and not a['motors']


def test_switch_at_settling_window_edge_uses_post_event_state():
    s,r=fixture();r['time_s']=[0.,1.8,1.8,2.];r['sample_side']=['initial','pre_event','post_event','regular']
    for m in s.coupled.motors:
        r['channels'][m.id+'.speed_pu']=[.99]*4;r['channels'][m.id+'.current_pu']=[1.]*4
        r['channels'][m.id+'.bus_voltage_pu']=[1.]*4;r['channels'][m.id+'.connected']=[0.,0.,1.,1.]
        if m.thermal:r['channels'][m.id+'.thermal_latched']=[0.]*4
    assert assess(s,r,MotorAcceptanceCriteria(settling_window_s=.2))['passed']


def test_real_three_second_runup_is_accepted_and_criteria_do_not_mutate_trace():
    from benchmarks.multibus_motor_cases import example as motors
    from power_grid_env.study_spec import run_study
    s=motors('runup3s');r=run_study(s,isolate=True,wall_timeout_s=90.);assert r['completed'],r
    before=copy.deepcopy(r);a=assess(s,r,MotorAcceptanceCriteria())
    assert a['passed'] and r==before
