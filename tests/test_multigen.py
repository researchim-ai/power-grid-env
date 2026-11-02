"""Joint PV roots, targeted controls, coordinate invariance and safe resumption."""
import copy,json
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from benchmarks.multigen_cases import example,NAMES,NEGATIVE
from benchmarks.multibus_operating_point import independent_circuit,validate_scope
from benchmarks.multigen_audit import control_contract,trace_contract,invariant
from power_grid_env.study_spec import run_study


@pytest.fixture(scope='module')
def references():
    @lru_cache(None)
    def get(name):
        s=example(name);p=run_study(s,isolate=True,wall_timeout_s=90.);assert p['completed'],p
        c=independent_circuit(s);return s,p,c.run(p)
    return get


@pytest.mark.parametrize('name',NAMES)
def test_joint_multi_machine_physics_matches_primary(name,references):
    from benchmarks.multibus_motor_validation import compare_si
    s,p,i=references(name)
    assert compare_si(s,p,i)['passed']
    assert trace_contract(s,p)['passed'] and trace_contract(s,i)['passed']
    assert set(i['initialization']['generator_initial_q_var'])=={g.id for g in s.network.generators}


@pytest.mark.parametrize('name',['permuted2','base2'])
def test_physical_response_does_not_depend_on_machine_order_or_network_base(name,references):
    s,p,i=references('governor2');_,q,j=references(name)
    assert invariant(s,p,q)['passed'] and invariant(s,i,j)['passed']


@pytest.mark.parametrize('name',NEGATIVE)
def test_invalid_multi_generator_scope_or_initial_Q_is_explicitly_rejected(name):
    with pytest.raises(ValueError):independent_circuit(example(name))


@pytest.mark.parametrize('fault',['other_machine','wrong_base','missing_event','missing_machine','nonfinite'])
def test_per_machine_control_audit_catches_cross_talk_and_incorrect_scaling(fault,references):
    s,_,i=references('governor2');r=copy.deepcopy(i);e=next(e for e in r['event_log'] if e['kind']=='governor_step')
    if fault=='other_machine':e['references_after']['g']['governor']+=.03
    elif fault=='wrong_base':e['references_after']['g2']['governor']+=.03
    elif fault=='missing_event':r['event_log'].remove(e)
    elif fault=='missing_machine':e['references_after'].pop('g')
    else:e['references_after']['g2']['governor']=float('nan')
    try:result=control_contract(s,r)
    except ValueError:return
    assert not result['passed']


def test_machine_and_rotor_states_cannot_jump_at_control_events(references):
    s,p,_=references('governor2');r=copy.deepcopy(p);index=r['sample_side'].index('post_event');r['channels']['g2.omega'][index]+=.01
    assert not trace_contract(s,r)['passed']


@pytest.mark.parametrize('kind',['governor_step','voltage_reference_step'])
def test_native_control_routes_id_to_its_own_addresses_and_power_base(kind):
    from benchmarks.native_closed_driver import NativeLoop
    loop=NativeLoop(example('governor2'));loop.a=SimpleNamespace(TGOV1=SimpleNamespace(pref0=SimpleNamespace(v=np.array([.2,.3]))),SEXS=SimpleNamespace(vref0=SimpleNamespace(v=np.array([1.,1.1]))))
    loop.ctx=dict(generator_controls={'g':dict(governor_index=1,exciter_index=0,factor=.2),'g2':dict(governor_index=0,exciter_index=1,factor=.1)});loop.log=[]
    event=SimpleNamespace(kind=kind,equipment_id='g2',value=.03);loop.apply_generator_control(event,.2)
    assert loop.a.TGOV1.pref0.v==pytest.approx([.203,.3] if kind=='governor_step' else [.2,.3])
    assert loop.a.SEXS.vref0.v==pytest.approx([1.,1.13] if kind=='voltage_reference_step' else [1.,1.1])
    assert loop.log[0]['equipment_id']=='g2'


def test_protocol_precedes_native_calls_and_resume_rechecks_original_protocol(tmp_path,monkeypatch):
    from benchmarks import multigen_validation as v
    calls=[]
    def stop(*args,**kwargs):
        protocol=json.loads((tmp_path/'protocol.json').read_text());assert protocol['cases']==list(NAMES)
        assert protocol['limits']['control_reference_delta']==1e-12
        calls.append(1);raise RuntimeError('preflight')
    monkeypatch.setattr(v,'native_reference',stop)
    for _ in range(2):
        with pytest.raises(RuntimeError,match='preflight'):v.run(tmp_path,1)
    assert len(calls)==2
    protocol=json.loads((tmp_path/'protocol.json').read_text());protocol['native_timeout_s']=999.
    (tmp_path/'protocol.json').write_text(json.dumps(protocol))
    with pytest.raises(ValueError,match='identical original protocol'):v.run(tmp_path,1)
    assert len(calls)==2


def test_changed_cached_trace_is_not_silently_recomputed_or_accepted(tmp_path,monkeypatch):
    from benchmarks import multigen_validation as v
    from power_grid_env.report import write_json
    monkeypatch.setattr(v,'native_reference',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('stop')))
    with pytest.raises(RuntimeError):v.run(tmp_path,1)
    write_json(tmp_path/'hold2/comparison.json',dict(passed=True,artifacts_sha256={'SI.json':'0'*64}))
    write_json(tmp_path/'hold2/SI.json',{})
    with pytest.raises(ValueError,match='cached raw case changed'):v.run(tmp_path,1)


def test_native_scope_does_not_compute_multi_PV_equilibrium(monkeypatch):
    from benchmarks.multibus_multi_generator import MultiCircuit
    from benchmarks.multibus_si_motor import Circuit
    def forbidden(*args,**kwargs):raise AssertionError('SI trajectory/initialization used on native scope path')
    for method in ['steady_response','electrical','rhs']:monkeypatch.setattr(Circuit,method,forbidden)
    monkeypatch.setattr(MultiCircuit,'initialize_joint',forbidden)
    assert validate_scope(example('hold4')).generator is None


def test_native_four_machine_initializer_consumes_no_primary_or_SI_solution(monkeypatch):
    import sys,tempfile
    from benchmarks.andes_multibus_motor import worker
    from benchmarks.multibus_multi_generator import MultiCircuit
    from benchmarks.multibus_si_motor import Circuit
    from benchmarks.group_si_generator import Generator
    from power_grid_env.andes_rms import ensure_andes_importable
    spec=example('hold4');spec.coupled.machines.reverse()
    circuit=independent_circuit(spec);expected=circuit.initialization
    voltage=circuit.electrical(circuit.state)[0];expected_signals={}
    for j,(node,g,at) in enumerate(circuit.ports):expected_signals.update(g.signals(circuit.state[at],circuit.generator_currents[j],voltage[node]))
    assert ensure_andes_importable()
    import andes
    stock=Path(tempfile.gettempdir())/'power-grid-env-andes-pycode'
    if not (stock/'Motor5.py').exists():andes.System(no_output=True,default_config=True,pycode_path=str(stock)).prepare(quick=True,nomp=True)
    def forbidden(*args,**kwargs):raise AssertionError('primary or SI helper consumed by native multigen initializer')
    for method in ['steady_response','electrical','rhs','initialize_equilibrium']:monkeypatch.setattr(Circuit,method,forbidden)
    monkeypatch.setattr(MultiCircuit,'initialize_joint',forbidden);monkeypatch.setattr(Generator,'at_terminal',forbidden);monkeypatch.setattr('power_grid_env.study_spec.run_study',forbidden)
    def clear():
        for name in list(sys.modules):
            if name=='pycode' or name.startswith('pycode.'):sys.modules.pop(name,None)
    clear()
    try:r=worker(spec,.00025)
    finally:clear()
    assert r['completed'] and r['stock_generated_cache_unchanged']
    assert r['initialization']['maximum_generator_initial_rhs']<1e-8
    assert r['initialization']['generator_initial_q_var_by_id']==pytest.approx(expected['generator_initial_q_var'],abs=.01)
    for g in spec.network.generators:
        for key in ('delta','omega','e1q','e1d','e2d','e2q','TGOV1.valve_pu','SEXS.field_pu'):
            channel=g.id+'.'+key;assert r['channels'][channel][0]==pytest.approx(expected_signals[channel],abs=1e-8)


def test_passport_order_alone_does_not_change_machine_parameters_or_response(references):
    s,p,i=references('governor2');swapped=s.model_copy(deep=True);swapped.coupled.machines.reverse()
    q=run_study(swapped,isolate=True,wall_timeout_s=90.);assert q['completed']
    j=independent_circuit(swapped).run(q)
    assert invariant(s,p,q)['passed'] and invariant(s,i,j)['passed']
