import numpy as np
import pytest
from benchmarks.generator_heat_cases import example
from tests.test_two_cage_winding import initial
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.dae import NetworkDAE,residual_product
from power_grid_env.dae_linearization import DAEBlocks

@pytest.mark.parametrize('inconsistent',[False,True])
def test_physical_heat_DAE_tangents(inconsistent):
    m,x,a=initial(example());u=np.random.default_rng(324).normal(size=len(x));v=m.electrical(x)[0];dv=np.full(len(v),.03+.04j);h=2e-6;cb=m.integration_jacobian()
    if inconsistent:
        v=v+.03j;system=NetworkDAE(m);f=m.rhs(0,x)
        actual=residual_product(system,0,x,f,v,u,u,dv);expected=(system.residual(0,x+h*u,f+h*u,v+h*dv)-system.residual(0,x-h*u,f-h*u,v-h*dv))/(2*h)
    else:
        actual=cb.jacobian_actions(0,x,u[:,None],m.electrical(x))[:,0];expected=(m.rhs(0,x+h*u)-m.rhs(0,x-h*u))/(2*h)
        block=DAEBlocks(m,cb,0,x,16,lambda:None,lambda:None)
        np.testing.assert_allclose(block.action(u+1j*u),actual+1j*actual,rtol=1e-12,atol=1e-9)
    np.testing.assert_allclose(actual,expected,rtol=3e-7,atol=3e-6)
    assert len(a['physical_eigenvalues_per_s'])==15

@pytest.mark.parametrize('method',['radau','network_dae'])
@pytest.mark.parametrize('mode',['passport','equilibrium'])
def test_independent_heat_and_transition(method,mode):
    from benchmarks.generator_heat_reference import GeneratorHeatReference
    from benchmarks.mixed_winding_acceptance import compare
    s=example(mode,True);s['coupled']['integration_method']=method;r=run_study(StudySpec.model_validate(s));ref=GeneratorHeatReference(s).run()
    assert r['completed'] and compare(ref,r)['passed']
    assert r['generator_heat_model']['electrical_or_shaft_loss_feedback'] is False
    key='full_physical_spectral_abscissa_per_s' if mode=='equilibrium' else 'connected_machine_spectral_abscissa_per_s'
    oracle='controlled_spectral_abscissa_per_s' if mode=='equilibrium' else 'frozen_heat_excluded_spectral_abscissa_per_s'
    assert r['operating_point'][key]==pytest.approx(ref.initial[oracle],abs=2e-6)

@pytest.mark.parametrize('kind',['policy','runaway','ceiling','flag'])
def test_invalid_heat_equilibrium_has_diagnostic(kind):
    s=example();h=s['coupled']['generator_heat'][0]
    if kind=='policy':h['initialization']='specified'
    elif kind=='runaway':h['ambient_w_per_k']=.01
    elif kind=='ceiling':h.update(maximum_c=30.,initial_c=20.)
    else:s['coupled']['winding_generator_profile']='GENCLS_TGOV1_SEXS'
    r=run_study(StudySpec.model_validate(s));assert not r['completed']


def test_capacity_changes_pole_not_equilibrium():
    s=example();m,x,a=initial(s);s['coupled']['generator_heat'][0]['capacity_j_per_k']*=2
    n,y,b=initial(s);np.testing.assert_allclose(x,y,atol=1e-10,rtol=1e-10)
    assert a['full_physical_spectral_abscissa_per_s']==pytest.approx(2*b['full_physical_spectral_abscissa_per_s'],rel=1e-7)


def test_reference_uses_no_native_heat_helpers(monkeypatch):
    from power_grid_env.generator_heat import GeneratorHeatBank
    from benchmarks.generator_heat_reference import GeneratorHeatReference
    def forbidden(*a,**k):raise AssertionError('native heat helper used')
    for name in ['initialize','terms','derivatives','actions']:monkeypatch.setattr(GeneratorHeatBank,name,forbidden)
    r=GeneratorHeatReference(example()).run();assert 20.<r.initial['generator_temperature_c']<180.


def test_constant_input_closed_form_temperature_and_energy_balance():
    from scipy.integrate import solve_ivp,quad
    s=example('passport');m,x,_=initial(s);i,(a,p)=next(iter(m.generator_heat.entries.items()))
    voltage=m.electrical(x)[0];current=m.generator_currents(x,voltage);terminal=m.generator_terminal_voltage(x,voltage)
    ib=m.gen_passports[i].rated_va/(np.sqrt(3)*m.generators[i].ll_v);q=3*p.resistance_ohm*abs(current[i]*ib)**2
    k=p.ambient_w_per_k-q*p.alpha_per_c;equilibrium=(q*(1-p.alpha_per_c*p.reference_c)+p.core_loss_w*abs(terminal[i])**2+p.ambient_w_per_k*p.ambient_c)/k
    initial_t=x[a]
    def rate(time,t):
        state=x.copy();state[a]=t[0];return m.generator_heat.derivatives(state,current,terminal)
    solution=solve_ivp(rate,(0,600),[initial_t],rtol=1e-11,atol=1e-12,dense_output=True)
    expected=lambda t:equilibrium+(initial_t-equilibrium)*np.exp(-k*t/p.capacity_j_per_k)
    np.testing.assert_allclose(solution.sol(np.linspace(0,600,25))[0],[expected(t) for t in np.linspace(0,600,25)],rtol=1e-10,atol=1e-9)
    energy=quad(lambda t:-k*(expected(t)-equilibrium),0,600,epsabs=1e-7)[0]
    assert p.capacity_j_per_k*(expected(600)-initial_t)==pytest.approx(energy,abs=1e-6)
