import numpy as np
import pytest
from benchmarks.stator_trip_cases import example
from benchmarks.stator_trip_reference import StatorTripReference
from power_grid_env.study_spec import StudySpec,run_study
from tests.test_two_cage_winding import initial
from power_grid_env.dae import NetworkDAE,residual_product
from power_grid_env.rms_winding import operator

@pytest.mark.parametrize('controls',['governor','exciter','both'])
@pytest.mark.parametrize('mode',['passport','equilibrium'])
@pytest.mark.parametrize('method',['radau','network_dae'])
def test_independent_combined_trip_heat_and_energy(controls,mode,method):
    from benchmarks.stator_trip_acceptance import compare
    s=example(controls,mode);s['coupled']['integration_method']=method;r=run_study(StudySpec.model_validate(s));ref=StatorTripReference(s).run()
    assert r['completed'] and compare(ref,r)['passed']
    trips=[e for e in r['event_log'] if e['kind']=='generator_thermal_trip'];assert len(trips)==1
    assert trips[0]['time_s']<.1 if mode=='passport' else .1<trips[0]['time_s']<.2
    assert r['channels']['g.current_pu'][-1]==r['channels']['g.stator.copper_electrical_loss_w'][-1]==0.
    assert r['channels']['g.heat.core_loss_w'][-1]>0.

@pytest.mark.parametrize('inconsistent',[False,True])
def test_open_mode_variable_R_DAE_and_zero_injection(inconsistent):
    m,x,a=initial(example());m.generator_thermal.apply({0},0.,x,[]);m.factorize()
    base=operator(m,x)[0].toarray();y=x.copy();y[m.generator_heat.entries[0][0]]+=30.
    np.testing.assert_array_equal(operator(m,y)[0].toarray(),base)
    np.testing.assert_array_equal(m.network_injection(y),m.network_injection(x))
    np.testing.assert_array_equal(m.generator_currents(y,m.electrical(y)[0]),0.)
    u=np.random.default_rng(624).normal(size=len(x));v=m.electrical(x)[0];dv=np.full(len(v),.03+.04j);h=2e-6;cb=m.integration_jacobian()
    if inconsistent:
        v+=.03j;sys=NetworkDAE(m);f=m.rhs(0,x);actual=residual_product(sys,0,x,f,v,u,u,dv)
        expected=(sys.residual(0,x+h*u,f+h*u,v+h*dv)-sys.residual(0,x-h*u,f-h*u,v-h*dv))/(2*h)
    else:
        actual=cb.jacobian_actions(0,x,u[:,None],m.electrical(x))[:,0];expected=(m.rhs(0,x+h*u)-m.rhs(0,x-h*u))/(2*h)
    np.testing.assert_allclose(actual,expected,rtol=3e-7,atol=3e-6)


def test_posttrip_core_heat_and_temperature_closed_form_without_exciter():
    ref=StatorTripReference(example('governor','passport')).run();trip=next(e['time_s'] for e in ref.events if e['kind']=='generator_thermal_trip')
    initial=ref.sample(trip,'post_event');h=ref.heat;core=initial['g.heat.core_loss_w'];equilibrium=h.ambient_c+core/h.ambient_w_per_k
    for t in np.linspace(trip,.4,21):
        v=ref.sample(float(t),'post_event');expected=equilibrium+(initial['g.heat.temperature_c']-equilibrium)*np.exp(-h.ambient_w_per_k*(t-trip)/h.capacity_j_per_k)
        assert v['g.heat.temperature_c']==pytest.approx(expected,abs=1e-8)
        assert v['g.heat.copper_loss_w']==0. and v['g.heat.core_loss_w']==pytest.approx(core,abs=1e-8)
        assert v['g.stator_thermal_level']==pytest.approx(ref.replica.trip_level*np.exp(-(t-trip)/ref.replica.time_constant_s),abs=1e-8)


def test_reference_uses_no_native_hybrid_or_R_T_helpers(monkeypatch):
    import power_grid_env.generator_stator as stator
    import power_grid_env.generator_energy as energy
    from power_grid_env.generator_thermal import GeneratorThermalBank
    def forbidden(*a,**k):raise AssertionError('native helper used')
    for name in ['impedance','admittance']:monkeypatch.setattr(stator,name,forbidden)
    monkeypatch.setattr(energy,'rotor_rates',forbidden);monkeypatch.setattr(GeneratorThermalBank,'apply',forbidden)
    ref=StatorTripReference(example()).run();assert sum(e['kind']=='generator_thermal_trip' for e in ref.events)==1

@pytest.mark.parametrize('inconsistent',[False,True])
def test_connected_R_T_replica_cross_derivatives(inconsistent):
    m,x,_=initial(example());x=x.copy();x[m.generator_heat.entries[0][0]]+=2.
    u=np.random.default_rng(172).normal(size=len(x));v=m.electrical(x)[0];dv=np.full(len(v),.03+.04j);h=2e-6;cb=m.integration_jacobian()
    if inconsistent:
        v+=.03j;sys=NetworkDAE(m);f=m.rhs(0,x);actual=residual_product(sys,0,x,f,v,u,u,dv)
        expected=(sys.residual(0,x+h*u,f+h*u,v+h*dv)-sys.residual(0,x-h*u,f-h*u,v-h*dv))/(2*h)
    else:
        actual=cb.jacobian_actions(0,x,u[:,None],m.electrical(x))[:,0];expected=(m.rhs(0,x+h*u)-m.rhs(0,x-h*u))/(2*h)
    np.testing.assert_allclose(actual,expected,rtol=3e-7,atol=3e-6)
