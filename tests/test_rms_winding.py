import copy
from pathlib import Path

import numpy as np
import pytest

from power_grid_env.study_artifacts import read_json
from power_grid_env.study_spec import StudySpec, run_study
from power_grid_env.coupled_rms import CoupledNetwork
from power_grid_env.coupled_initialization import initialize_coupled
from power_grid_env.dae import NetworkDAE, residual_product
from power_grid_env.dae_linearization import DAEBlocks
from power_grid_env.rms_winding import operator

ROOT=Path(__file__).resolve().parents[1]


def example(kind='primary-uv'):
    s=read_json(ROOT/f'cases/motor-only-loaded/protected/{kind}.json')
    s['coupled']['motors'][0]['winding_thermal']=dict(stator_capacity_j_per_k=3000.,rotor_capacity_j_per_k=2000.,
        stator_ambient_w_per_k=10.,rotor_ambient_w_per_k=8.,cross_w_per_k=2.,
        initial_temperature_c=[90.,70.],provenance='owner://rms-winding/tests')
    return s


def model_state():
    s=StudySpec.model_validate(example());m=CoupledNetwork(s.network,s.coupled)
    x,info=initialize_coupled(m);assert x is not None
    return m,x,info


def test_loaded_hot_initialization_is_consistent_but_not_thermal_equilibrium():
    m,x,info=model_state()
    a=m.windings.addresses[0]
    assert list(x[a])==[90.,70.]
    assert info['checked_dynamic_residual_per_s']<1e-8
    assert info['winding_stability_scope'].startswith('connected electrical')
    assert max(abs(m.physical_rhs(0,x)[a]))>1e-3
    assert max(abs(NetworkDAE(m).consistent(0,x).derivative[a]))>1e-3


@pytest.mark.parametrize('supplied_voltage', [False,True])
def test_exact_tangents_with_temperature_dependent_stamp_and_rotor_rate(supplied_voltage):
    m,x,_=model_state();x=x.copy();x[:3]+=[.01,-.015,-.02];x[m.windings.addresses[0]]+=[3.,-2.]
    rng=np.random.default_rng(719);u=rng.normal(size=len(x));v=m.electrical(x)[0]
    dv=rng.normal(size=len(v))+1j*rng.normal(size=len(v))
    cb=m.integration_jacobian();h=2e-6
    if supplied_voltage:
        v=v+(.03+.02j)
        actual=cb.dae_actions(.1,x,u[:,None],m.electrical_at_voltage(x,v),voltage_directions=dv[:,None])[:,0]
        expected=(m.differential_at_voltage(.1,x+h*u,v+h*dv)-m.differential_at_voltage(.1,x-h*u,v-h*dv))/(2*h)
    else:
        actual=cb.jacobian_actions(.1,x,u[:,None],m.electrical(x))[:,0]
        expected=(m.rhs(.1,x+h*u)-m.rhs(.1,x-h*u))/(2*h)
    np.testing.assert_allclose(actual,expected,rtol=2e-7,atol=2e-6)


def test_full_DAE_residual_tangent_at_inconsistent_voltage_and_schur_blocks():
    m,x,_=model_state();system=NetworkDAE(m);rng=np.random.default_rng(56)
    u=rng.normal(size=len(x));udot=rng.normal(size=len(x));v=m.electrical(x)[0]+(.05-.02j)
    dv=rng.normal(size=len(v))+1j*rng.normal(size=len(v));f=m.physical_rhs(0,x);h=2e-6
    actual=residual_product(system,.1,x,f,v,u,udot,dv)
    expected=(system.residual(.1,x+h*u,f+h*udot,v+h*dv)-system.residual(.1,x-h*u,f-h*udot,v-h*dv))/(2*h)
    np.testing.assert_allclose(actual,expected,rtol=3e-7,atol=3e-6)
    blocks=DAEBlocks(m,m.integration_jacobian(),0,x,16,lambda:None,lambda:None)
    expected=m.integration_jacobian().jacobian_actions(0,x,u[:,None],m.electrical(x))[:,0]
    np.testing.assert_allclose(blocks.action(u),expected,rtol=1e-12,atol=1e-10)
    np.testing.assert_allclose(blocks.action(u+1j*u),expected+1j*expected,rtol=1e-12,atol=1e-10)


def test_matrix_cache_survives_temperature_rollback_without_changing_discrete_mode():
    m,x,_=model_state();system=NetworkDAE(m)
    a=operator(m,x)[0].toarray();initial=system.project(x)
    hot=x.copy();hot[m.windings.addresses[0]]+=[20.,10.]
    assert np.max(abs(operator(m,hot)[0].toarray()-a))>1e-4
    assert np.max(abs(system.project(hot)-initial))>1e-5
    np.testing.assert_array_equal(operator(m,x)[0].toarray(),a)
    np.testing.assert_allclose(system.project(x),initial,atol=1e-14)


@pytest.mark.parametrize('method',['radau','network_dae'])
@pytest.mark.parametrize('kind',['primary-uv','breaker_failure-uv','measurement_loss-uv','primary-no-uv'])
def test_temperature_feedback_with_active_protection_and_independent_winding_reference(method,kind):
    from benchmarks.rms_winding_reference import ElectrothermalReference
    from benchmarks.rms_winding_acceptance import compare
    s=example(kind);s['coupled']['integration_method']=method
    report=run_study(StudySpec.model_validate(s));assert report['completed']
    ref=ElectrothermalReference(s).run()
    checks=compare(ref,report)
    assert checks['passed'],{k:v for k,v in checks.items() if k!='values'}
    assert report['channels']['m.stator_temperature_c'][-1]!=90.
    assert ref.sample(.6)['m.connected']==float(kind=='primary-no-uv')


def test_no_winding_passport_keeps_existing_canonical_schema():
    s=example();s['coupled']['motors'][0].pop('winding_thermal')
    assert 'winding_thermal' not in StudySpec.model_validate(s).coupled.motors[0].model_dump(mode='json')


@pytest.mark.parametrize('change',['incomplete_double_cage','cached_ports','invalid_temperature','missing_provenance'])
def test_unsupported_or_invalid_profiles_fail_explicitly(change):
    s=example();m=s['coupled']['motors'][0]
    if change=='incomplete_double_cage':m.update(model='double_cage',rotor2_r_pu=.1)
    elif change=='cached_ports':s['coupled'].update(jacobian_network='cached_ports',integration_method='radau')
    elif change=='invalid_temperature':m['winding_thermal']['initial_temperature_c']=[210.,70.]
    else:m['winding_thermal'].pop('provenance')
    if change=='cached_ports':assert not run_study(StudySpec.model_validate(s))['completed']
    else:
        with pytest.raises(ValueError):StudySpec.model_validate(s)


def test_multimotor_local_temperature_blocks_and_sparse_schur_action():
    s=example();second=copy.deepcopy(s['coupled']['motors'][0]);second['id']='m2';second['winding_thermal']['initial_temperature_c']=[60.,50.]
    s['coupled']['motors'].append(second)
    spec=StudySpec.model_validate(s);m=CoupledNetwork(spec.network,spec.coupled);x,info=initialize_coupled(m)
    assert x is not None
    direction=np.random.default_rng(89).normal(size=len(x));h=2e-6
    cb=m.integration_jacobian();actual=cb.jacobian_actions(0,x,direction[:,None],m.electrical(x))[:,0]
    expected=(m.rhs(0,x+h*direction)-m.rhs(0,x-h*direction))/(2*h)
    np.testing.assert_allclose(actual,expected,rtol=3e-7,atol=3e-6)
    blocks=DAEBlocks(m,cb,0,x,16,lambda:None,lambda:None)
    np.testing.assert_allclose(blocks.action(direction),actual,rtol=1e-12,atol=1e-10)


def test_reference_is_independent_and_heat_channel_corruption_is_detected(monkeypatch):
    from benchmarks.rms_winding_reference import ElectrothermalReference
    from benchmarks.rms_winding_acceptance import compare
    from power_grid_env.rms_winding import RMSWindingBank
    s=example();report=run_study(StudySpec.model_validate(s))
    def forbidden(*a,**k):raise AssertionError('native electrothermal RHS used by reference')
    for name in ['derivatives','parameters','actions','operator','injection']:
        monkeypatch.setattr(RMSWindingBank,name,forbidden)
    monkeypatch.setattr(CoupledNetwork,'physical_rhs',forbidden)
    monkeypatch.setattr(CoupledNetwork,'static_electrical',forbidden)
    ref=ElectrothermalReference(s).run()
    assert compare(ref,report)['passed']
    report=copy.deepcopy(report);report['channels']['m.stator_net_heat_w'][10]+=1.
    assert not compare(ref,report)['passed']


@pytest.mark.parametrize('method',['radau','network_dae'])
def test_leaving_temperature_validity_is_structured_failure_not_extrapolation(method):
    s=example();s['coupled']['integration_method']=method
    s['coupled']['motors'][0]['winding_thermal'].update(initial_temperature_c=[20.,20.],maximum_c=20.001,stator_capacity_j_per_k=1.,rotor_capacity_j_per_k=1.)
    result=run_study(StudySpec.model_validate(s))
    assert not result['completed']
    # solve_ivp-stage validity failures use the existing numerical-failure
    # envelope; the exact model-validity cause must remain visible.
    assert result['status']=='numerically_not_converged'
    assert result['solver_message']=='RMS winding temperature outside passport validity'
    assert result['runtime_budget']['rhs_evaluations']>0
    assert 'channels' not in result
