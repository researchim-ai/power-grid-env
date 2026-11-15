"""Bounded fixed-field GENCLS / thermal motor coupling checks."""
import copy
import numpy as np
import pytest
from benchmarks.mixed_winding_cases import example
from tests.test_two_cage_winding import initial
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.dae import NetworkDAE,residual_product
from power_grid_env.dae_linearization import DAEBlocks

@pytest.mark.parametrize('inconsistent',[False,True])
def test_mixed_analytic_network_and_residual_tangents(inconsistent):
    m,x,_=initial(example());u=np.random.default_rng(117).normal(size=len(x));v=m.electrical(x)[0];dv=np.full(len(v),.03+.04j);h=2e-6;cb=m.integration_jacobian()
    if inconsistent:
        v=v+.03j
        actual=cb.dae_actions(0,x,u[:,None],m.electrical_at_voltage(x,v),voltage_directions=dv[:,None])[:,0]
        expected=(m.differential_at_voltage(0,x+h*u,v+h*dv)-m.differential_at_voltage(0,x-h*u,v-h*dv))/(2*h)
        system=NetworkDAE(m);f=m.rhs(0,x)
        np.testing.assert_allclose(residual_product(system,0,x,f,v,u,u,dv),(system.residual(0,x+h*u,f+h*u,v+h*dv)-system.residual(0,x-h*u,f-h*u,v-h*dv))/(2*h),rtol=3e-7,atol=3e-6)
    else:
        actual=cb.jacobian_actions(0,x,u[:,None],m.electrical(x))[:,0]
        expected=(m.rhs(0,x+h*u)-m.rhs(0,x-h*u))/(2*h)
        blocks=DAEBlocks(m,cb,0,x,16,lambda:None,lambda:None)
        np.testing.assert_allclose(blocks.action(u+1j*u),actual+1j*actual,rtol=1e-12,atol=1e-9)
    np.testing.assert_allclose(actual,expected,rtol=3e-7,atol=3e-6)

@pytest.mark.parametrize('kind',['missing_profile','governor'])
def test_mixed_profiles_fail_closed(kind):
    s=example()
    if kind=='missing_profile':s['coupled']['winding_generator_profile']=None
    elif kind=='thermal_equilibrium':s['coupled']['operating_point']['winding_state']='equilibrium'
    else:s['coupled']['machines'][0]['governor']=dict(R=.05,T1=.1,T2=.2,T3=1.,VMIN=0.,VMAX=1.,Dt=0.)
    report=run_study(StudySpec.model_validate(s))
    assert not report['completed'] and report['status']=='rejected' and report['reason']=='coupled_scope'

@pytest.mark.parametrize('method',['radau','network_dae'])
def test_mixed_independent_fault_trajectory(method):
    from benchmarks.mixed_winding_reference import MixedReference
    from benchmarks.mixed_winding_acceptance import compare
    s=example(fault=True);s['coupled']['integration_method']=method
    report=run_study(StudySpec.model_validate(s));reference=MixedReference(s).run()
    result=compare(reference,report)
    assert report['completed'] and result['passed'],result
    assert abs(report['channels']['g.omega'][-1]-1)>1e-4


def test_omitted_profile_preserves_legacy_identity():
    from tests.test_two_cage_winding import example as motor
    s=motor();assert 'winding_generator_profile' not in StudySpec.model_validate(s).model_dump(mode='json')['coupled']


def test_independent_reference_does_not_use_native_machine_or_initialization(monkeypatch):
    from benchmarks.mixed_winding_reference import MixedReference
    import power_grid_env.coupled_initialization as initialization
    from power_grid_env.coupled_rms import CoupledNetwork
    import power_grid_env.rms_winding as winding
    s=example();m,x,a=initial(s)
    def forbidden(*args,**kwargs):raise AssertionError('native helper reached independent reference')
    monkeypatch.setattr(initialization,'initialize_coupled',forbidden)
    monkeypatch.setattr(CoupledNetwork,'generator_internal',forbidden)
    monkeypatch.setattr(winding.RMSWindingBank,'actions',forbidden)
    ref=MixedReference(s).run()
    assert ref.initial['generator_mechanical_pu']==pytest.approx(a['generator_initial']['g']['mechanical_power_pu'],abs=1e-11)
    assert ref.initial['frozen_temperature_spectral_abscissa_per_s']==pytest.approx(a['connected_machine_spectral_abscissa_per_s'],abs=2e-5)
