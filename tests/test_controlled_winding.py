import numpy as np
import pytest
from benchmarks.controlled_winding_cases import example
from tests.test_two_cage_winding import initial
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.dae import NetworkDAE,residual_product
from power_grid_env.dae_linearization import DAEBlocks


@pytest.mark.parametrize('controls',['governor','exciter','both'])
@pytest.mark.parametrize('inconsistent',[False,True])
def test_controlled_winding_analytic_DAE_actions(controls,inconsistent):
    m,x,a=initial(example(controls));u=np.random.default_rng(734).normal(size=len(x));v=m.electrical(x)[0];dv=np.full(len(v),.03+.04j);h=2e-6;cb=m.integration_jacobian()
    if inconsistent:
        v=v+.03j
        actual=cb.dae_actions(0,x,u[:,None],m.electrical_at_voltage(x,v),voltage_directions=dv[:,None])[:,0]
        expected=(m.differential_at_voltage(0,x+h*u,v+h*dv)-m.differential_at_voltage(0,x-h*u,v-h*dv))/(2*h)
        system=NetworkDAE(m);f=m.rhs(0,x)
        np.testing.assert_allclose(residual_product(system,0,x,f,v,u,u,dv),(system.residual(0,x+h*u,f+h*u,v+h*dv)-system.residual(0,x-h*u,f-h*u,v-h*dv))/(2*h),rtol=3e-7,atol=3e-6)
    else:
        actual=cb.jacobian_actions(0,x,u[:,None],m.electrical(x))[:,0];expected=(m.rhs(0,x+h*u)-m.rhs(0,x-h*u))/(2*h)
        blocks=DAEBlocks(m,cb,0,x,16,lambda:None,lambda:None)
        np.testing.assert_allclose(blocks.action(u+1j*u),actual+1j*actual,rtol=1e-12,atol=1e-9)
    np.testing.assert_allclose(actual,expected,rtol=3e-7,atol=3e-6)
    assert max(abs(m.physical_rhs(0,x)))<1e-8


@pytest.mark.parametrize('held',[False,True])
def test_controller_clip_and_held_rows(held):
    m,x,_=initial(example());valve=m.controllers.gov[0][0];field=m.controllers.avr[0][1]
    x[valve]=1.3;x[field]=5.1
    if held:m.controller_boundaries.active.update({valve:'upper',field:'upper'})
    u=np.random.default_rng(4).normal(size=len(x));h=2e-6
    analytic=m.integration_jacobian().jacobian_actions(0,x,u[:,None],m.electrical(x))[:,0]
    numeric=(m.rhs(0,x+h*u)-m.rhs(0,x-h*u))/(2*h)
    np.testing.assert_allclose(analytic,numeric,rtol=3e-7,atol=3e-6)
    if held:assert analytic[valve]==analytic[field]==0.


@pytest.mark.parametrize('method',['radau','network_dae'])
@pytest.mark.parametrize('mode',['passport','equilibrium'])
def test_controlled_fault_matches_independent_reference(method,mode):
    from benchmarks.controlled_winding_reference import ControlledReference
    from benchmarks.mixed_winding_acceptance import compare
    s=example(fault=True,mode=mode);s['coupled']['integration_method']=method;r=run_study(StudySpec.model_validate(s));ref=ControlledReference(s).run()
    assert r['completed'] and compare(ref,r)['passed']
    key='full_physical_spectral_abscissa_per_s' if mode=='equilibrium' else 'connected_machine_spectral_abscissa_per_s'
    assert r['operating_point'][key]==pytest.approx(ref.initial['controlled_spectral_abscissa_per_s'],abs=2e-6)


@pytest.mark.parametrize('kind',['governor_limit','exciter_limit','missing_profile'])
def test_controller_initialization_refuses_unsupported_or_limited_root(kind):
    s=example()
    if kind=='governor_limit':s['coupled']['machines'][0]['governor']['VMAX']=.1
    elif kind=='exciter_limit':s['coupled']['machines'][0]['exciter']['EMAX']=1.
    else:s['coupled']['winding_generator_profile']='GENCLS_fixed_field'
    r=run_study(StudySpec.model_validate(s));assert not r['completed']
    assert 'limit' in str(r) or r['reason']=='coupled_scope'


def test_reference_never_calls_native_controller_or_heat_initialization(monkeypatch):
    from benchmarks.controlled_winding_reference import ControlledReference
    from power_grid_env.coupled_controls import CoupledControllerBank
    import power_grid_env.mixed_winding_equilibrium as module
    def forbidden(*a,**k):raise AssertionError('native helper used')
    for name in ['initialize','derivatives','field','mechanical']:monkeypatch.setattr(CoupledControllerBank,name,forbidden)
    monkeypatch.setattr(module,'initialize_mixed_electrothermal',forbidden)
    ref=ControlledReference(example()).run();assert ref.initial['controlled_spectral_abscissa_per_s']<0.
