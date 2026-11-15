import copy
import numpy as np
import pytest

from tests.test_rms_equilibrium import case
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.coupled_rms import CoupledNetwork
from power_grid_env.coupled_initialization import initialize_coupled
from power_grid_env.dae import NetworkDAE,residual_product
from power_grid_env.dae_linearization import DAEBlocks


def example(mode='equilibrium'):
    s=case();s['coupled']['motors'][0].update(model='double_cage',rotor2_r_pu=.06,rotor2_x_pu=.03,rotor_mutual_x_pu=.01)
    s['coupled']['operating_point']['winding_state']=mode
    return s


def initial(s):
    p=StudySpec.model_validate(s);m=CoupledNetwork(p.network,p.coupled);x,a=initialize_coupled(m)
    assert x is not None,a
    return m,x,a


@pytest.mark.parametrize('mode',['passport','equilibrium'])
def test_two_cage_initialization_and_true_copper_losses(mode):
    m,x,op=initial(example(mode));v,i,_,_=m.electrical(x);signals=m.windings.signals(x,i)
    assert signals['m.rotor_copper_loss_w']==pytest.approx(signals['m.rotor1_copper_loss_w']+signals['m.rotor2_copper_loss_w'],abs=1e-10)
    assert signals['m.rotor1_resistance_pu']/m.settings.motors[0].rotor_r_pu==pytest.approx(signals['m.rotor2_resistance_pu']/m.settings.motors[0].rotor2_r_pu)
    if mode=='equilibrium':
        assert max(abs(m.physical_rhs(0,x)))<1e-8
        assert len(op['physical_eigenvalues_per_s'])==8
        assert op['maximum_stationary_heat_error_w']<1e-5
    else:assert max(abs(m.physical_rhs(0,x)[m.windings.addresses[0]]))>1e-3


@pytest.mark.parametrize('inconsistent',[False,True])
def test_two_cage_analytic_tangents_and_DAE_blocks(inconsistent):
    m,x,_=initial(example());x=x.copy();x[m.cages.addresses[0]]+=[.02,-.01];x[m.windings.addresses[0]]+=[2.,-3.]
    u=np.random.default_rng(156).normal(size=len(x));v=m.electrical(x)[0];dv=np.array([.03+.04j]*len(v));h=2e-6;cb=m.integration_jacobian()
    if inconsistent:
        v=v+.03j
        actual=cb.dae_actions(0,x,u[:,None],m.electrical_at_voltage(x,v),voltage_directions=dv[:,None])[:,0]
        expected=(m.differential_at_voltage(0,x+h*u,v+h*dv)-m.differential_at_voltage(0,x-h*u,v-h*dv))/(2*h)
        f=m.rhs(0,x);system=NetworkDAE(m)
        np.testing.assert_allclose(residual_product(system,0,x,f,v,u,u,dv),(system.residual(0,x+h*u,f+h*u,v+h*dv)-system.residual(0,x-h*u,f-h*u,v-h*dv))/(2*h),rtol=3e-7,atol=3e-6)
    else:
        actual=cb.jacobian_actions(0,x,u[:,None],m.electrical(x))[:,0]
        expected=(m.rhs(0,x+h*u)-m.rhs(0,x-h*u))/(2*h)
        blocks=DAEBlocks(m,cb,0,x,16,lambda:None,lambda:None)
        np.testing.assert_allclose(blocks.action(u+1j*u),actual+1j*actual,rtol=1e-12,atol=1e-9)
    np.testing.assert_allclose(actual,expected,rtol=3e-7,atol=3e-6)


def test_analytic_reduced_stationary_Jacobian_with_two_cages(monkeypatch):
    import power_grid_env.rms_winding_equilibrium as module
    original=module.least_squares;checked=[]
    def audited(fun,x0,jac,**kw):
        actual=jac(x0);numeric=np.empty_like(actual)
        for i in range(len(x0)):
            h=2e-6*max(1,abs(x0[i]));a=x0.copy();b=x0.copy();a[i]+=h;b[i]-=h;numeric[:,i]=(fun(a)-fun(b))/(2*h)
        np.testing.assert_allclose(actual,numeric,rtol=2e-6,atol=3e-6);checked.append(True)
        return original(fun,x0,jac=jac,**kw)
    monkeypatch.setattr(module,'least_squares',audited);initial(example());assert checked


@pytest.mark.parametrize('method',['radau','network_dae'])
def test_stationary_hold_and_unique_channel_axes(method):
    s=example();s['coupled']['integration_method']=method;r=run_study(StudySpec.model_validate(s));assert r['completed']
    assert all(len(v)==len(r['time_s']) for v in r['channels'].values())
    for key in ['stator_temperature_c','rotor_temperature_c','speed_pu','cage1_e_real_pu']:
        assert max(abs(np.array(r['channels']['m.'+key])-r['channels']['m.'+key][0]))<1e-8


def test_identical_two_cages_reduce_to_equivalent_single_cage_with_heat():
    a=example();m=a['coupled']['motors'][0];m.update(rotor_mutual_x_pu=0.,rotor_r_pu=.03,rotor2_r_pu=.03,rotor_x_pu=.12,rotor2_x_pu=.12)
    a['coupled'].update(duration_s=.12,faults=[dict(id='f',node_id=m['node_id'],apply_s=.04,clear_s=.08,r_ohm=.1,x_ohm=.05)])
    b=copy.deepcopy(a);m=b['coupled']['motors'][0];m.update(model='single_cage',rotor_r_pu=.015,rotor_x_pu=.06,rotor2_r_pu=None,rotor2_x_pu=None)
    results=[run_study(StudySpec.model_validate(s)) for s in [a,b]]
    assert all(r['completed'] for r in results)
    assert results[0]['time_s']==results[1]['time_s']
    for k in ['speed_pu','e_real_pu','e_imag_pu','current_pu','stator_temperature_c','rotor_temperature_c','rotor_copper_loss_w']:
        np.testing.assert_allclose(results[0]['channels']['m.'+k],results[1]['channels']['m.'+k],atol=1e-5,rtol=1e-7)


def test_cage_label_swap_preserves_terminal_and_thermal_solution():
    a=example();b=copy.deepcopy(a);m=b['coupled']['motors'][0]
    m['rotor_r_pu'],m['rotor2_r_pu']=m['rotor2_r_pu'],m['rotor_r_pu'];m['rotor_x_pu'],m['rotor2_x_pu']=m['rotor2_x_pu'],m['rotor_x_pu']
    ma,x,_=initial(a);mb,y,_=initial(b)
    ca=ma.windings.signals(x,ma.electrical(x)[1]);cb=mb.windings.signals(y,mb.electrical(y)[1])
    np.testing.assert_allclose(x[:3],y[:3],rtol=1e-9,atol=1e-9)
    np.testing.assert_allclose(x[ma.windings.addresses[0]],y[mb.windings.addresses[0]],rtol=1e-9,atol=1e-7)
    assert ca['m.rotor1_copper_loss_w']==pytest.approx(cb['m.rotor2_copper_loss_w'],abs=1e-6)


def test_mixed_single_two_cage_group_keeps_physical_equilibrium():
    s=example();other=case()['coupled']['motors'][0];other['id']='single';s['coupled']['motors'].append(other)
    m,x,info=initial(s);assert max(abs(m.physical_rhs(0,x)))<1e-8
    assert len(info['physical_eigenvalues_per_s'])==14


@pytest.mark.parametrize('mode',['cold','passport','equilibrium'])
def test_independent_two_flux_reference_through_fault_and_copper_heat(mode):
    from benchmarks.two_cage_winding_reference import TwoCageReference
    from benchmarks.rms_winding_acceptance import compare
    s=example('passport' if mode=='cold' else mode)
    if mode=='cold':s['coupled']['operating_point']=None
    s['coupled'].update(duration_s=.12,faults=[dict(id='fault',node_id='n1',apply_s=.04,clear_s=.08,r_ohm=.1,x_ohm=.05)])
    r=run_study(StudySpec.model_validate(s));a=TwoCageReference(s).run()
    checks=compare(a,r);assert checks['passed'],{k:v for k,v in checks.items() if k!='values'}


def test_two_cage_without_I2_replica_still_has_physical_thermal_equilibrium():
    s=example();s['coupled']['motors'][0]['thermal']=None
    s['coupled']['operating_point']['thermal_state']='passport'
    m,x,a=initial(s);assert len(a['physical_eigenvalues_per_s'])==7
    assert max(abs(m.physical_rhs(0,x)))<1e-8


def test_independent_reference_never_uses_native_cage_matrices(monkeypatch):
    from benchmarks.two_cage_winding_reference import TwoCageReference
    from benchmarks.rms_winding_acceptance import compare
    import power_grid_env.motor_cages as cages
    import power_grid_env.rms_winding_equilibrium as equilibrium
    from power_grid_env.rms_winding import RMSWindingBank
    s=example();r=run_study(StudySpec.model_validate(s))
    def forbidden(*a,**kw):raise AssertionError('native cage/winding implementation used by reference')
    monkeypatch.setattr(cages,'double_cage_coefficients',forbidden)
    monkeypatch.setattr(equilibrium,'initialize_electrothermal',forbidden)
    for name in ['rotor_values','derivatives','actions','cage_steady']:monkeypatch.setattr(RMSWindingBank,name,forbidden)
    assert compare(TwoCageReference(s).run(),r)['passed']


def test_RMS_transfer_cannot_silently_discard_winding_temperature_physics():
    from power_grid_env.motor_transfer import transfer_passport
    from tests.test_motor_transfer import forward,reverse
    m=transfer_passport(reverse())['target']
    m['winding_thermal']=copy.deepcopy(example()['coupled']['motors'][0]['winding_thermal'])
    p=forward(m)
    with pytest.raises(ValueError,match='omission acknowledgement'):transfer_passport(p)
    p['acknowledged_omissions']=['winding_thermal']
    result=transfer_passport(p)
    assert result['omitted_properties']==['winding_thermal']
    assert not result['transient_equivalence_claimed']
