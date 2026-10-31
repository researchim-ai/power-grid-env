"""Line switching: independent dynamics, connectivity and physical branch flows."""
from pathlib import Path
import json
import numpy as np
import pytest
from power_grid_env.study_spec import StudySpec,run_study,run_study_document
from power_grid_env.coupled_rms import CoupledEvent
from benchmarks.coupled_line_reference import reference,native_controlled_limit

NAMES=['open_reclose','persistent_open','fault_open','saturated_fault_open','charging']


def example(name='open_reclose'):
    return StudySpec.model_validate_json(Path('cases/studies/coupled_line_'+name+'.json').read_text())


@pytest.mark.parametrize('name',NAMES)
def test_switched_line_dynamics_match_independent_physical_SI(name):
    s=example(name);r=run_study(s);assert r['completed'],r
    e=reference(s,r)
    for key,values in e['channels'].items():
        tolerance=100. if key.endswith(('.p_w','.q_var','_p_w','_q_var')) else 1e-5 if key.endswith(('.omega','.delta','.speed_pu')) else 1e-4
        assert max(abs(np.asarray(values)-r['channels'][key]))<tolerance,key
    assert r['maximum_kcl_residual_pu']<1e-8 and r['maximum_power_balance_residual_pu']<1e-8
    for event in s.coupled.events:
        if event.kind not in {'line_open','line_close'}:continue
        a,b=np.flatnonzero(np.asarray(r['time_s'])==event.time_s)
        assert r['channels'][event.equipment_id+'.closed'][a]!=r['channels'][event.equipment_id+'.closed'][b]
        for key in ['g.delta','g.omega','g.e1q','g.e1d','g.e2d','g.e2q','m.speed_pu','m.e_real_pu','m.e_imag_pu']:
            assert r['channels'][key][a]==r['channels'][key][b]


@pytest.mark.parametrize('saturated',[False,True])
def test_line_trip_generator_only_limit_converges_to_ANDES(saturated):
    result=native_controlled_limit(saturated);assert result['passed'],result


def test_open_line_has_no_charging_or_power_and_closed_line_has_copper_loss():
    s=example('charging');r=run_study(s);assert r['completed']
    c=r['channels'];opened=np.asarray(c['line.closed'])==0.
    for name in ['from_current_a','to_current_a','from_p_w','from_q_var','to_p_w','to_q_var','loss_p_w','net_q_var']:
        assert np.all(np.asarray(c['line.'+name])[opened]==0.)
    from power_grid_env.rms_study import positive_sequence
    l=s.network.lines[0];resistance=positive_sequence(l.r_ohm).real
    # Series current excludes the pi-end charging current.
    for index in [0,len(r['time_s'])-1]:
        from power_grid_env.topology import compile_topology
        topology=compile_topology(s.network);a=topology.terminal_bus[l.terminal_from];b=topology.terminal_bus[l.terminal_to]
        va=c[a+'.v'][index]*np.exp(1j*c[a+'.a'][index])*10000/np.sqrt(3)
        vb=c[b+'.v'][index]*np.exp(1j*c[b+'.a'][index])*10000/np.sqrt(3)
        z=positive_sequence(np.asarray(l.r_ohm)+1j*np.asarray(l.x_ohm))
        expected=3*abs((va-vb)/z)**2*resistance
        assert c['line.loss_p_w'][index]==pytest.approx(expected,abs=1e-7)


def test_islanding_redundancy_unknown_and_conflicts_reject_before_integration(monkeypatch):
    import power_grid_env.induction_network as solver
    monkeypatch.setattr(solver,'solve_ivp',lambda *a,**k:pytest.fail('must reject before integration'))
    for events in [
        [CoupledEvent(time_s=.2,kind='line_open',equipment_id='absent')],
        [CoupledEvent(time_s=.2,kind='line_close',equipment_id='line')],
        [CoupledEvent(time_s=.2,kind='line_open',equipment_id='line'),CoupledEvent(time_s=.3,kind='line_open',equipment_id='line')],
        [CoupledEvent(time_s=.2,kind='line_open',equipment_id='line'),CoupledEvent(time_s=.2,kind='line_close',equipment_id='line')],
        [CoupledEvent(time_s=.2,kind='line_open',equipment_id='line'),CoupledEvent(time_s=.2,kind='line_open',equipment_id='parallel')]]:
        s=example();s.coupled.events=events;r=run_study(s)
        assert r['reason']=='coupled_scope'
    from test_multivoltage_rms import example as multi
    s=example();s.network=multi('Dyn11').network;s.network.transformers[0].windings.append(s.network.transformers[0].windings[-1].model_copy(deep=True))
    assert run_study(s)['reason']=='coupled_scope'
    d=example().model_dump(mode='json');d['coupled']['events'][0]['value']=1.
    assert run_study_document(d)['reason']=='schema'


def test_simultaneous_path_exchange_and_fault_clear_are_one_boundary():
    s=example('fault_open')
    s.coupled.events=[CoupledEvent(time_s=.1,kind='line_open',equipment_id='parallel'),
        CoupledEvent(time_s=.3,kind='line_open',equipment_id='line'),
        CoupledEvent(time_s=.3,kind='line_close',equipment_id='parallel')]
    r=run_study(s);assert r['completed'],r
    indices=np.flatnonzero(np.asarray(r['time_s'])==.3);assert len(indices)==2
    post=indices[-1];assert r['channels']['line.closed'][post]==0. and r['channels']['parallel.closed'][post]==1.
    assert r['channels']['fault.active'][post]==0.
    assert len([e for e in r['event_log'] if e['time_s']==.3])==3


def test_post_switch_snapshot_matches_ABC_backend():
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.model import Load,Terminal
    from power_grid_env.build import grounding,provenance
    from power_grid_env.topology import compile_topology
    s=example('persistent_open');r=run_study(s);assert r['completed']
    index=np.flatnonzero(np.asarray(r['time_s'])==.3)[-1];c=r['channels']
    line=s.network.lines[0];removed={line.terminal_from,line.terminal_to}
    s.network.lines=[l for l in s.network.lines if l.id!=line.id];s.network.terminals=[t for t in s.network.terminals if t.id not in removed]
    g=s.network.generators[0];g.mode='pq';g.p_w=c['g.p_w'][index];g.q_var=c['g.q_var'][index]
    pu=c['m.bus_voltage_pu'][index];p=c['m.p_w'][index]/pu**2;q=c['m.q_var'][index]/pu**2
    s.network.terminals.append(Terminal(id='motor-load-t',equipment_id='motor-load',connectivity_node_id='plant',phases=list('ABC')))
    s.network.loads.append(Load(id='motor-load',terminal_id='motor-load-t',phases=list('ABC'),connection='wye',grounding=grounding('solid'),provenance=provenance(),model='constant_z',p_w={k:p/3 for k in 'ABC'},q_var={k:q/3 for k in 'ABC'}))
    abc=solve_network(s.network);assert abc.status=='converged',abc.diagnostics
    for v in abc.voltages:
        if v.phase=='A':assert c[v.bus_id+'.v'][index]==pytest.approx(abs(v.complex)/v.v_base_ln_v,abs=1e-5)


def test_line_worker_cli_and_schema(tmp_path):
    from power_grid_env.cli import main
    from power_grid_env.schema_export import export_schemas
    import jsonschema
    s=example('charging');a=run_study(s);b=run_study(s,isolate=True,wall_timeout_s=30.)
    assert a['completed'] and b['completed'] and a['channels']==b['channels']
    p=tmp_path/'case.json';p.write_text(s.model_dump_json())
    assert main(['run-study',str(p),'--out',str(tmp_path/'result.json'),'--isolate','--wall-timeout','30'])==0
    export_schemas(tmp_path/'schemas');jsonschema.validate(s.model_dump(mode='json'),json.loads((tmp_path/'schemas/study.schema.json').read_text()))
