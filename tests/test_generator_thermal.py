"""Analytic heating, atomically latched generator trips and shared-network physics."""
import copy,math
from functools import lru_cache
from types import SimpleNamespace
import numpy as np
import pytest
from pydantic import ValidationError
from power_grid_env.generator_thermal import GeneratorThermalProtection,GeneratorThermalBank
from benchmarks.generator_thermal_cases import example,NAMES,NEGATIVE
from benchmarks.generator_thermal_validation import full_compare,physical_contract,baseline,trips
from benchmarks.multibus_operating_point import independent_circuit
from power_grid_env.study_spec import run_study


@pytest.fixture(scope='module')
def reference():
    @lru_cache(None)
    def get(name):
        s=example(name);p=run_study(s,isolate=True,wall_timeout_s=90.);assert p['completed'],p
        return s,p,independent_circuit(s).run(p)
    return get


@pytest.mark.parametrize('name',NAMES)
def test_protected_genrou_network_matches_independent_windings_before_and_after_trip(name,reference):
    s,p,i=reference(name)
    assert full_compare(s,p,i)['passed']
    assert physical_contract(s,p)['passed'] and physical_contract(s,i)['passed']
    assert len(trips(p))==int(name not in ('equilibrium_hold','manual_open'))


@pytest.mark.parametrize('field,value',[('time_constant_s',0.),('time_constant_s',float('nan')),('trip_level',0.),('initial_level',-1.),('initial_level',float('inf')),('model','OEM49')])
def test_unphysical_or_undeclared_relay_is_rejected_by_schema(field,value):
    data=dict(generator_id='g',time_constant_s=.1,trip_level=1.);data[field]=value
    with pytest.raises(ValidationError):GeneratorThermalProtection(**data)


@pytest.mark.parametrize('name',NEGATIVE)
def test_invalid_thermal_target_or_initial_state_never_runs_as_unprotected(name):
    s=example(name);r=run_study(s,isolate=True,wall_timeout_s=90.)
    assert r.get('completed') is False
    assert r['reason']=='generator_initial_thermal_limit' if name=='initial_limit' else r['reason']=='coupled_scope'
    with pytest.raises(ValueError):independent_circuit(s)


def test_native_adapter_rejects_protection_semantics_before_spawning(monkeypatch):
    from benchmarks.andes_multibus_motor import native_reference
    monkeypatch.setattr('benchmarks.andes_multibus_motor.subprocess.Popen',lambda *a,**k:pytest.fail('unsupported protected native worker launched'))
    with pytest.raises(ValueError,match='pre-trip only'):native_reference(example('constant_cold'),.00025)


def bank_fixture(initial=(0.,0.),thresholds=(1.,1.)):
    configs=[GeneratorThermalProtection(generator_id=gid,time_constant_s=2.,trip_level=threshold,initial_level=value) for gid,threshold,value in zip(['a','b'],thresholds,initial)]
    model=SimpleNamespace(generators=[SimpleNamespace(id='a'),SimpleNamespace(id='b')],settings=SimpleNamespace(generator_thermal=configs),gen_connected=np.array([True,True]))
    model.electrical=lambda state:(np.array([1.,1.]),None,None,None)
    model.generator_currents=lambda state,v:np.array([2.,2.])
    return model,GeneratorThermalBank(model,0)


def test_constant_current_replica_matches_exact_heating_and_cooling():
    from scipy.integrate import solve_ivp
    model,bank=bank_fixture();state=np.zeros(2);assert bank.initialize(state,np.array([2.,2.])) is None
    sol=solve_ivp(lambda t,x:bank.derivatives(x,np.array([2.,2.])),(0.,.5),state,rtol=1e-11,atol=1e-13)
    assert sol.y[:,-1]==pytest.approx(np.full(2,4*(1-math.exp(-.5/2))),abs=1e-10)
    off=solve_ivp(lambda t,x:bank.derivatives(x,np.zeros(2)),(0.,.5),sol.y[:,-1],rtol=1e-11,atol=1e-13)
    assert off.y[:,-1]==pytest.approx(sol.y[:,-1]*math.exp(-.5/2),abs=1e-10)


def test_exact_relay_ties_trip_atomically_without_resetting_any_states():
    model,bank=bank_fixture();state=np.ones(2);before=state.copy();log=[];bank.apply({0},.5,state,log)
    assert np.array_equal(state,before) and not model.gen_connected.any()
    assert [e['equipment_id'] for e in log]==['a','b']
    assert bank.guards()==([],[])


def test_nearby_but_distinct_relay_threshold_is_not_coalesced():
    model,bank=bank_fixture(thresholds=(1.,1.+1e-7));state=np.ones(2);log=[];bank.apply({0},.5,state,log)
    assert model.gen_connected.tolist()==[False,True]
    assert len(log)==1 and bank.guards()[0]==[1]


def test_manual_open_prevents_future_heating_trip_and_does_not_set_thermal_latch(reference):
    s,r,_=reference('manual_open');assert not trips(r)
    assert r['channels']['g2.connected'][-1]==0 and r['channels']['g2.stator_thermal_latched'][-1]==0
    assert r['channels']['g2.stator_thermal_level'][-1]>0


def test_thermal_latch_survives_cooling_and_rotor_is_not_frozen(reference):
    s,r,_=reference('constant_cold');assert r['channels']['g2.stator_thermal_level'][-1]<.001
    assert r['channels']['g2.stator_thermal_latched'][-1]==1
    assert r['channels']['g2.omega'][-1]!=pytest.approx(1.,abs=1e-5)
    assert r['channels']['g2.terminal_voltage_pu'][-1]>0


def test_thermal_initialization_uses_generator_own_current_base():
    from power_grid_env.coupled_rms import CoupledNetwork
    from power_grid_env.coupled_initialization import initialize_coupled
    s=example('equilibrium_hold');a=CoupledNetwork(s.network,s.coupled);x,meta=initialize_coupled(a);assert x is not None
    bspec=s.model_copy(deep=True);next(p for p in bspec.coupled.machines if p.generator_id=='g2').rated_va*=2
    b=CoupledNetwork(bspec.network,bspec.coupled);y,meta=initialize_coupled(b);assert y is not None
    address=a.generator_thermal.entries[1][0];other=b.generator_thermal.entries[1][0]
    assert x[address]==pytest.approx(4*y[other],rel=1e-8)


def test_effective_protection_is_rejected_for_unqualified_modal_analysis():
    s=example('equilibrium_hold');s.study='coupled_small_signal'
    r=run_study(s);assert not r['completed'] and r['reason']=='generator_thermal_hybrid_modal_analysis_unsupported'


@pytest.mark.parametrize('defect',['state_reset','residual_current','cooled_unlatch','frozen_rotor'])
def test_physical_or_channel_audit_rejects_corrupted_posttrip_trace(defect,reference):
    s,p,i=reference('constant_cold');r=copy.deepcopy(p)
    first=next(k for k,v in enumerate(r['channels']['g2.connected']) if v==0)
    if defect=='state_reset':r['channels']['g2.e1q'][first]=0.
    elif defect=='residual_current':r['channels']['g2.current_pu'][-1]=.1
    elif defect=='cooled_unlatch':r['channels']['g2.stator_thermal_latched'][-1]=0
    else:
        for k in range(first,len(r['time_s'])):r['channels']['g2.delta'][k]=r['channels']['g2.delta'][first]
    assert not physical_contract(s,r)['passed'] or not full_compare(s,r,i)['passed']


def test_benchmark_protocol_precedes_any_native_baseline(tmp_path,monkeypatch):
    import json
    from benchmarks import generator_thermal_validation as v
    def stop(*args,**kwargs):
        p=json.loads((tmp_path/'protocol.json').read_text())
        assert p['native_post_trip_comparison'] is False and p['limits']['event_s']==1e-6
        raise RuntimeError('preflight')
    monkeypatch.setattr(v,'native_reference',stop)
    with pytest.raises(RuntimeError,match='preflight'):v.run(tmp_path,1)
    assert json.loads((tmp_path/'summary.json').read_text())['passed'] is False


def test_classical_generator_uses_the_same_own_current_thermal_law():
    from power_grid_env.rms_study import ClassicalMachine
    s=example('constant_cold');old=s.coupled.machines[1]
    s.coupled.machines[1]=ClassicalMachine(generator_id=old.generator_id,rated_va=old.rated_va,inertia_s=old.inertia_s,damping_pu=old.damping_pu,x_d_prime_pu=old.x_d_prime_pu,governor=old.governor,exciter=old.exciter)
    r=run_study(s,isolate=True,wall_timeout_s=90.);assert r['completed'],r
    trip=trips(r)[0]['time_s'];heat=r['channels']['g2.current_pu'][0]**2
    expected=.1*math.log(heat/(heat-.003))
    assert abs(trip-expected)<1e-8 and r['channels']['g2.connected'][-1]==0


def test_motor_start_current_pulse_is_filtered_by_generator_thermal_memory():
    import json
    from pathlib import Path
    from power_grid_env.study_spec import StudySpec
    s=StudySpec.model_validate(json.loads(Path('cases/studies/generator_thermal_motor_start.json').read_text()))
    p=run_study(s,isolate=True,wall_timeout_s=90.)
    assert p['completed'] and not trips(p)
    relay=s.coupled.generator_thermal[0]
    assert max(p['channels']['g2.current_pu'])**2>relay.trip_level
    assert max(p['channels']['g2.stator_thermal_level'])<relay.trip_level
    assert all(p['channels']['g2.connected'])
    i=independent_circuit(s).run(p)
    assert full_compare(s,p,i)['passed']
    assert physical_contract(s,p)['passed'] and physical_contract(s,i)['passed']
