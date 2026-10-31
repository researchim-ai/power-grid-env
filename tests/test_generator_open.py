"""Electrical generator opening: free rotor, machine-side sensing and no ghost injection."""
from pathlib import Path
import json
import numpy as np
import pytest
from power_grid_env.study_spec import StudySpec,run_study,run_study_document
from power_grid_env.coupled_rms import CoupledEvent
from benchmarks.generator_open_reference import reference,native_controlled_limit

NAMES=['genrou','gencls','saturated','fault_line','unload']


def example(name='genrou'):
    return StudySpec.model_validate_json(Path('cases/studies/generator_open_'+name+'.json').read_text())


@pytest.mark.parametrize('name',NAMES)
def test_generator_open_matches_independent_SI_free_rotor_and_controllers(name):
    s=example(name);r=run_study(s);assert r['completed'],r
    e=reference(s,r)
    for key,values in e['channels'].items():
        error=np.asarray(values)-r['channels'][key]
        if key.endswith('.terminal_angle_rad'):error=np.angle(np.exp(1j*error))
        tolerance=100. if key.endswith(('.p_w','.q_var','_p_w','_q_var')) else 1e-5 if key.endswith(('.omega','.delta','.speed_pu')) else 1e-4
        assert max(abs(error))<tolerance,key
    after=np.flatnonzero(np.asarray(r['time_s'])>.3)
    for key in ['g.connected','g.current_pu','g.p_w','g.q_var','g.electrical_power_pu']:
        assert np.all(np.asarray(r['channels'][key])[after]==0.)
    assert r['maximum_kcl_residual_pu']<1e-8 and r['maximum_power_balance_residual_pu']<1e-8


def test_uncontrolled_open_classical_rotor_matches_analytic_acceleration():
    from test_coupled_rms import example as classical
    s=classical();s.coupled.events=[CoupledEvent(time_s=.2,kind='generator_open',equipment_id='g')]
    r=run_study(s);assert r['completed'],r
    c=r['channels'];m=s.coupled.machines[0];assert m.damping_pu>0
    start=np.flatnonzero(np.asarray(r['time_s'])==.2)[-1]
    elapsed=np.asarray(r['time_s'][start:])-.2;power=c['g.mechanical_power_pu'][start];tau=2*m.inertia_s/m.damping_pu
    speed=1+power/m.damping_pu*(1-np.exp(-elapsed/tau))
    angle=c['g.delta'][start]+2*np.pi*s.network.frequency_hz*power/m.damping_pu*(elapsed-tau*(1-np.exp(-elapsed/tau)))
    assert max(abs(np.asarray(c['g.omega'][start:])-speed))<1e-8
    assert max(abs(np.asarray(c['g.delta'][start:])-angle))<1e-6


def test_opening_keeps_states_continuous_and_senses_machine_terminal():
    r=run_study(example());assert r['completed'];c=r['channels']
    before,after=np.flatnonzero(np.asarray(r['time_s'])==.3)
    for key in ['g.delta','g.omega','g.e1q','g.e1d','g.e2d','g.e2q','g.SEXS.field_state_pu','g.TGOV1.valve_state_pu','m.e_real_pu','m.e_imag_pu','m.speed_pu']:
        assert c[key][before]==c[key][after]
    assert c['g.connected'][before]==1. and c['g.connected'][after]==0.
    assert abs(c['g.terminal_voltage_pu'][after]-c['g.bus_voltage_pu'][after])>.01
    assert c['g.SEXS.error_pu'][after]==pytest.approx(c['g.SEXS.reference_pu'][after]-c['g.terminal_voltage_pu'][after]+c['g.IEEEST.output_pu'][after])
    assert abs(c['g.omega'][-1]-c['g.omega'][after])>1e-4
    assert np.ptp(c['g.e1q'][after:])>1e-4


def test_simultaneous_fault_line_and_generator_open_have_one_atomic_boundary():
    r=run_study(example('fault_line'));assert r['completed'],r
    indices=np.flatnonzero(np.asarray(r['time_s'])==.3);assert len(indices)==2
    last=indices[-1];c=r['channels']
    assert c['g.connected'][last]==c['line.closed'][last]==c['fault.active'][last]==0.
    assert len([e for e in r['event_log'] if e['time_s']==.3])==3


@pytest.mark.parametrize('saturated',[False,True])
def test_post_open_external_grid_limit_matches_ANDES_with_explicit_semantic_scope(saturated):
    r=native_controlled_limit(saturated);assert r['passed'],r
    assert 'rotor/control semantics differ' in r['scope']


def test_mixed_generators_open_only_target_and_other_machine_stays_connected():
    from power_grid_env.build import NetBuilder
    from power_grid_env.rms_study import ClassicalMachine
    builder=NetBuilder('two-open','Two generators',10.,s_base_va=5e6)
    for n in ['grid','a','b']:builder.node(n)
    builder.source('grid',.1,.3);builder.line_z1('grid','a',1+3j,line_id='l1');builder.line_z1('a','b',.5+1j,line_id='l2')
    for n in ['a','b']:builder.generator(n,.2e6,mode='pv',v_set_pu=1.,q_min_var=-10e6,q_max_var=10e6,generator_id=n)
    s=example();s.network=builder.build();s.coupled.duration_s=.2;s.coupled.motors[0].node_id='b'
    m=s.coupled.machines[0];m.generator_id='a';s.coupled.machines=[m,ClassicalMachine(generator_id='b',rated_va=2e6,inertia_s=1.,x_d_prime_pu=.3,damping_pu=3.)]
    s.coupled.events=[CoupledEvent(time_s=.1,kind='generator_open',equipment_id='a')]
    r=run_study(s);assert r['completed'],r
    assert r['channels']['a.connected'][-1]==0. and r['channels']['b.connected'][-1]==1.
    assert r['channels']['a.p_w'][-1]==0. and abs(r['channels']['b.p_w'][-1])>1e4


def test_invalid_breaker_sequences_reclose_and_clearing_protocol_reject(monkeypatch):
    import power_grid_env.induction_network as solver
    from power_grid_env.fault_clearing import clearing_variants,FaultClearingProtocol
    monkeypatch.setattr(solver,'solve_ivp',lambda *a,**kw:pytest.fail('must reject before TDS'))
    for events in [[CoupledEvent(time_s=.3,kind='generator_open',equipment_id='unknown')],
        [CoupledEvent(time_s=.2,kind='generator_open',equipment_id='g'),CoupledEvent(time_s=.3,kind='generator_open',equipment_id='g')]]:
        s=example();s.coupled.events=events;assert run_study(s)['reason']=='coupled_scope'
    for kind,value in [('generator_close',None),('generator_open',1.)]:
        d=example().model_dump(mode='json');d['coupled']['events']=[dict(time_s=.3,kind=kind,equipment_id='g',value=value)]
        assert run_study_document(d)['reason']=='schema'
    p=FaultClearingProtocol.model_validate_json(Path('cases/fault-clearing/genrou.json').read_text())
    with pytest.raises(ValueError,match='disconnected-rotor'):clearing_variants(example('fault_line'),p)


def test_generator_open_worker_cli_schemas_and_deadline(tmp_path):
    from power_grid_env.cli import main
    from power_grid_env.schema_export import export_schemas
    import jsonschema
    s=example('unload');a=run_study(s);b=run_study(s,isolate=True,wall_timeout_s=30.)
    assert a['completed'] and b['completed'] and a['channels']==b['channels']
    assert run_study(s,wall_timeout_s=.0001)['status']=='timeout'
    assert run_study(s,cancelled=lambda:True)['status']=='cancelled'
    p=tmp_path/'case.json';p.write_text(s.model_dump_json())
    assert main(['run-study',str(p),'--out',str(tmp_path/'result.json'),'--isolate','--wall-timeout','30'])==0
    export_schemas(tmp_path/'schemas');jsonschema.validate(s.model_dump(mode='json'),json.loads((tmp_path/'schemas/study.schema.json').read_text()))


def test_open_generator_excitation_cannot_feed_the_grid():
    s=example();s.coupled.events.append(CoupledEvent(time_s=.6,kind='voltage_reference_step',equipment_id='g',value=0.))
    a=run_study(s);s.coupled.events[-1].value=.1;b=run_study(s)
    assert a['completed'] and b['completed']
    assert max(abs(np.asarray(a['channels']['g.terminal_voltage_pu'])-b['channels']['g.terminal_voltage_pu']))>.01
    for key in ['m.speed_pu','m.bus_voltage_pu','parallel.from_current_a']:
        assert max(abs(np.asarray(a['channels'][key])-b['channels'][key]))<1e-5
