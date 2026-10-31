"""IEEEST cascade and shared closed-loop machine/motor verification."""
from pathlib import Path
import numpy as np
import pytest
from scipy import signal
from scipy.integrate import solve_ivp
from power_grid_env.study_spec import StudySpec,run_study,run_study_document
from power_grid_env.coupled_pss import CoupledPSS
from benchmarks.coupled_pss_reference import reference,native_controlled_limit

NAMES=['hold','motor_load','fault','saturated_fault','classical_fault','voltage_gate','limits']


def example(name='hold'):
    return StudySpec.model_validate_json(Path('cases/studies/pss_coupled_'+name+'.json').read_text())


@pytest.mark.parametrize('name',NAMES)
def test_joint_pss_trajectory_matches_independent_SI_and_polynomial_transfer(name):
    s=example(name);r=run_study(s);assert r['completed'],r
    e=reference(s,r)
    for key,values in e['channels'].items():
        limit=100. if key.endswith(('.p_w','.q_var')) else 1e-5 if key.endswith(('.delta','.omega','.speed_pu')) else 1e-4
        assert max(abs(np.array(values)-r['channels'][key]))<limit,key
    assert r['additional_validation']['ok'] and r['additional_validation']['pss_limits']['g']['within_limits']
    assert r['maximum_power_balance_residual_pu']<1e-8


def test_seven_state_cascade_matches_frequency_transfer_and_gate():
    m=example().coupled.machines[0];p=m.stabilizer;pss=CoupledPSS([m],{0:slice(0,7)})
    t=np.linspace(0.,5.,5001);u=.01*np.sin(2*t)
    sol=solve_ivp(lambda time,x:pss.evaluate(x,np.array([1+.01*np.sin(2*time)]),np.array([1.]))[0][0],(0.,5.),np.zeros(7),t_eval=t,rtol=1e-10,atol=1e-12)
    assert sol.success
    num=np.polymul(np.polymul([p.A6,p.A5,1.],[p.T1,1.]),np.polymul([p.T3,1.],[p.KS*p.T5,0.]))
    den=np.polymul(np.polymul([p.A2,p.A1,1.],[p.A4,p.A3,1.]),np.polymul(np.polymul([p.T2,1.],[p.T4,1.]),[p.T6,1.]))
    _,expected,_=signal.lsim((num,den),U=u,T=t)
    actual=[pss.evaluate(x,np.array([1+v]),np.array([1.]))[2]['g.IEEEST.raw_pu'] for x,v in zip(sol.y.T,u)]
    assert max(abs(np.asarray(actual)-expected))<1e-7
    # Gate does not reset or freeze the filters, including at the strict boundaries.
    x=sol.y[:,-1]
    for voltage in [p.VCL,p.VCU,p.VCL/2,p.VCU*2]:
        derivatives,out,channels=pss.evaluate(x,np.array([1.01]),np.array([voltage]))
        assert out[0]==0. and channels['g.IEEEST.voltage_gate']==0.
        assert np.linalg.norm(derivatives[0])>0


@pytest.mark.parametrize('saturated',[False,True])
def test_controlled_generator_only_pss_limit_matches_ANDES(saturated):
    r=native_controlled_limit(saturated);assert r['passed'],r
    assert r['errors'][-1]['g.IEEEST.raw_pu']<1e-5


def test_voltage_gate_and_limits_observe_real_closed_loop_behavior():
    r=run_study(example('voltage_gate'));assert r['completed']
    c=r['channels'];blocked=np.asarray(c['g.IEEEST.voltage_gate'])==0
    assert blocked.any() and (~blocked).any()
    assert np.all(np.asarray(c['g.IEEEST.output_pu'])[blocked]==0)
    r=run_study(example('limits'));assert r['completed']
    assert max(abs(np.asarray(r['channels']['g.IEEEST.limited_pu'])))==pytest.approx(.001)
    assert max(abs(np.asarray(r['channels']['g.IEEEST.raw_pu'])))>.001


def test_zero_gain_is_closed_loop_equivalent_to_absent_pss():
    s=example('fault');s.coupled.machines[0].stabilizer.KS=0.;a=run_study(s)
    s.coupled.machines[0].stabilizer=None;b=run_study(s)
    assert a['completed'] and b['completed']
    for key in ['g.delta','g.omega','g.e1q','m.speed_pu','g.SEXS.field_pu']:
        assert max(abs(np.array(a['channels'][key])-b['channels'][key]))<1e-6
    assert max(abs(np.asarray(a['channels']['g.IEEEST.output_pu'])))==0.


def test_pss_dependency_mode_and_steady_boundary_gates():
    d=example().model_dump(mode='json');d['coupled']['machines'][0]['exciter']=None
    assert run_study_document(d)['reason']=='schema'
    d=example().model_dump(mode='json');d['coupled']['machines'][0]['stabilizer']['MODE']=2
    assert run_study_document(d)['reason']=='schema'
    s=example();s.coupled.machines[0].stabilizer.VCL=1.
    assert run_study(s)['reason']=='controller_boundary_operating_point'
    s=example();s.coupled.machines[0].stabilizer.LSMIN=0.
    assert run_study(s)['reason']=='controller_boundary_operating_point'


def test_mixed_optional_pss_layout_and_worker():
    from power_grid_env.build import NetBuilder
    from power_grid_env.rms_study import ClassicalMachine
    builder=NetBuilder('mixed-pss','Mixed PSS',10.,s_base_va=5e6)
    for n in ['grid','a','b']:builder.node(n)
    builder.source('grid',.1,.3);builder.line_z1('grid','a',1+3j,line_id='l1');builder.line_z1('a','b',.5+1j,line_id='l2')
    for node in ['a','b']:builder.generator(node,.2e6,mode='pv',v_set_pu=1.,q_min_var=-10e6,q_max_var=10e6,generator_id=node)
    s=example();s.network=builder.build();s.coupled.duration_s=.05;s.coupled.motors[0].node_id='b'
    m=s.coupled.machines[0];m.generator_id='b'
    a=ClassicalMachine(generator_id='a',rated_va=2e6,inertia_s=1.,x_d_prime_pu=.3,damping_pu=3.)
    s.coupled.machines=[m,a]
    r=run_study(s);w=run_study(s,isolate=True,wall_timeout_s=30.)
    assert r['completed'] and w['completed'] and r['channels']==w['channels']
    assert 'b.IEEEST.raw_pu' in r['channels'] and 'a.IEEEST.raw_pu' not in r['channels']
