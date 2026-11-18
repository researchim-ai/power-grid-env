import copy
import numpy as np
import pytest
from benchmarks.stator_limiter_cases import example
from benchmarks.stator_limiter_reference import StatorLimiterReference
from benchmarks.stator_limiter_acceptance import compare,coverage
from power_grid_env.study_spec import StudySpec,run_study
from tests.test_two_cage_winding import initial
from power_grid_env.dae import NetworkDAE,residual_product
from power_grid_env.dae_linearization import DAEBlocks

@pytest.mark.parametrize('controls',['governor','exciter','both'])
@pytest.mark.parametrize('method',['radau','network_dae'])
def test_independent_stator_limiter_events_and_waveforms(controls,method):
    s=example(controls);s['coupled']['integration_method']=method;r=run_study(StudySpec.model_validate(s));ref=StatorLimiterReference(s).run()
    assert r['completed'] and compare(ref,r)['passed'];coverage(r['event_log'],controls)
    assert max(abs(v) for v in r['channels']['g.stator.power_balance_w'])<1e-7

@pytest.mark.parametrize('inconsistent',[False,True])
def test_clipped_held_R_T_and_energy_tangents(inconsistent):
    m,x,_=initial(example());valve=m.controllers.gov[0][0];field=m.controllers.avr[0][1]
    x[valve]=.4;x[field]=1.5;m.controller_boundaries.active.update({valve:'upper',field:'upper'})
    u=np.random.default_rng(493).normal(size=len(x));v=m.electrical(x)[0];dv=np.full(len(v),.03+.04j);h=2e-6;cb=m.integration_jacobian()
    if inconsistent:
        v+=.03j;sys=NetworkDAE(m);f=m.rhs(0,x);actual=residual_product(sys,0,x,f,v,u,u,dv)
        expected=(sys.residual(0,x+h*u,f+h*u,v+h*dv)-sys.residual(0,x-h*u,f-h*u,v-h*dv))/(2*h)
    else:
        actual=cb.jacobian_actions(0,x,u[:,None],m.electrical(x))[:,0];expected=(m.rhs(0,x+h*u)-m.rhs(0,x-h*u))/(2*h)
        block=DAEBlocks(m,cb,0,x,16,lambda:None,lambda:None);np.testing.assert_allclose(block.action(u+1j*u),actual+1j*actual,atol=1e-9,rtol=1e-12)
        assert actual[valve]==actual[field]==0.
    np.testing.assert_allclose(actual,expected,atol=3e-6,rtol=3e-7)

@pytest.mark.parametrize('mode',['passport','equilibrium'])
def test_command_order_permutation(mode):
    a,b=[run_study(StudySpec.model_validate(example(mode=mode,reverse=reverse))) for reverse in [False,True]]
    assert a['completed'] and b['completed']
    for key in ['time_s','sample_side','event_log','channels']:assert a[key]==b[key]


def test_reference_held_states_and_historical_references():
    ref=StatorLimiterReference(example()).run();count=0
    for left,right,sol,c in ref.segments:
        for address,side in c['held'].items():
            target=ref.entries[address][1 if side=='lower' else 2]
            np.testing.assert_allclose([sol(t)[address] for t in np.linspace(left,right,7)],target,atol=1e-12,rtol=0);count+=1
    assert count>=4
    old=ref.sample(.1);ref.pref=999.;ref.vref=999.;assert old==ref.sample(.1)


def test_no_native_controller_stator_or_energy_helpers(monkeypatch):
    from power_grid_env.controller_boundaries import ControllerBoundaries
    import power_grid_env.generator_stator as stator
    import power_grid_env.generator_energy as energy
    def forbidden(*a,**k):raise AssertionError('native helper used')
    for key in ['guards','apply','rates','derivative']:monkeypatch.setattr(ControllerBoundaries,key,forbidden)
    monkeypatch.setattr(stator,'impedance',forbidden);monkeypatch.setattr(energy,'rotor_rates',forbidden)
    coverage(StatorLimiterReference(example()).run().events,'both')


def test_wrong_bound_is_rejected():
    s=example();report=run_study(StudySpec.model_validate(s));ref=StatorLimiterReference(s).run();wrong=copy.deepcopy(report)
    next(e for e in wrong['event_log'] if e['kind']=='controller_limit_enter')['bound']='WRONG'
    assert not compare(ref,wrong)['passed']
