import numpy as np
import pytest
from benchmarks.generator_stator_cases import example
from tests.test_two_cage_winding import initial
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.dae import NetworkDAE,residual_product
from power_grid_env.dae_linearization import DAEBlocks

@pytest.mark.parametrize('inconsistent',[False,True])
def test_stator_variable_Y_DAE(inconsistent):
    m,x,a=initial(example());x=x.copy();x[m.gen_offset+1]+=.01;x[m.generator_heat.entries[0][0]]+=2.
    u=np.random.default_rng(653).normal(size=len(x));v=m.electrical(x)[0];dv=np.full(len(v),.03+.04j);h=2e-6;cb=m.integration_jacobian()
    if inconsistent:
        v+=.03j;system=NetworkDAE(m);f=m.rhs(0,x);actual=residual_product(system,0,x,f,v,u,u,dv)
        expected=(system.residual(0,x+h*u,f+h*u,v+h*dv)-system.residual(0,x-h*u,f-h*u,v-h*dv))/(2*h)
    else:
        actual=cb.jacobian_actions(0,x,u[:,None],m.electrical(x))[:,0];expected=(m.rhs(0,x+h*u)-m.rhs(0,x-h*u))/(2*h)
        block=DAEBlocks(m,cb,0,x,16,lambda:None,lambda:None);np.testing.assert_allclose(block.action(u+1j*u),actual+1j*actual,rtol=1e-12,atol=1e-9)
    np.testing.assert_allclose(actual,expected,rtol=3e-7,atol=3e-6)


def test_joint_stator_reduced_analytic_Jacobian(monkeypatch):
    import power_grid_env.stator_equilibrium as module
    original=module.least_squares;count=[]
    def audited(fun,x0,jac,**kw):
        actual=jac(x0);numeric=np.empty_like(actual)
        for i in range(len(x0)):
            h=2e-6*max(1.,abs(x0[i]));hi=x0.copy();lo=x0.copy();hi[i]+=h;lo[i]-=h;numeric[:,i]=(fun(hi)-fun(lo))/(2*h)
        np.testing.assert_allclose(actual,numeric,rtol=2e-6,atol=3e-6);count.append(True)
        return original(fun,x0,jac=jac,**kw)
    monkeypatch.setattr(module,'least_squares',audited);m,x,a=initial(example());assert count and max(abs(m.physical_rhs(0,x)))<1e-8


def test_hot_temperature_changes_electrical_operator_and_real_power_drop():
    from power_grid_env.rms_winding import operator
    from power_grid_env.generator_stator import impedance
    m,x,a=initial(example());cold=operator(m,x)[0].copy();y=x.copy();y[m.generator_heat.entries[0][0]]+=30.
    hot=operator(m,y)[0];assert np.max(abs((hot-cold).data))>1e-4
    v=m.electrical(y)[0];ig=m.generator_currents(y,v);eg=m.generator_internal(y);rated=m.gen_passports[0].rated_va
    loss=((eg-v[m.gen_buses])*np.conj(ig)).real*rated
    heat=m.generator_heat.signals(y,ig,m.generator_terminal_voltage(y,v))
    assert loss[0]==pytest.approx(heat['g.heat.copper_loss_w'],abs=1e-8)
    assert impedance(m,y)[0].real>impedance(m,x)[0].real


def test_zero_loss_stationary_boundary():
    s=example();s['coupled']['generator_heat'][0].update(resistance_ohm=0.,core_loss_w=0.)
    m,x,a=initial(s);assert x[m.generator_heat.entries[0][0]]==20.
    assert a['generator_initial']['g']['mechanical_power_pu']==pytest.approx(.2,abs=1e-10)

@pytest.mark.parametrize('mode',['passport','equilibrium'])
@pytest.mark.parametrize('method',['radau','network_dae'])
def test_independent_stator_and_energy_trajectory(mode,method):
    from benchmarks.generator_stator_reference import GeneratorStatorReference
    from benchmarks.generator_stator_acceptance import compare
    s=example(mode,True);s['coupled']['integration_method']=method;r=run_study(StudySpec.model_validate(s));ref=GeneratorStatorReference(s).run()
    assert r['completed'] and compare(ref,r)['passed']
    assert max(abs(v) for v in r['channels']['g.stator.power_balance_w'])<1e-7
    assert r['generator_heat_model']['electrical_resistance_stamp'] is True


def test_no_native_stator_or_joint_helpers_in_reference(monkeypatch):
    import power_grid_env.generator_stator as module
    import power_grid_env.stator_equilibrium as initialization
    from benchmarks.generator_stator_reference import GeneratorStatorReference
    def forbidden(*a,**k):raise AssertionError('native helper used')
    for name in ['impedance','admittance','impedance_product']:monkeypatch.setattr(module,name,forbidden)
    monkeypatch.setattr(initialization,'initialize_stator_electrothermal',forbidden)
    ref=GeneratorStatorReference(example()).run();assert ref.initial['generator_mechanical_pu']>.2


@pytest.mark.parametrize('kind',['ceiling','missing_heat','policy'])
def test_infeasible_joint_or_inconsistent_policy_rejected(kind):
    s=example()
    if kind=='ceiling':s['coupled']['generator_heat'][0].update(maximum_c=30.,initial_c=20.)
    elif kind=='missing_heat':s['coupled']['generator_heat']=[]
    else:s['coupled']['operating_point']['winding_state']='passport'
    r=run_study(StudySpec.model_validate(s));assert not r['completed']
