import numpy as np
import pytest
from benchmarks.network_automation_reference import example,reference,compare,NAMES
from power_grid_env.study_spec import run_study
from power_grid_env.network_automation import NetworkAutomation
from power_grid_env.coupled_rms import CoupledNetwork,CoupledEvent
from power_grid_env.coupled_initialization import initialize_coupled
from power_grid_env.dae import NetworkDAE,residual_product
from power_grid_env.dae_linearization import DAEBlocks

@pytest.mark.parametrize('name',NAMES)
def test_DAE_automation_matches_independent_SI_and_recorded_physical_events(name):
    spec=example(name);native=reference(name,spec)
    spec.coupled.integration_method='network_dae';spec.coupled.jacobian_network='direct_solve';spec.coupled.jacobian_format='csc'
    actual=run_study(spec);assert actual['completed'],actual
    assert compare(actual,native)['passed']
    assert actual['integration']['jacobian_assembly']=='analytic_Schur_products'
    assert actual['maximum_kcl_residual_pu']<1e-8 and actual['maximum_power_balance_residual_pu']<1e-8


def model_at_fault(curve='very_inverse',port='line'):
    spec=example('overcurrent');spec.coupled.jacobian_network='direct_solve'
    d=spec.coupled.network_automation.model_dump();d['relays'][0].update(curve=curve,measurement_kind=port,measurement_id={'line':'feeder','source':'source','generator':'g'}[port],pickup=1.)
    spec.coupled.network_automation=NetworkAutomation.model_validate(d)
    model=CoupledNetwork(spec.network,spec.coupled);state,info=initialize_coupled(model,require_stable=False);assert state is not None,info
    from types import SimpleNamespace
    model.apply_additional_event(SimpleNamespace(kind='fault_apply',equipment_id='fault'),time=.1,state=state,event_log=[])
    model.factorize();model.automation.initialize(state)
    assert model.automation.active[0]
    return model,state

@pytest.mark.parametrize('curve',['definite','standard_inverse','very_inverse','extremely_inverse'])
@pytest.mark.parametrize('port',['line','source','generator'])
def test_piecewise_analytic_relay_actions_and_nonlocal_DAE_blocks(curve,port):
    m,x=model_at_fault(curve,port);cb=m.integration_jacobian();rng=np.random.default_rng(12)
    u=rng.normal(size=(len(x),2))*.02;eps=1e-7;ctx=m.electrical(x)
    exact=cb.jacobian_actions(.1,x,u,ctx)
    for j in range(2):
        fd=(m.rhs(.1,x+eps*u[:,j])-m.rhs(.1,x-eps*u[:,j]))/(2*eps)
        assert np.allclose(exact[:,j],fd,rtol=3e-6,atol=3e-5)
    blocks=DAEBlocks(m,cb,.1,x,32,lambda:None,lambda:None)
    assert np.allclose(blocks.action(u[:,0]+1j*u[:,1]),exact[:,0]+1j*exact[:,1],rtol=1e-10,atol=1e-8)
    system=NetworkDAE(m);v=ctx[0]*(.99+.002j);dot=m.differential_at_voltage(.1,x,v)
    dv=(rng.normal(size=len(v))+1j*rng.normal(size=len(v)))*.01
    action=residual_product(system,.1,x,dot,v,u[:,0],u[:,1],dv)
    fd=(system.residual(.1,x+eps*u[:,0],dot+eps*u[:,1],v+eps*dv)-system.residual(.1,x-eps*u[:,0],dot-eps*u[:,1],v-eps*dv))/(2*eps)
    assert np.allclose(action,fd,rtol=4e-6,atol=5e-5)


def test_relay_branch_change_invalidates_DAE_context():
    m,x=model_at_fault();system=NetworkDAE(m);m.automation.active[0]=False
    with pytest.raises(ValueError,match='context changed'):system.project(x)


def test_reset_latched_and_measurement_lost_derivatives_are_piecewise_exact():
    m,x=model_at_fault();bank=m.automation;x[bank.offset]=.4
    for active,tripped,available in [(False,False,True),(True,True,True),(True,False,False)]:
        bank.active[0]=active;bank.tripped[0]=tripped;bank.available['oc']=available
        u=np.zeros((len(x),1));u[bank.offset]=1.
        a=m.integration_jacobian().jacobian_actions(.1,x,u,m.electrical(x))
        expected=-1/bank.relays[0].reset_s if not active and not tripped else 0.
        assert a[bank.offset,0]==expected


def test_two_live_islands_and_independent_reconnections_preserve_local_equilibria():
    from power_grid_env.build import NetBuilder
    from power_grid_env.induction import InductionMotor
    from power_grid_env.coupled_rms import CoupledSettings
    from power_grid_env.rms_study import ClassicalMachine
    from power_grid_env.study_spec import StudySpec
    b=NetBuilder('two-islands','authored two independently supplied islands',10.,s_base_va=1e6);b.node('grid');b.source('grid',2.,8.)
    motors=[];machines=[];breakers=[]
    for i in [1,2]:
        node=f'p{i}';b.node(node);b.line_z1('grid',node,.2+1j,line_id=f'l{i}')
        b.load(node,300000,100000,model='constant_z',load_id=f'load{i}')
        b.generator(node,300000,mode='pv',v_set_pu=1.,q_min_var=-1e6,q_max_var=1e6,generator_id=f'g{i}')
        machines.append(ClassicalMachine(generator_id=f'g{i}',rated_va=1e6,inertia_s=1.,damping_pu=1.,x_d_prime_pu=.25))
        breakers.append(dict(id=f'cb{i}',target_kind='line',target_id=f'l{i}'))
    motors=[InductionMotor(id='m',node_id='p1',rated_va=1e5,rated_ll_v=10000.,stator_r_pu=.02,stator_x_pu=.1,rotor_r_pu=.015,rotor_x_pu=.1,magnetizing_x_pu=3.,inertia_s=.5,fan_torque_pu=.6,connected=False)]
    events=[CoupledEvent(time_s=t,kind=kind,equipment_id=id_) for t,kind,id_ in [(.1,'breaker_open','cb1'),(.15,'breaker_open','cb2'),(.4,'breaker_close','cb1'),(.5,'breaker_close','cb2')]]
    s=StudySpec(id='two-islands',study='coupled_rms',backend='scipy',network=b.build(),coupled=CoupledSettings(motors=motors,machines=machines,duration_s=.6,step_s=.005,events=events,
        network_automation=NetworkAutomation(breakers=breakers),event_policy='coincident_roots_v1',integration_method='network_dae',jacobian_network='direct_solve',jacobian_format='csc',rtol=1e-9,atol=1e-11))
    a=run_study(s);assert a['completed'],a
    for id_ in ['g1','g2']:assert max(abs(np.array(a['channels'][id_+'.omega'])-1))<1e-8
    operations=[e for e in a['event_log'] if e['kind'] in {'breaker_open','breaker_close'} and e.get('outcome')=='applied']
    assert [e['time_s'] for e in operations]==pytest.approx([.1,.15,.4,.5],abs=1e-10)
    s.coupled.integration_method='radau';s.coupled.jacobian_network='cached_ports';b=run_study(s);assert b['completed'],b
    for id_ in ['g1','g2']:
        assert a['channels'][id_+'.omega']==pytest.approx(b['channels'][id_+'.omega'],abs=1e-8)
    assert a['maximum_kcl_residual_pu']<1e-8


@pytest.mark.parametrize('name',['classical','round','pss','esst','thermal','oel','batch'])
def test_automation_keeps_existing_machine_and_controller_analytic_families(name):
    from tests.test_sparse_jacobian import profile
    from power_grid_env.network_automation import NetworkAutomationBank
    m,old=profile(name);p=NetworkAutomation(breakers=[dict(id='cb',target_kind='source',target_id=m.network.sources[0].id)],
        relays=[dict(id='oc',kind='overcurrent',measurement_kind='generator',measurement_id=m.generators[0].id,breaker_id='cb',pickup=.001,curve='very_inverse',time_multiplier_s=.02)])
    m.settings.network_automation=p;m.settings.jacobian_network='direct_solve';m.automation=NetworkAutomationBank(m,m.takeover_oel.end)
    m.dynamic_end=m.automation.end;x=np.r_[old,np.zeros(m.automation.end-m.automation.offset)]
    m.factorize();m.automation.initialize(x);cb=m.integration_jacobian();assert cb is not None
    rng=np.random.default_rng(31);u=rng.normal(size=(len(x),2))*.001
    action=cb.jacobian_actions(0.,x,u,m.electrical(x));blocks=DAEBlocks(m,cb,0.,x,32,lambda:None,lambda:None)
    assert np.allclose(blocks.action(u[:,0]+1j*u[:,1]),action[:,0]+1j*action[:,1],rtol=1e-9,atol=1e-7)


@pytest.mark.parametrize('retry',[False,True])
def test_rejected_DAE_linear_step_does_not_consume_timer_or_breaker_action(monkeypatch,retry):
    from power_grid_env.dae_solver import NetworkRadau,ShiftedSystem,LinearFailure,statistics
    from power_grid_env.dae_options import DAEOptions
    m,x=model_at_fault();x[m.automation.offset]=.4;m.automation.enqueue(.2,'open','line-cb','test-pending')
    original=x.copy();flags=(list(m.automation.active),list(m.automation.tripped),list(m.automation.queue))
    stats=statistics();solver=NetworkRadau(m.rhs,.1,x,.100001,model=m,callback=m.integration_jacobian(),options=DAEOptions(maximum_linear_retries=2 if retry else 0),stats=stats,check_time=lambda:None,first_step=1e-6,rtol=1e-9,atol=1e-11)
    solve=ShiftedSystem.solve;calls=0
    def injected(self,rhs):
        nonlocal calls;calls+=1
        if calls==1:raise LinearFailure('injected unsolved linear stage')
        return solve(self,rhs)
    monkeypatch.setattr(ShiftedSystem,'solve',injected);solver.step()
    assert (list(m.automation.active),list(m.automation.tripped),list(m.automation.queue))==flags
    assert np.array_equal(x,original) and stats['linear_retries']==1
    if retry:assert solver.t>.1 and solver.status!='failed'
    else:assert solver.t==.1 and solver.status=='failed' and np.array_equal(solver.y,original)


@pytest.mark.parametrize('change',['initialization','line_position','source_position'])
def test_manual_discrete_changes_also_invalidate_saved_mode(change):
    m,x=model_at_fault();system=NetworkDAE(m)
    if change=='initialization':m.automation.initialized=False
    elif change=='line_position':m.lines.closed['feeder']=False
    else:m.source_connected=False
    with pytest.raises(ValueError,match='context changed'):system.project(x)
