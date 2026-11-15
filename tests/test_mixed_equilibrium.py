import copy
import numpy as np
import pytest
from benchmarks.mixed_equilibrium_cases import example
from tests.test_two_cage_winding import initial
from power_grid_env.study_spec import StudySpec,run_study


def test_analytic_reduced_PV_heat_Jacobian(monkeypatch):
    import power_grid_env.mixed_winding_equilibrium as module
    original=module.least_squares;checked=[]
    def audit(fun,x0,jac,**kw):
        actual=jac(x0);numeric=np.empty_like(actual)
        for i in range(len(x0)):
            h=2e-6*max(1.,abs(x0[i]));a=x0.copy();b=x0.copy();a[i]+=h;b[i]-=h;numeric[:,i]=(fun(a)-fun(b))/(2*h)
        np.testing.assert_allclose(actual,numeric,atol=3e-6,rtol=2e-6);checked.append(True)
        return original(fun,x0,jac=jac,**kw)
    monkeypatch.setattr(module,'least_squares',audit)
    m,x,a=initial(example());assert checked
    assert max(abs(m.physical_rhs(0,x)))<1e-8
    assert a['maximum_stationary_heat_error_w']<1e-5
    assert len(a['physical_eigenvalues_per_s'])==10


@pytest.mark.parametrize('method',['radau','network_dae'])
@pytest.mark.parametrize('fault',[False,True])
def test_independent_joint_equilibrium_and_trajectory(method,fault):
    from benchmarks.mixed_equilibrium_reference import MixedEquilibriumReference
    from benchmarks.mixed_winding_acceptance import compare
    s=example(fault=fault);s['coupled']['integration_method']=method
    r=run_study(StudySpec.model_validate(s));ref=MixedEquilibriumReference(s).run()
    assert r['completed'] and compare(ref,r)['passed']
    a=r['operating_point'];b=ref.initial
    assert a['full_physical_spectral_abscissa_per_s']==pytest.approx(b['full_physical_spectral_abscissa_per_s'],abs=1e-7)
    for key in ['stator_temperature_c','rotor_temperature_c','speed_pu']:
        assert a['motor_initial']['m'][key]==pytest.approx(b[key],abs=1e-8)
        if not fault:assert np.ptp(r['channels']['m.'+key])<1e-8


def test_heat_root_does_not_depend_on_seed_or_capacity():
    a=example();b=example(temperatures=(20.,20.));h=b['coupled']['motors'][0]['winding_thermal'];h['stator_capacity_j_per_k']*=100.;h['rotor_capacity_j_per_k']*=200.
    ma,x,aa=initial(a);mb,y,ab=initial(b)
    np.testing.assert_allclose(x,y,rtol=1e-10,atol=1e-8)
    assert aa['full_physical_spectral_abscissa_per_s']!=pytest.approx(ab['full_physical_spectral_abscissa_per_s'],abs=1e-5)


@pytest.mark.parametrize('kind',['cooling','ceiling','q_limit','relay_policy'])
def test_impossible_equilibrium_has_diagnostic(kind):
    s=example();h=s['coupled']['motors'][0]['winding_thermal']
    if kind=='cooling':h.update(stator_ambient_w_per_k=0.,rotor_ambient_w_per_k=0.,cross_w_per_k=0.)
    elif kind=='ceiling':h.update(maximum_c=30.,initial_temperature_c=[20.,20.],stator_capacity_j_per_k=1e16,rotor_capacity_j_per_k=1e16)
    elif kind=='q_limit':s['network']['generators'][0].update(q_min_var=-1.,q_max_var=1.)
    else:s['coupled']['operating_point']['thermal_state']='passport'
    r=run_study(StudySpec.model_validate(s));assert not r['completed']
    assert 'equilibrium' in str(r) or 'q_limit' in str(r) or 'thermal' in str(r)


def test_independent_root_forbids_native_helpers(monkeypatch):
    from benchmarks.mixed_equilibrium_reference import MixedEquilibriumReference
    import power_grid_env.mixed_winding_equilibrium as module
    from power_grid_env.rms_winding import RMSWindingBank
    def forbidden(*a,**k):raise AssertionError('native numerical helper used')
    monkeypatch.setattr(module,'initialize_mixed_electrothermal',forbidden)
    monkeypatch.setattr(RMSWindingBank,'cage_steady',forbidden)
    monkeypatch.setattr(RMSWindingBank,'actions',forbidden)
    ref=MixedEquilibriumReference(example()).run();assert ref.initial['thermal_equilibrium']


@pytest.mark.parametrize('method',['radau','network_dae'])
def test_five_second_equilibrium_hold(method):
    s=example();s['coupled'].update(duration_s=5.,step_s=.01,integration_method=method)
    r=run_study(StudySpec.model_validate(s));assert r['completed']
    for key in ['m.speed_pu','m.stator_temperature_c','m.rotor_temperature_c','g.omega','g.delta','g.p_w']:
        assert np.ptp(r['channels'][key])<(1e-5 if key.endswith('_w') else 1e-8)


def test_single_and_double_motor_groups_share_PV_heat_initialization():
    s=example();other=copy.deepcopy(s['coupled']['motors'][0]);other.update(id='m2',model='single_cage',rated_va=5000.,rotor2_r_pu=None,rotor2_x_pu=None,rotor_mutual_x_pu=0.)
    s['coupled']['motors'].append(other);m,x,a=initial(s)
    assert len(a['motor_initial'])==2 and a['generator_constraints']==2
    assert max(abs(m.physical_rhs(0,x)))<1e-8
    assert a['maximum_stationary_heat_error_w']<1e-5
