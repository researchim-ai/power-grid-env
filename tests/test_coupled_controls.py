"""Joint governor/exciter dynamics, limiter recovery and independent validation."""
from pathlib import Path
import json
import math
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from power_grid_env.study_spec import StudySpec,run_study,run_study_document
from power_grid_env.coupled_controls import CoupledControllerBank
from power_grid_env.coupled_rms import CoupledEvent
from power_grid_env.rms_study import ClassicalMachine
from power_grid_env.rms_controls import TGOV1Passport,SEXSPassport
from benchmarks.regulated_coupled_reference import reference,native_controlled_limit


def example(name='hold'):
    return StudySpec.model_validate_json(Path('cases/studies/regulated_coupled_'+name+'.json').read_text())


def bank_fixture():
    machine=ClassicalMachine(generator_id='g',rated_va=2e6,inertia_s=1.,x_d_prime_pu=.3,
        governor=TGOV1Passport(R=.05,T1=.1,T2=.05,T3=.3,VMIN=0.,VMAX=.3,Dt=0.),
        exciter=SEXSPassport(TATB=1.,TB=.5,K=10.,TE=.1,EMIN=.2,EMAX=1.2))
    bank=CoupledControllerBank([machine],0);state=np.zeros(bank.end)
    assert bank.initialize(state,np.array([1.]),np.array([1.]),np.array([.2]),1e-8) is None
    return bank,state


def test_governor_component_matches_closed_form_capped_lag_and_turbine_filter():
    bank,state=bank_fixture();bank.pref[0]=.4
    times=np.linspace(0.,.4,201)
    sol=solve_ivp(lambda t,x:bank.derivatives(x,np.array([1.]),np.array([1.])),(0.,.4),state,
                  method='Radau',rtol=1e-10,atol=1e-12,t_eval=times)
    assert sol.success
    hit=.1*math.log(2.)
    valve=np.minimum(.3,.4-.2*np.exp(-times/.1))
    def lag_before(t):return .4-.2*np.exp(-t/.3)-.2*.1/(.1-.3)*(np.exp(-t/.1)-np.exp(-t/.3))
    lag=np.array([lag_before(t) if t<=hit else .3+(lag_before(hit)-.3)*math.exp(-(t-hit)/.3) for t in times])
    assert sol.y[0]==pytest.approx(valve,abs=1e-7)
    assert sol.y[1]==pytest.approx(lag,abs=1e-7)
    expected=.05/.3*valve+(1-.05/.3)*lag
    actual=[bank.mechanical(x,np.array([1.]),np.array([.2]))[0] for x in sol.y.T]
    assert actual==pytest.approx(expected,abs=1e-7)


def test_exciter_component_hits_ceiling_and_recovers_as_exact_exponential():
    bank,state=bank_fixture()
    first=solve_ivp(lambda t,x:bank.derivatives(x,np.array([1.]),np.array([.95])),(0.,.4),state,
                    method='Radau',rtol=1e-10,atol=1e-12,dense_output=True)
    second=solve_ivp(lambda t,x:bank.derivatives(x,np.array([1.]),np.array([1.])),(.4,.8),first.y[:,-1],
                     method='Radau',rtol=1e-10,atol=1e-12,dense_output=True)
    assert first.success and second.success
    times=np.linspace(0.,.8,161)
    actual=[bank.field(first.sol(t) if t<=.4 else second.sol(t),np.ones(1))[0] for t in times]
    expected=[min(1.2,1.5-.5*math.exp(-t/.1)) if t<=.4 else 1.+.2*math.exp(-(t-.4)/.1) for t in times]
    assert actual==pytest.approx(expected,abs=1e-7)


@pytest.mark.parametrize('name',['hold','motor_load','voltage_sag','setpoint_steps','limits_recovery','thermal_trip'])
def test_joint_regulated_trajectory_matches_independent_SI_and_transfer_functions(name):
    s=example(name);r=run_study(s);assert r['completed'],r
    expected=reference(s,r)
    for key,values in expected['channels'].items():
        limit=100. if key.endswith(('.p_w','.q_var')) else 1e-5 if key.endswith(('.omega','.speed_pu','.delta')) else 1e-4
        assert max(abs(np.array(values)-r['channels'][key]))<limit,key
    assert r['additional_validation']['ok']
    assert r['maximum_power_balance_residual_pu']<1e-8
    actual_trip=next((e['time_s'] for e in r['event_log'] if e['kind']=='thermal_trip'),None)
    if name=='thermal_trip':assert actual_trip==pytest.approx(expected['trip_time_s'],abs=1e-6)
    else:assert actual_trip is expected['trip_time_s'] is None


def test_coupled_limiters_are_bounded_and_leave_limits_after_reference_returns():
    r=run_study(example('limits_recovery'));assert r['completed'],r
    limits=r['additional_validation']['controller_limits']
    for key,cap in [('g.TGOV1.valve_pu',.3),('g.SEXS.field_pu',1.2)]:
        assert max(r['channels'][key])==pytest.approx(cap,abs=1e-8)
        assert r['channels'][key][-1]<cap-.01
        assert limits[key]['maximum_raw_state_overshoot']<=limits[key]['state_overshoot_tolerance']
        assert limits[key]['within_limits']
    for time in [.2,.6]:
        before,after=np.flatnonzero(np.array(r['time_s'])==time)
        for key in ['g.delta','g.omega','g.TGOV1.valve_state_pu','g.SEXS.field_state_pu','m.speed_pu']:
            assert r['channels'][key][before]==r['channels'][key][after]


def test_saturated_output_does_not_hide_unacceptable_raw_state_overshoot():
    bank,state=bank_fixture()
    signals=bank.signals(state,np.array([1.]),np.array([1.]),np.array([.2]))
    channels={key:[value] for key,value in signals.items()}
    wrong=state.copy();wrong[bank.avr[0][1]]=1.21
    bank.observe_accepted_states(wrong[:,None])
    checked=bank.validate_channels(channels,1e-7,1e-9)
    assert not checked['ok']
    assert checked['controller_limits']['g.SEXS.field_pu']['maximum_raw_state_overshoot']>=.009


def test_controllers_react_to_motor_load_and_system_base_is_not_controller_base():
    s=example('motor_load');a=run_study(s);assert a['completed']
    assert np.ptp(a['channels']['g.TGOV1.output_pu'])>1e-4
    assert np.ptp(a['channels']['g.SEXS.field_pu'])>1e-4
    fixed=s.model_copy(deep=True);fixed.coupled.machines[0].governor=None;fixed.coupled.machines[0].exciter=None
    unregulated=run_study(fixed);assert unregulated['completed']
    assert max(abs(np.array(a['channels']['g.omega'])-unregulated['channels']['g.omega']))>1e-7
    s.network.s_base_va=3e6;b=run_study(s);assert b['completed'],b
    for key in ['g.omega','g.delta','g.TGOV1.output_pu','g.SEXS.field_pu','m.speed_pu']:
        assert a['channels'][key]==pytest.approx(b['channels'][key],abs=2e-6)


def test_native_ANDES_disconnected_motor_controller_limit_agrees():
    result=native_controlled_limit()
    assert result['passed'],result
    assert result['errors'][-1]['g.delta']<result['errors'][0]['g.delta']


def test_invalid_control_routing_and_initial_boundaries_reject_before_TDS(monkeypatch):
    import power_grid_env.induction_network as core
    monkeypatch.setattr(core,'solve_ivp',lambda *a,**k:pytest.fail('must reject before integration'))
    s=example();s.coupled.events=[CoupledEvent(time_s=.2,kind='generator_mechanical_step',equipment_id='g',value=.1)]
    assert run_study(s)['reason']=='coupled_scope'
    s=example();s.coupled.machines[0].governor=None;s.coupled.events=[CoupledEvent(time_s=.2,kind='governor_step',equipment_id='g',value=.1)]
    assert run_study(s)['reason']=='coupled_scope'
    s=example();s.coupled.machines[0].exciter=None;s.coupled.events=[CoupledEvent(time_s=.2,kind='voltage_reference_step',equipment_id='g',value=.01)]
    assert run_study(s)['reason']=='coupled_scope'
    s=example();s.coupled.machines[0].governor.VMAX=.2
    assert run_study(s)['reason']=='initial_governor_limit_or_boundary'
    s=example();s.coupled.machines[0].exciter.EMAX=1.
    assert run_study(s)['reason']=='initial_exciter_limit_or_boundary'
    payload=example().model_dump(mode='json');payload['coupled']['machines'][0]['exciter']['TE']=0.
    assert run_study_document(payload)['reason']=='schema'


def test_mixed_governor_and_exciter_generator_layout_and_optional_controllers():
    from power_grid_env.build import NetBuilder
    builder=NetBuilder('regulated-multi','Regulated multiple generators',10.,s_base_va=5e6)
    for node in ['grid','first','second']:builder.node(node)
    builder.source('grid',.1,.3);builder.line_z1('grid','first',1.+3j,line_id='a');builder.line_z1('first','second',.5+1j,line_id='b')
    for node,p in [('first',.2e6),('second',.3e6)]:builder.generator(node,p,mode='pv',v_set_pu=1.,q_min_var=-10e6,q_max_var=10e6,generator_id=node)
    s=example();s.network=builder.build();s.coupled.duration_s=.1;s.coupled.motors[0].node_id='second'
    m=s.coupled.machines[0];a=m.model_copy(deep=True);a.generator_id='first';a.exciter=None
    b=m.model_copy(deep=True);b.generator_id='second';b.governor=None
    s.coupled.machines=[a,b]
    r=run_study(s);assert r['completed'],r
    assert 'first.TGOV1.output_pu' in r['channels'] and 'second.SEXS.field_pu' in r['channels']
    assert 'first.SEXS.field_pu' not in r['channels'] and 'second.TGOV1.output_pu' not in r['channels']


def test_controlled_worker_cli_deadline_and_cancellation(tmp_path):
    s=example('limits_recovery');a=run_study(s);b=run_study(s,isolate=True,wall_timeout_s=30.)
    assert a['completed'] and b['completed'],b
    assert a['channels']==b['channels'] and a['additional_validation']==b['additional_validation']
    assert run_study(s,wall_timeout_s=.0001)['status']=='timeout'
    assert run_study(s,cancelled=lambda:True)['status']=='cancelled'
    from power_grid_env.cli import main
    path=tmp_path/'study.json';path.write_text(s.model_dump_json())
    assert main(['run-study',str(path),'--out',str(tmp_path/'out.json'),'--isolate','--wall-timeout','30'])==0
    assert json.loads((tmp_path/'out.json').read_text())['completed']


def test_initial_jacobian_perturbations_stay_inside_nearby_limits():
    bank,state=bank_fixture()
    valve=bank.gov[0][0];field=bank.avr[0][1]
    state[valve]=.3-5e-7;state[field]=.2+5e-7
    for address,low,high in [(valve,0.,.3),(field,.2,1.2)]:
        h=bank.difference_step(state,address,1e-6)
        assert 0<h<5e-7
        assert low<state[address]-h<state[address]+h<high
    assert bank.difference_step(state,bank.gov[0][1],1e-6)==1e-6
