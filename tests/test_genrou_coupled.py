"""Shared GENROU/motor dynamics: independent physics, bases and failure paths."""
from pathlib import Path
import json
import numpy as np
import pytest
from power_grid_env.study_spec import StudySpec,run_study,run_study_document
from power_grid_env.rms_study import ClassicalMachine,RoundRotorMachine
from power_grid_env.coupled_rms import CoupledEvent
from benchmarks.genrou_coupled_reference import reference,native_controlled_limit

NAMES=['hold','motor_load','voltage_sag','setpoint_steps','limits_recovery','thermal_trip','saturated_hold','saturated_sag']


def example(name='hold'):
    return StudySpec.model_validate_json(Path('cases/studies/genrou_coupled_'+name+'.json').read_text())


@pytest.mark.parametrize('name',NAMES)
def test_joint_six_state_machine_motor_and_controllers_match_independent_SI(name):
    s=example(name);r=run_study(s);assert r['completed'],r
    e=reference(s,r)
    for key,values in e['channels'].items():
        limit=100. if key.endswith(('.p_w','.q_var')) else 1e-5 if key.endswith(('.omega','.speed_pu','.delta')) else 1e-4
        assert max(abs(np.array(values)-r['channels'][key]))<limit,key
    actual=next((v['time_s'] for v in r['event_log'] if v['kind']=='thermal_trip'),None)
    if e['trip_time_s'] is None:assert actual is None
    else:assert actual==pytest.approx(e['trip_time_s'],abs=1e-6)
    assert r['machine_models']=={'g':'GENROU'}
    assert r['maximum_kcl_residual_pu']<1e-8 and r['maximum_power_balance_residual_pu']<1e-8
    assert r['additional_validation']['ok']
    assert not r['industrial_profile_qualified'] and not r['field_validated']


@pytest.mark.parametrize('saturated',[False,True])
def test_saturated_and_unsaturated_generator_only_limits_converge_to_ANDES(saturated):
    result=native_controlled_limit(saturated);assert result['passed'],result
    assert result['errors'][-1]['g.e1q']<result['errors'][0]['g.e1q']


def test_round_rotor_initialization_field_and_copper_loss_are_physical():
    s=example();r=run_study(s);assert r['completed']
    p=s.coupled.machines[0];c=r['channels'];initial=r['operating_point']['generator_initial']['g']
    assert initial['field_pu']>initial['internal_voltage_pu']+.1
    assert initial['mechanical_power_pu']-initial['p_w']/p.rated_va==pytest.approx(p.armature_r_pu*c['g.current_pu'][0]**2,abs=1e-10)
    for name in ['e1q','e1d','e2d','e2q','delta','omega','field_pu']:
        assert np.ptp(c['g.'+name])<1e-9
    assert max(abs(np.array(c['g.Se'])))==0.
    s=example('saturated_hold');r=run_study(s);assert r['completed']
    assert min(r['channels']['g.Se'])>0


def test_reference_steps_preserve_rotor_states_and_recover_from_both_limits():
    r=run_study(example('limits_recovery'));assert r['completed']
    c=r['channels']
    for t in [.2,.6]:
        indices=[i for i,time in enumerate(r['time_s']) if time==t]
        assert len(indices)==2
        a,b=indices
        for key in ['e1q','e1d','e2d','e2q','delta','omega','SEXS.field_state_pu']:
            assert c['g.'+key][a]==c['g.'+key][b]
    assert max(c['g.SEXS.field_pu'])==pytest.approx(2.4)
    assert max(c['g.TGOV1.valve_pu'])==pytest.approx(.3)
    assert c['g.SEXS.field_pu'][-1]<2.35
    assert c['g.TGOV1.valve_pu'][-1]<.29


def test_system_base_invariance_and_uncontrolled_six_state_machine():
    s=example('motor_load');a=run_study(s);s.network.s_base_va=3e6;b=run_study(s)
    assert a['completed'] and b['completed']
    for key in ['g.e1q','g.e2d','g.delta','g.omega','m.speed_pu','g.SEXS.field_pu','g.TGOV1.output_pu']:
        assert max(abs(np.array(a['channels'][key])-b['channels'][key]))<2e-6
    s=example();s.coupled.machines[0].exciter=None;s.coupled.machines[0].governor=None
    s.coupled.events=[CoupledEvent(time_s=.2,kind='generator_mechanical_step',equipment_id='g',value=.03)]
    c=run_study(s);assert c['completed'],c
    assert np.ptp(c['channels']['g.field_pu'])==0.
    assert np.ptp(c['channels']['g.e1q'])>1e-5
    assert 'g.TGOV1.output_pu' not in c['channels']


def test_mixed_classical_and_round_rotor_order_and_optional_control_layout():
    from power_grid_env.build import NetBuilder
    builder=NetBuilder('mixed-generators','Mixed machines',10.,s_base_va=5e6)
    for node in ['grid','first','second']:builder.node(node)
    builder.source('grid',.1,.3);builder.line_z1('grid','first',1.+3j,line_id='a');builder.line_z1('first','second',.5+1j,line_id='b')
    for node,p in [('first',.2e6),('second',.3e6)]:builder.generator(node,p,mode='pv',v_set_pu=1.,q_min_var=-10e6,q_max_var=10e6,generator_id=node)
    for reverse in [False,True]:
        s=example();s.network=builder.build();s.coupled.duration_s=.1;s.coupled.motors[0].node_id='second'
        round=s.coupled.machines[0];round.generator_id='second' if reverse else 'first';round.exciter=None
        classical=ClassicalMachine(generator_id='first' if reverse else 'second',rated_va=2e6,inertia_s=1.,x_d_prime_pu=.3,damping_pu=3.,exciter=example().coupled.machines[0].exciter)
        s.coupled.machines=[classical,round]
        r=run_study(s);assert r['completed'],r
        assert r['machine_models'][round.generator_id]=='GENROU'
        assert round.generator_id+'.e1q' in r['channels'] and classical.generator_id+'.e1q' not in r['channels']
        assert round.generator_id+'.SEXS.field_pu' not in r['channels']


def test_bad_round_rotor_parameters_and_initial_field_reject_before_integration(monkeypatch):
    import power_grid_env.induction_network as solver
    monkeypatch.setattr(solver,'solve_ivp',lambda *a,**k:pytest.fail('must reject before integration'))
    s=example();s.coupled.machines[0].exciter.EMAX=1.3
    assert run_study(s)['reason']=='initial_exciter_limit_or_boundary'
    s=example();s.coupled.machines[0].x_q_subtransient_pu=.2
    assert run_study(s)['reason']=='coupled_scope'
    d=example().model_dump(mode='json');del d['coupled']['machines'][0]['td_prime_s']
    assert run_study_document(d)['reason']=='schema'
    d=example().model_dump(mode='json');d['coupled']['machines'][0]['saturation']='quadratic'
    assert run_study_document(d)['reason']=='schema'


def test_round_rotor_worker_cli_cancel_and_deadline(tmp_path):
    from power_grid_env.cli import main
    s=example('saturated_sag');a=run_study(s);b=run_study(s,isolate=True,wall_timeout_s=30.)
    assert a['completed'] and b['completed'],b
    assert a['channels']==b['channels']
    assert run_study(s,wall_timeout_s=.0001)['status']=='timeout'
    assert run_study(s,cancelled=lambda:True)['status']=='cancelled'
    path=tmp_path/'case.json';path.write_text(s.model_dump_json())
    assert main(['run-study',str(path),'--out',str(tmp_path/'result.json'),'--isolate','--wall-timeout','30'])==0
    assert json.loads((tmp_path/'result.json').read_text())['completed']
