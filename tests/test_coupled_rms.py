"""One-network generator/motor trajectories and independent physical closure."""
from pathlib import Path
import json
import numpy as np
import pytest
from pydantic import ValidationError
from benchmarks.coupled_rms_reference import reference
from power_grid_env.study_spec import StudySpec,run_study,run_study_document
from power_grid_env.coupled_rms import CoupledEvent


def example(name='hold'):
    return StudySpec.model_validate_json(Path('cases/studies/coupled_'+name+'.json').read_text())


@pytest.mark.parametrize('name',['hold','motor_load_step','generator_step','voltage_sag','cold_start','thermal_trip'])
def test_shared_trajectory_matches_independent_physical_currents_and_flux(name):
    s=example(name);r=run_study(s);assert r['completed'],r
    independent=reference(s,r)
    for key,values in independent['channels'].items():
        tolerance=100. if key.endswith(('.p_w','.q_var')) else 1e-5 if key.endswith(('.omega','.speed_pu','.delta')) else 1e-4
        assert max(abs(np.asarray(values)-r['channels'][key]))<tolerance,key
    assert r['maximum_kcl_residual_pu']<1e-8
    assert r['maximum_power_balance_residual_pu']<1e-8
    actual=next((e['time_s'] for e in r['event_log'] if e['kind']=='thermal_trip'),None)
    if name=='thermal_trip':assert actual==pytest.approx(independent['trip_time_s'],abs=1e-6)
    else:assert actual is independent['trip_time_s'] is None
    assert not r['industrial_profile_qualified'] and not r['field_validated']


def test_motor_load_and_generator_mechanical_changes_have_bidirectional_effects():
    hold=run_study(example());motor=run_study(example('motor_load_step'));generator=run_study(example('generator_step'))
    assert hold['completed'] and motor['completed'] and generator['completed']
    assert max(abs(np.array(hold['channels']['g.omega'])-1))<1e-9
    assert max(abs(np.array(motor['channels']['g.omega'])-1))>1e-5
    assert max(abs(np.array(generator['channels']['m.speed_pu'])-hold['channels']['m.speed_pu'][0]))>1e-7
    assert motor['channels']['m.speed_pu'][-1]<hold['channels']['m.speed_pu'][-1]
    at=np.flatnonzero(np.array(generator['time_s'])==.2)
    assert len(at)==2
    for key in ['g.delta','g.omega','m.e_real_pu','m.e_imag_pu','m.speed_pu']:
        assert generator['channels'][key][at[0]]==generator['channels'][key][at[1]]
    assert generator['channels']['g.mechanical_power_pu'][at[1]]-generator['channels']['g.mechanical_power_pu'][at[0]]==pytest.approx(.05)


def test_system_base_and_multiple_machines_preserve_state_identity():
    s=example('motor_load_step');a=run_study(s);s.network.s_base_va=2e6;b=run_study(s)
    assert a['completed'] and b['completed']
    for key in ['g.delta','g.omega','m.speed_pu','m.bus_voltage_pu','g.current_pu']:
        assert a['channels'][key]==pytest.approx(b['channels'][key],abs=1e-6)
    # Independent generator buses avoid implicit reactive sharing assumptions.
    from power_grid_env.build import NetBuilder
    from power_grid_env.rms_study import ClassicalMachine
    builder=NetBuilder('multi-coupled','Multiple generators and motors',10.,s_base_va=5e6)
    for node in ['grid','g1','g2']:builder.node(node)
    builder.source('grid',.1,.3);builder.line_z1('grid','g1',1.+3j,line_id='a');builder.line_z1('g1','g2',.5+1j,line_id='b')
    builder.generator('g1',.2e6,mode='pv',v_set_pu=1.,q_min_var=-10e6,q_max_var=10e6,generator_id='first')
    builder.generator('g2',.3e6,mode='pv',v_set_pu=1.,q_min_var=-10e6,q_max_var=10e6,generator_id='second')
    s=example();s.network=builder.build();s.coupled.duration_s=.1;s.coupled.motors[0].node_id='g2'
    s.coupled.machines=[ClassicalMachine(generator_id=i,rated_va=2e6,inertia_s=1.,x_d_prime_pu=.3,damping_pu=3.) for i in ['first','second']]
    r=run_study(s);assert r['completed'],r
    assert r['channels']['first.p_w'][0]==pytest.approx(.2e6,abs=.1)
    assert r['channels']['second.p_w'][0]==pytest.approx(.3e6,abs=.1)
    assert set(r['generator_mapping'])=={'first','second'}


def test_disconnected_motor_limit_agrees_with_existing_ANDES_GENCLS():
    s=example('generator_step');s.coupled.motors[0].connected=False
    coupled=run_study(s);assert coupled['completed'],coupled
    from power_grid_env.rms_study import RMSSettings,RMSEvent
    other=s.model_copy(deep=True);other.study='rms';other.backend='andes';other.coupled=None
    other.rms=RMSSettings(duration_s=s.coupled.duration_s,step_s=s.coupled.step_s,machines=s.coupled.machines,
        events=[RMSEvent(time_s=.2,kind='mechanical_step',equipment_id='g',value=.05)])
    times=np.asarray(coupled['time_s']);keep=np.r_[np.diff(times)!=0,True]
    errors=[]
    for step in [.005,.0025,.00125]:
        other.rms.step_s=step
        native=run_study(other);assert native['completed'],native
        row={}
        for key in ['g.delta','g.omega']:
            expected=np.interp(times[keep],native['time_s'],native['channels'][key])
            row[key]=float(max(abs(np.array(coupled['channels'][key])[keep]-expected)))
        errors.append(row)
    assert errors[-1]['g.delta']<1e-4 and errors[-1]['g.omega']<1e-5
    assert errors[0]['g.delta']/errors[1]['g.delta']==pytest.approx(4.,rel=.15)
    assert errors[1]['g.delta']/errors[2]['g.delta']==pytest.approx(4.,rel=.15)



@pytest.mark.parametrize('group',['Dyn11','YNd1','Dyn5'])
def test_joint_loaded_transformer_snapshot_matches_full_ABC_backend(group):
    from test_multivoltage_rms import example as multi
    from power_grid_env.backend_opendss import solve_network
    s=example();s.network=multi(group).network;s.network.loads[0].model='constant_z'
    s.coupled.motors[0].node_id='load';s.coupled.duration_s=.05
    r=run_study(s);assert r['completed'],r
    pu=r['channels']['m.bus_voltage_pu'][0]
    power=complex(r['channels']['m.p_w'][0],r['channels']['m.q_var'][0])/pu**2
    for phase in 'ABC':
        s.network.loads[0].p_w[phase]+=power.real/3;s.network.loads[0].q_var[phase]+=power.imag/3
    abc=solve_network(s.network);assert abc.status=='converged',abc.diagnostics
    for v in abc.voltages:
        if v.phase=='A':
            assert r['channels'][v.bus_id+'.v'][0]==pytest.approx(abs(v.complex)/v.v_base_ln_v,abs=1e-5)
            angle=r['channels'][v.bus_id+'.a'][0]
            assert abs(np.angle(np.exp(1j*(angle-np.angle(v.complex)))))<1e-4


def test_unsupported_model_and_invalid_target_reject_before_integration(monkeypatch):
    import power_grid_env.induction_network as engine
    monkeypatch.setattr(engine,'solve_ivp',lambda *a,**k:pytest.fail('must reject before integration'))
    s=example();s.coupled.events=[CoupledEvent(time_s=.2,kind='generator_mechanical_step',equipment_id='absent',value=.1)]
    assert run_study(s)['reason']=='coupled_scope'
    s=example();s.network.generators[0].mode='pq'
    assert run_study(s)['reason']=='coupled_scope'
    from power_grid_env.rms_controls import SEXSPassport
    s=example();s.coupled.machines[0].exciter=SEXSPassport(TATB=.3,TB=.5,K=20.,TE=.1,EMIN=-1.,EMAX=3.)
    assert run_study(s)['reason']=='coupled_scope'
    s=example();s.coupled.motors[0].id='g'
    assert run_study(s)['reason']=='coupled_scope'
    s=example();s.coupled.machines.append(s.coupled.machines[0])
    assert run_study(s)['reason']=='coupled_scope'
    s=example();source=s.network.sources[0];source.id='g'
    next(t for t in s.network.terminals if t.id==source.terminal_id).equipment_id='g'
    assert run_study(s)['reason']=='coupled_scope'
    payload=example().model_dump(mode='json');payload['coupled']['machines'][0]['model']='GENROU'
    assert run_study_document(payload)['reason']=='schema'
    payload=example().model_dump(mode='json');payload['coupled']['events']=[dict(time_s=.2,kind='generator_trip',equipment_id='g')]
    assert run_study_document(payload)['reason']=='schema'
    s=example();s.network.generators[0].q_min_var=-1.;s.network.generators[0].q_max_var=1.
    assert run_study(s)['reason']=='generator_initial_q_limit'
    s=example();s.coupled.motors[0].fan_torque_pu=1000.
    assert run_study(s)['status']=='initialization_failed'


def test_coupled_settings_cannot_be_silently_ignored():
    s=example();s.study='motor_start'
    assert run_study(s)['reason']=='coupled_settings_only_for_coupled_rms'
    s=example();s.coupled=None
    assert run_study(s)['reason']=='coupled_settings_required'


def test_worker_cli_timeout_cancellation_and_recovery(tmp_path):
    s=example('motor_load_step');a=run_study(s);b=run_study(s,isolate=True,wall_timeout_s=30.)
    assert a['completed'] and b['completed'],b
    assert a['channels']==b['channels']
    assert run_study(s,wall_timeout_s=.0001)['status']=='timeout'
    assert run_study(s,cancelled=lambda:True)['status']=='cancelled'
    from power_grid_env.cli import main
    path=tmp_path/'study.json';path.write_text(s.model_dump_json())
    assert main(['run-study',str(path),'--out',str(tmp_path/'out.json'),'--isolate','--wall-timeout','30'])==0
    assert json.loads((tmp_path/'out.json').read_text())['completed']


def test_simultaneous_generator_and_motor_commands_are_one_atomic_boundary():
    s=example();s.coupled.events=[CoupledEvent(time_s=.2,kind='generator_mechanical_step',equipment_id='g',value=.05),
        CoupledEvent(time_s=.2,kind='load_torque_scale',equipment_id='m',value=1.1)]
    r=run_study(s);assert r['completed'],r
    before,after=np.flatnonzero(np.array(r['time_s'])==.2)
    for key in ['g.delta','g.omega','m.e_real_pu','m.e_imag_pu','m.speed_pu','g.current_pu','m.current_pu']:
        assert r['channels'][key][before]==r['channels'][key][after]
    assert r['channels']['g.mechanical_power_pu'][after]-r['channels']['g.mechanical_power_pu'][before]==pytest.approx(.05)
    assert r['channels']['m.load_scale'][after]==1.1
    assert len(r['event_log'])==2
