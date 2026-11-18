import numpy as np
import pytest
from benchmarks.generator_energy_cases import example
from tests.test_two_cage_winding import initial
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.dae import NetworkDAE,residual_product
from power_grid_env.dae_linearization import DAEBlocks

@pytest.mark.parametrize('inconsistent',[False,True])
def test_energy_DAE_tangents(inconsistent):
    m,x,a=initial(example());x=x.copy();x[m.gen_offset+1]+=.01
    u=np.random.default_rng(542).normal(size=len(x));v=m.electrical(x)[0];dv=np.full(len(v),.03+.04j);h=2e-6;cb=m.integration_jacobian()
    if inconsistent:
        v=v+.03j;system=NetworkDAE(m);f=m.rhs(0,x)
        actual=residual_product(system,0,x,f,v,u,u,dv);expected=(system.residual(0,x+h*u,f+h*u,v+h*dv)-system.residual(0,x-h*u,f-h*u,v-h*dv))/(2*h)
    else:
        actual=cb.jacobian_actions(0,x,u[:,None],m.electrical(x))[:,0];expected=(m.rhs(0,x+h*u)-m.rhs(0,x-h*u))/(2*h)
        block=DAEBlocks(m,cb,0,x,16,lambda:None,lambda:None)
        np.testing.assert_allclose(block.action(u+1j*u),actual+1j*actual,rtol=1e-12,atol=1e-9)
    np.testing.assert_allclose(actual,expected,rtol=3e-7,atol=3e-6)

@pytest.mark.parametrize('method',['radau','network_dae'])
@pytest.mark.parametrize('mode',['passport','equilibrium'])
def test_independent_energy_trajectory_and_integral(method,mode):
    from benchmarks.generator_energy_reference import GeneratorEnergyReference
    from benchmarks.generator_energy_acceptance import compare
    s=example(mode,True);s['coupled']['integration_method']=method;r=run_study(StudySpec.model_validate(s));ref=GeneratorEnergyReference(s).run()
    assert r['completed'] and compare(ref,r)['passed']
    from scipy.integrate import quad
    def energy(t):
        q=ref.sample(t);return q['g.energy.kinetic_energy_j']+q['g.energy.thermal_energy_j']
    def power(t):
        q=ref.sample(t);return q['g.energy.shaft_power_w']-q['g.energy.terminal_power_w']-q['g.energy.signed_damping_exchange_w']-q['g.energy.ambient_heat_w']
    value=sum(quad(power,l,h,epsabs=1e-5)[0] for l,h in [(0,.1),(.1,.2),(.2,.4)])
    assert energy(.4)-energy(0)==pytest.approx(value,abs=1e-4)
    assert max(abs(v) for v in r['channels']['g.energy.balance_residual_w'])<1e-7


def test_lossless_stationary_limit():
    s=example();s['coupled']['generator_heat'][0].update(resistance_ohm=0.,core_loss_w=0.)
    m,x,a=initial(s);assert a['generator_initial']['g']['mechanical_power_pu']==pytest.approx(.2,abs=1e-10)
    assert max(abs(m.physical_rhs(0,x)))<1e-8


def test_energy_profile_requires_heat_per_generator():
    s=example();s['coupled']['generator_heat']=[];r=run_study(StudySpec.model_validate(s));assert not r['completed']


def test_no_double_debit_in_stationary_shaft_power():
    m,x,a=initial(example());voltage=m.electrical(x)[0];ig=m.generator_currents(x,voltage);terminal=m.generator_terminal_voltage(x,voltage)
    heat=m.generator_heat.signals(x,ig,terminal)
    expected=.2+(heat['g.heat.copper_loss_w']+heat['g.heat.core_loss_w'])/100000.
    assert a['generator_initial']['g']['mechanical_power_pu']==pytest.approx(expected,abs=1e-11)


def test_reference_calls_no_native_energy_helpers(monkeypatch):
    import power_grid_env.generator_energy as module
    from benchmarks.generator_energy_reference import GeneratorEnergyReference
    def forbidden(*a,**k):raise AssertionError('native energy helper used')
    for name in ['losses','initialize_power','rotor_rates','actions','signals']:monkeypatch.setattr(module,name,forbidden)
    r=GeneratorEnergyReference(example()).run();assert r.initial['generator_mechanical_pu']>.2


def test_nonpositive_rotor_speed_refused():
    m,x,_=initial(example());x[m.gen_offset+1]=0.
    with pytest.raises(ValueError,match='positive rotor speed'):m.physical_rhs(0,x)
