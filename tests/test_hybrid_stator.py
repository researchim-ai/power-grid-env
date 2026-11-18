import copy
import numpy as np
import pytest
from benchmarks.hybrid_stator_cases import example
from benchmarks.hybrid_stator_reference import HybridStatorReference
from power_grid_env.study_spec import StudySpec,run_study
from tests.test_two_cage_winding import initial
from power_grid_env.rms_winding import operator


@pytest.mark.parametrize('profile',['stator','fixed_R'])
def test_discrete_generator_open_invalidates_cache_before_factorize(profile):
    from benchmarks.stator_trip_cases import example as trip_example
    from benchmarks.controlled_winding_cases import example as fixed_example
    m,x,_=initial(trip_example() if profile=='stator' else fixed_example());before=m.electrical(x)[0];m.gen_connected[0]=False
    immediate=m.electrical(x)[0];m.factorize();fresh=m.electrical(x)[0]
    np.testing.assert_allclose(immediate,fresh,rtol=0,atol=1e-14)
    assert max(abs(before-fresh))>.01


def test_discrete_motor_open_invalidates_cache_before_factorize():
    m,x,_=initial(example());m.electrical(x);m.connected[0]=False
    immediate=m.electrical(x)[0];m.factorize();fresh=m.electrical(x)[0]
    np.testing.assert_allclose(immediate,fresh,rtol=0,atol=1e-14)

@pytest.mark.parametrize('method',['radau','network_dae'])
@pytest.mark.parametrize('lead',[.1,1.])
def test_unified_trip_and_limiter_reference(method,lead):
    from benchmarks.hybrid_stator_acceptance import compare
    s=example(lead=lead);s['coupled']['integration_method']=method
    report=run_study(StudySpec.model_validate(s));ref=HybridStatorReference(s).run()
    assert report['completed'] and compare(ref,report)['passed']
    trip=next(e for e in report['event_log'] if e['kind']=='generator_thermal_trip')
    releases=[e for e in report['event_log'] if e['kind']=='controller_limit_release' and e['controller']=='SEXS']
    assert releases[0]['time_s']==trip['time_s'] if lead==1. else releases[0]['time_s']>trip['time_s']

@pytest.mark.parametrize('mode',['passport','equilibrium'])
def test_event_list_permutation(mode):
    a,b=[run_study(StudySpec.model_validate(example(mode,reverse))) for reverse in [False,True]]
    assert a['completed'] and b['completed']
    for key in ['time_s','sample_side','event_log','channels']:assert a[key]==b[key]


def test_trip_cascade_preserves_state_and_removes_current():
    ref=HybridStatorReference(example()).run();trip=next(e['time_s'] for e in ref.events if e['kind']=='generator_thermal_trip')
    pre,post=[ref.sample(trip,s) for s in ['pre_event','post_event']]
    for key in ['g.delta','g.omega','g.heat.temperature_c','m.speed_pu','g.stator_thermal_level']:assert pre[key]==pytest.approx(post[key],abs=1e-12)
    assert post['g.current_pu']==post['g.heat.copper_loss_w']==0.
    assert post['g.heat.core_loss_w']>0.


def test_no_native_event_or_stator_helpers(monkeypatch):
    from power_grid_env.controller_boundaries import ControllerBoundaries
    from power_grid_env.generator_thermal import GeneratorThermalBank
    import power_grid_env.generator_stator as stator
    def forbidden(*a,**k):raise AssertionError('native helper used')
    for name in ['guards','apply','refresh','rates']:monkeypatch.setattr(ControllerBoundaries,name,forbidden)
    monkeypatch.setattr(GeneratorThermalBank,'apply',forbidden);monkeypatch.setattr(stator,'impedance',forbidden)
    ref=HybridStatorReference(example()).run();assert any(e['kind']=='generator_thermal_trip' for e in ref.events)


def test_two_point_energy_segment_is_included():
    from benchmarks.energy_integral_gate import native_error
    times=[0.,.1,.1,.10005,.10005,.2,.2,.5];sides=['initial','pre_event','post_event','pre_event','post_event','pre_event','post_event','regular']
    report=dict(time_s=times,sample_side=sides,event_log=[dict(time_s=t) for t in [.1,.10005,.2]],channels={})
    c=report['channels'];c['g.energy.shaft_power_w']=[1+2*t for t in times]
    for key in ['terminal_power_w','signed_damping_exchange_w','ambient_heat_w','thermal_energy_j','balance_residual_w']:c['g.energy.'+key]=[0.]*len(times)
    c['g.energy.kinetic_energy_j']=[t+t*t for t in times]
    assert native_error(report)<1e-12
