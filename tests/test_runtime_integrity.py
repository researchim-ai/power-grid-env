"""Actual work/output caps, global controller guards and event sampling contracts."""
import copy
import numpy as np
import pytest
from pydantic import ValidationError
from power_grid_env.runtime_limits import RuntimeLimits,IntegrationBudget,RuntimeLimitExceeded
from power_grid_env.study_spec import run_study
from benchmarks.generator_excitation_cases import example
from benchmarks.event_sampling import native_sample_sides


@pytest.mark.parametrize('resource,value',[('maximum_rhs_evaluations',0),('maximum_event_batches',0),('maximum_retained_scalars',0),('maximum_integration_wall_s',0),('maximum_integration_wall_s',float('inf'))])
def test_runtime_limits_reject_nonpositive_and_nonfinite(resource,value):
    with pytest.raises(ValidationError):RuntimeLimits(**{resource:value})


@pytest.mark.parametrize('resource',['maximum_rhs_evaluations','maximum_event_batches','maximum_retained_scalars'])
def test_runtime_cap_is_enforced_before_extra_work(resource):
    b=IntegrationBudget(RuntimeLimits(**{resource:1}))
    if resource=='maximum_rhs_evaluations':
        calls=[];f=b.rhs(lambda t,x:calls.append(t));f(0,None)
        with pytest.raises(RuntimeLimitExceeded):f(1,None)
        assert calls==[0]
    elif resource=='maximum_event_batches':
        b.event_batch()
        with pytest.raises(RuntimeLimitExceeded):b.event_batch()
    else:
        b.record(1)
        with pytest.raises(RuntimeLimitExceeded):b.record(1)


def test_wall_deadline_uses_monotonic_elapsed_time():
    times=iter([100.,100.5,101.]);b=IntegrationBudget(RuntimeLimits(maximum_integration_wall_s=1.),clock=lambda:next(times))
    b.check_time()
    with pytest.raises(RuntimeLimitExceeded,match='integration_wall'):b.check_time()


@pytest.mark.parametrize('resource,value,reason',[('maximum_rhs_evaluations',1,'rhs_evaluations'),('maximum_event_batches',1,'event_batches'),('maximum_retained_scalars',1,'retained_scalars')])
@pytest.mark.parametrize('isolated',[False,True])
def test_public_study_returns_explicit_failure_without_partial_channels(resource,value,reason,isolated):
    s=example('exciter_floor');s.coupled.overexcitation_limiters=[]
    setattr(s.coupled.runtime_limits,resource,value)
    r=run_study(s,isolate=isolated,wall_timeout_s=90.)
    assert not r['completed'] and r['status']=='runtime_limit_exceeded' and r['reason']==reason
    assert 'channels' not in r
    assert r['resource_limit']['attempted']>r['resource_limit']['limit']


def test_global_governor_bound_and_release_without_OEL():
    s=example('thermal_trip');s.coupled.overexcitation_limiters=[]
    r=run_study(s);assert r['completed'],r
    events=[e for e in r['event_log'] if e['kind'].startswith('controller_limit_')]
    assert any(e['controller']=='TGOV1' and e['bound']=='lower' for e in events)
    assert any(e['controller']=='TGOV1' and e['kind']=='controller_limit_release' for e in events)
    assert min(r['channels']['g2.TGOV1.valve_state_pu'])>=-1e-12


def test_return_from_floor_has_one_atomic_command_boundary_without_OEL():
    s=example('exciter_floor');s.coupled.overexcitation_limiters=[]
    r=run_study(s);assert r['completed'],r
    assert [side for t,side in zip(r['time_s'],r['sample_side']) if t==.8]==['pre_event','post_event']
    assert any(e['kind']=='controller_limit_release' and e['time_s']==.8 for e in r['event_log'])
    assert r['runtime_budget']['event_batches']>=3


def test_native_sampling_relaxes_only_pure_continuous_controller_roots():
    r=dict(time_s=[.2,.2,.3,.3,.4,.4],sample_side=['pre_event','post_event']*3,event_log=[
        dict(time_s=.2,kind='controller_limit_enter'),dict(time_s=.3,kind='controller_limit_release'),dict(time_s=.3,kind='source_voltage')])
    assert native_sample_sides(r)==['regular','regular','pre_event','post_event','pre_event','post_event']


def test_runtime_caps_do_not_change_trajectory_when_not_reached():
    s=example('hold');s.coupled.duration_s=.1
    a=run_study(s);s.coupled.runtime_limits.maximum_rhs_evaluations=1_000_000
    b=run_study(s)
    assert a['completed'] and b['completed'] and a['channels']==b['channels'] and a['time_s']==b['time_s']


def test_native_sampling_keeps_grid_and_all_physical_event_sides():
    from benchmarks.event_sampling import native_sample_indices
    r=dict(time_s=[0.,.125,.125,.2,.2,.325,.325,.4],
           sample_side=['regular','pre_event','post_event','pre_event','post_event','pre_event','post_event','regular'],
           event_log=[dict(time_s=.125,kind='controller_limit_enter'),
                      dict(time_s=.2,kind='controller_limit_release'),
                      dict(time_s=.325,kind='controller_limit_enter'),dict(time_s=.325,kind='source_voltage')])
    assert native_sample_indices(r,.1)==[0,3,4,5,6,7]
    with pytest.raises(ValueError):native_sample_indices(r,float('nan'))
