import copy
import numpy as np
import pytest

from tests.test_rms_winding import example
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.coupled_rms import CoupledNetwork
from power_grid_env.coupled_initialization import initialize_coupled
from power_grid_env.dae import NetworkDAE


def case():
    s=example();c=s['coupled'];c['operating_point'].update(winding_state='equilibrium',thermal_state='equilibrium')
    c['motors'][0]['winding_thermal'].update(stator_ambient_w_per_k=30.,rotor_ambient_w_per_k=24.,cross_w_per_k=6.,maximum_c=180.)
    c.update(faults=[],events=[],duration_s=.05)
    return s


def initial(s):
    spec=StudySpec.model_validate(s);m=CoupledNetwork(spec.network,spec.coupled);x,info=initialize_coupled(m)
    return m,x,info


def test_joint_stationary_balance_and_full_physical_stability():
    m,x,info=initial(case());assert x is not None,info
    assert info['mode']=='motor_only_electrothermal_equilibrium'
    assert info['analytic_reduced_jacobian']
    assert max(abs(m.physical_rhs(0,x)))<1e-8
    assert info['full_physical_spectral_abscissa_per_s']<0
    assert len(info['physical_eigenvalues_per_s'])==m.motor_state_end
    consistency=NetworkDAE(m).consistent(0,x)
    assert max(abs(consistency.derivative))<1e-8
    assert consistency.maximum_kcl_pu<1e-10
    assert not np.allclose(x[m.windings.addresses[0]],[90.,70.])


@pytest.mark.parametrize('method',['radau','network_dae'])
def test_stationary_hold_has_no_thermal_or_electrical_drift(method):
    s=case();s['coupled']['integration_method']=method
    report=run_study(StudySpec.model_validate(s));assert report['completed']
    assert report['winding_thermal_model']['initial_temperature_policy'].startswith('simultaneous_stationary')
    for key in ['speed_pu','e_real_pu','e_imag_pu','thermal_level','stator_temperature_c','rotor_temperature_c']:
        v=report['channels']['m.'+key];assert max(abs(np.array(v)-v[0]))<1e-8


@pytest.mark.parametrize('count',[1,2,4])
def test_search_seeds_do_not_impose_physical_temperature_and_multiple_motors_balance(count):
    s=case();s['coupled']['motors']=[dict(copy.deepcopy(s['coupled']['motors'][0]),id='m'+str(i)) for i in range(count)]
    first,x,a=initial(s);assert x is not None,a
    for motor in s['coupled']['motors']:motor['winding_thermal']['initial_temperature_c']=[20.,20.]
    second,y,b=initial(s);assert y is not None,b
    np.testing.assert_allclose(x,y,atol=1e-7,rtol=1e-9)
    assert max(abs(second.physical_rhs(0,y)))<1e-8
    assert b['full_physical_spectral_abscissa_per_s']<0


@pytest.mark.parametrize('cross',[0.,6.])
def test_no_load_synchronous_limit_is_supported(cross):
    s=case();s['coupled']['motors'][0]['fan_torque_pu']=0.
    s['coupled']['motors'][0]['winding_thermal']['cross_w_per_k']=cross
    m,x,info=initial(s);assert x is not None,info
    assert x[2]==1.
    assert max(abs(m.physical_rhs(0,x)))<1e-8


@pytest.mark.parametrize('bad,reason',[
    ('cooling','underdetermined_winding_thermal_network'),
    ('passport','electrothermal_equilibrium_requires_relay_replica_equilibrium'),
    ('disconnected','electrothermal_equilibrium_requires_1_to_32_connected_single_cage_thermal_motors'),
    ('missing','electrothermal_equilibrium_requires_1_to_32_connected_single_cage_thermal_motors'),
    ('ceiling','no_qualified_electrothermal_equilibrium'),
    ('stall','no_qualified_electrothermal_equilibrium')])
def test_invalid_or_unreachable_equilibria_fail_before_time_integration(bad,reason):
    s=case();m=s['coupled']['motors'][0];h=m['winding_thermal']
    if bad=='cooling':h.update(stator_ambient_w_per_k=0.,rotor_ambient_w_per_k=0.,cross_w_per_k=0.)
    elif bad=='passport':s['coupled']['operating_point']['thermal_state']='passport'
    elif bad=='disconnected':m['connected']=False
    elif bad=='missing':m.pop('winding_thermal')
    elif bad=='ceiling':h.update(maximum_c=30.,initial_temperature_c=[20.,20.])
    else:m['fan_torque_pu']=1000.
    result=run_study(StudySpec.model_validate(s))
    assert not result['completed']
    assert result['reason']==reason,result
    assert 'time_s' not in result


def test_default_operating_point_serialization_stays_unchanged():
    from power_grid_env.induction import MotorOperatingPoint
    assert 'winding_state' not in MotorOperatingPoint().model_dump(mode='json')


@pytest.mark.parametrize('count',[1,2])
def test_reduced_stationary_jacobian_matches_finite_differences(monkeypatch,count):
    import power_grid_env.rms_winding_equilibrium as module
    original=module.least_squares;checks=[]
    def audited(fun,x0,jac,**kwargs):
        actual=jac(x0);numeric=np.empty_like(actual)
        for i in range(len(x0)):
            step=2e-6*max(1.,abs(x0[i]));plus=x0.copy();minus=x0.copy();plus[i]+=step;minus[i]-=step
            numeric[:,i]=(fun(plus)-fun(minus))/(2*step)
        np.testing.assert_allclose(actual,numeric,rtol=2e-6,atol=3e-6)
        checks.append(True)
        return original(fun,x0,jac=jac,**kwargs)
    monkeypatch.setattr(module,'least_squares',audited)
    s=case();s['coupled']['motors']=[dict(copy.deepcopy(s['coupled']['motors'][0]),id='m'+str(i)) for i in range(count)]
    m,x,info=initial(s);assert x is not None,info
    assert checks


def test_independent_stationary_root_and_dynamics_do_not_call_native_initializer(monkeypatch):
    from benchmarks.rms_equilibrium_reference import StationaryReference
    from benchmarks.rms_winding_acceptance import compare
    import power_grid_env.rms_winding_equilibrium as module
    from power_grid_env.rms_winding import RMSWindingBank
    s=case();report=run_study(StudySpec.model_validate(s))
    def forbidden(*a,**kw):raise AssertionError('native equilibrium/thermal physics called by independent reference')
    monkeypatch.setattr(module,'initialize_electrothermal',forbidden)
    for name in ['derivatives','parameters','actions','operator']:monkeypatch.setattr(RMSWindingBank,name,forbidden)
    ref=StationaryReference(s).run()
    assert compare(ref,report)['passed']
    assert max(abs(np.array(ref.initial['eigenvalues_per_s'])[:,0]))>0
    native=report['operating_point']['motor_initial']['m']
    assert native['stator_temperature_c']==pytest.approx(ref.initial['stator_temperature_c'],abs=1e-7)


def test_stationary_winding_model_does_not_require_an_I2_relay():
    s=case();s['coupled']['motors'][0]['thermal']=None
    s['coupled']['operating_point']['thermal_state']='passport'
    m,x,info=initial(s);assert x is not None,info
    assert len(info['physical_eigenvalues_per_s'])==5
    assert max(abs(m.physical_rhs(0,x)))<1e-8


def test_fixed_resistance_limit_matches_closed_form_passive_heat_network():
    s=case();s['coupled']['motors'][0]['winding_thermal']['alpha_per_c']=0.
    m,x,info=initial(s);assert x is not None,info
    signals=m.windings.signals(x,m.electrical(x)[1]);h=m.settings.motors[0].winding_thermal
    matrix=np.array([[h.stator_ambient_w_per_k+h.cross_w_per_k,-h.cross_w_per_k],[-h.cross_w_per_k,h.rotor_ambient_w_per_k+h.cross_w_per_k]])
    expected=h.ambient_c+np.linalg.solve(matrix,[signals['m.stator_copper_loss_w'],signals['m.rotor_copper_loss_w']])
    np.testing.assert_allclose(x[m.windings.addresses[0]],expected,atol=1e-9,rtol=0)


def test_stationary_current_above_relay_limit_is_not_approved():
    s=case();s['coupled']['motors'][0]['thermal']['trip_level']=.1
    result=run_study(StudySpec.model_validate(s))
    assert not result['completed']
    assert result['reason']=='initial_thermal_relay_limit'


def test_heat_capacities_change_time_scales_not_the_stationary_solution():
    s=case();m,x,a=initial(s);assert x is not None,a
    h=s['coupled']['motors'][0]['winding_thermal']
    h['stator_capacity_j_per_k']*=1e9;h['rotor_capacity_j_per_k']*=1e9
    spec=StudySpec.model_validate(s);other=CoupledNetwork(spec.network,spec.coupled)
    y,b=initialize_coupled(other,require_stable=False)
    assert y is not None,b
    np.testing.assert_allclose(x,y,atol=1e-6,rtol=1e-9)
    assert b['maximum_stationary_heat_error_w']<1e-5
