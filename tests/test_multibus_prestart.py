"""DOL breaker closure must preserve stored energy and prestart equilibrium."""
import copy
import numpy as np
import pytest
from benchmarks.multibus_prestart_cases import example,NAMES
from benchmarks.multibus_operating_point import independent_circuit,validate_scope
from benchmarks.multibus_prestart_validation import start_contract
from power_grid_env.study_spec import run_study


@pytest.mark.parametrize('name',NAMES)
def test_prestart_independent_network_matches_primary_and_close_contract(name):
    from benchmarks.multibus_motor_validation import compare_si
    s=example(name);p=run_study(s,isolate=True,wall_timeout_s=90.)
    assert p['completed'],p
    c=independent_circuit(s);i=c.run(p)
    assert compare_si(s,p,i)['passed']
    assert start_contract(s,p)['passed']
    assert start_contract(s,i)['passed']
    assert all(p['channels'][m.id+'.current_pu'][0]==0 for m in (s.coupled or s.induction).motors)


@pytest.mark.parametrize('name',['gen_hold','gen_lv_clock','grid_simultaneous'])
def test_initially_open_rotor_flux_speed_current_and_rhs_are_zero(name):
    c=independent_circuit(example(name));v,current,rot,torque=c.electrical(c.state)
    assert np.max(abs(current))<1e-12
    assert np.max(abs(rot))<1e-12
    assert np.max(abs(c.rhs(0.,c.state)))<1e-8
    assert not c.cfg['connected'].any()
    assert all(c.state[p['speed']]==0 for p in c.p)


def test_mixed_cold_initial_connection_uses_only_energized_stators():
    s=example('gen_staggered');s.coupled.motors[1].connected=True
    c=independent_circuit(s);currents=c.electrical(c.state)[1]
    assert abs(currents[0])<1e-12 and abs(currents[2])<1e-12
    assert abs(currents[1])>1
    assert c.initialization['maximum_generator_initial_rhs']<1e-8


def test_unqualified_loaded_disconnected_equilibrium_fails_explicitly():
    from benchmarks.multibus_loaded_cases import example as loaded
    s=loaded('gen_hold');s.coupled.motors[0].connected=False
    with pytest.raises(ValueError,match='loaded disconnected'):validate_scope(s)


@pytest.mark.parametrize('corruption',['leakage','state_jump','missing_side','missing_close'])
def test_start_contract_detects_incorrect_event_or_hidden_energy_reset(corruption):
    s=example('gen_staggered');r=run_study(s,isolate=True,wall_timeout_s=90.)
    assert start_contract(s,r)['passed']
    if corruption=='leakage':r['channels']['m.current_pu'][0]=.01
    else:
        idx=r['sample_side'].index('post_event')
        if corruption=='state_jump':r['channels']['g.omega'][idx]+=.01
        elif corruption=='missing_side':r['sample_side'][idx]='regular'
        else:r['channels']['m.connected'][idx]=0
    if corruption in ('missing_side','missing_close'):
        with pytest.raises(ValueError):start_contract(s,r)
    else:assert not start_contract(s,r)['passed']


def test_protocol_is_written_before_any_oracle_is_launched(tmp_path,monkeypatch):
    import json
    from benchmarks import multibus_prestart_validation as v
    def stop(*args,**kwargs):
        p=json.loads((tmp_path/'protocol.json').read_text())
        assert p['differential_state_jump_limit']==1e-10 and p['native_steps_s']==[.00025,.000125,.0000625]
        raise RuntimeError('preflight')
    monkeypatch.setattr(v,'native_reference',stop)
    with pytest.raises(RuntimeError,match='preflight'):v.run(tmp_path,['gen_hold'])


def test_native_prestart_uses_own_equilibrium_and_preserves_generator_states(monkeypatch):
    import sys,tempfile
    from pathlib import Path
    from power_grid_env.andes_rms import ensure_andes_importable
    from benchmarks.andes_multibus_motor import worker
    from benchmarks.multibus_si_motor import Circuit
    from benchmarks.multibus_operating_point import CoupledCircuit
    from benchmarks.group_si_generator import Generator
    assert ensure_andes_importable()
    import andes
    stock=Path(tempfile.gettempdir())/'power-grid-env-andes-pycode'
    if not (stock/'Motor5.py').is_file():andes.System(no_output=True,default_config=True,pycode_path=str(stock)).prepare(quick=True,nomp=True)
    def forbidden(*args,**kwargs):raise AssertionError('primary/SI solution used by native prestart')
    for method in ['steady_response','electrical','rhs','initialize_equilibrium']:monkeypatch.setattr(Circuit,method,forbidden)
    monkeypatch.setattr(CoupledCircuit,'initialize_joint',forbidden)
    monkeypatch.setattr(Generator,'at_terminal',forbidden)
    monkeypatch.setattr('power_grid_env.study_spec.run_study',forbidden)
    def clear():
        for key in list(sys.modules):
            if key=='pycode' or key.startswith('pycode.'):sys.modules.pop(key,None)
    clear()
    try:r=worker(example('gen_hold'),.00025)
    finally:clear()
    assert r['completed'] and r['stock_generated_cache_unchanged']
    assert r['initialization']['generator_states_preserved_during_motor_replacement']
    assert start_contract(example('gen_hold'),r,native=True)['passed']
