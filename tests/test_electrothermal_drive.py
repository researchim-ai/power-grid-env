"""PHY-19 independent power/energy identities and real hybrid trajectories."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import numpy as np
import pytest
from benchmarks.electrothermal_drive_acceptance import document,features
from benchmarks.electrothermal_drive_reference import ElectrothermalCircuit,run
from benchmarks.continuous_drive_reference import authenticate


@pytest.fixture(scope='module',autouse=True)
def native():
    wheel=os.environ.get('PGE_MOTULATOR_WHEEL')
    if not wheel:pytest.skip('set PGE_MOTULATOR_WHEEL to pinned 0.23.1 artifact')
    return authenticate(wheel)


@pytest.mark.parametrize('name',['warm_device_losses','brake_trip','winding_trip'])
def test_full_storage_derivative_with_device_rt_brake_and_winding(name):
    c=ElectrothermalCircuit(document(name));x=c.initial();x[:3]=[1.,-.5,0.]
    x[5:9]=[.03,.01,.01,-.02];x[9]=15.;c.mode[:]=[1,-1,0];c.bypassed=True
    c.gates=np.array([[1,0],[0,1],[0,0]],bool)
    dx=c.rhs(.001,x);epsilon=1e-8
    derivative=(c.stored(x+epsilon*dx)-c.stored(x-epsilon*dx))/(2*epsilon)
    assert derivative==pytest.approx(dx[c.base]-dx[c.base+1]-dx[c.base+2],rel=2e-8,abs=1e-3)
    if name=='brake_trip': assert dx[c.base+9]>0


@pytest.mark.parametrize('gates',[[[1,0],[0,1],[0,0]],[[0,1],[0,0],[1,0]],[[1,0],[1,0],[0,1]]])
@pytest.mark.parametrize('warm',[25.,90.])
def test_switching_jump_conserves_total_storage_and_uses_post_on_current(gates,warm):
    c=ElectrothermalCircuit(document('warm_device_losses'));x=c.initial();x[11]=warm
    old=x.copy();before=c.stored(x);c.switch(0.,x,np.array(gates,bool))
    assert c.switching_heat>0
    dc_before=old[3]-old[4];dc_after=x[3]-x[4];effective=c.p['dc_capacitance_f']+c.p['rail_earth_capacitance_f']/2
    assert .5*effective*(dc_before**2-dc_after**2)==pytest.approx(c.switching_heat,abs=1e-10)
    assert (x[11]-old[11])*c.p['thermal']['capacity_j_per_k'][1]==pytest.approx(c.switching_heat,abs=1e-10)
    assert c.stored(x)==pytest.approx(before,abs=1e-9)
    np.testing.assert_array_equal(x[5:10],old[5:10])
    energy=c.switching_heat;count=c.gate_edges;c.switch(0.,x,np.array(gates,bool))
    assert c.switching_heat==energy and c.gate_edges==count


def test_switching_trip_is_latched_and_turnoff_preserves_energy():
    c=ElectrothermalCircuit(document('switching_trip'));x=c.initial();before=c.stored(x)
    c.switch(0.,x,np.array([[1,0],[0,1],[1,0]],bool))
    assert c.tripped() and c.latched[1]
    assert c.thermal_events[0]['cause']=='switching_energy'
    c.switch(0.,x,np.zeros((3,2),bool))
    assert c.stored(x)==pytest.approx(before,abs=1e-9)
    assert not c.gates.any()
    x[11]=25.;c.latch(1.,x)
    assert c.tripped() and len(c.thermal_events)==1


def test_brake_trip_opens_brake_but_retains_passive_circuit():
    c=ElectrothermalCircuit(document('brake_trip'));x=c.initial()
    before=c.rhs(0.,x);assert before[c.base+9]>0
    c.latch(0.,x,forced=[2]);after=c.rhs(0.,x)
    assert after[c.base+9]==0 and c.tripped()
    assert np.all(np.isfinite(after))


@pytest.mark.parametrize('component',range(5))
def test_each_thermal_root_has_upward_direction_and_disappears_after_latch(component):
    c=ElectrothermalCircuit(document('winding_trip'));x=c.initial()
    events,keys=c.guards();index=keys.index(('thermal',component));event=events[index]
    assert event.direction==1 and event.terminal
    assert event(0.,x)<0
    c.latch(.001,x,forced=[component])
    assert ('thermal',component) not in c.guards()[1]


def test_initial_winding_trip_is_recorded_without_flux_reset():
    p=document('winding_trip');p['motor_temperature_trip_c'][0]=85.
    c=ElectrothermalCircuit(p);x=c.initial()
    assert c.tripped() and c.thermal_events[0]['cause']=='initial_temperature'
    np.testing.assert_array_equal(x[5:9],np.asarray(p['motor_initial_fluxes_ab_vs']).ravel())


def test_invalid_gate_overlap_cannot_change_state():
    c=ElectrothermalCircuit(document('warm_device_losses'));x=c.initial();before=x.copy()
    with pytest.raises(ValueError):c.switch(0.,x,np.ones((3,2),bool))
    np.testing.assert_array_equal(x,before)


def test_insufficient_dc_energy_fails_without_physical_mutation():
    p=document('warm_device_losses');p['switching_on_j']=1e9
    c=ElectrothermalCircuit(p);x=c.initial();before=x.copy()
    with pytest.raises(ValueError,match='available DC energy'):c.switch(0.,x,np.array([[1,0],[0,1],[1,0]],bool))
    np.testing.assert_array_equal(x,before)
    assert not c.gates.any() and c.switching_heat==0


@pytest.mark.parametrize('name',['switching_trip','conduction_trip','winding_trip','brake_trip'])
def test_real_reference_hits_required_trip_and_preserves_energy(name):
    result=run(document(name),method='DOP853',rtol=1e-10,atol=1e-12,max_step=2.5e-5)
    assert all(features(name,result).values())
    assert result['metrics']['maximum_energy_relative_error']<1e-8
    assert result['metrics']['maximum_switch_energy_residual_j']<1e-8


def test_reference_rejects_preloaded_runtime_before_reading_input():
    script="import power_grid_env,runpy,sys;sys.argv=['ref','--study','absent','--wheel','absent','--out','absent'];runpy.run_module('benchmarks.electrothermal_drive_reference',run_name='__main__')"
    result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=30)
    assert result.returncode and 'loaded before isolation' in result.stderr


@pytest.fixture(scope='module')
def qualified_corpus():
    root=os.environ.get('PGE_ELECTROTHERMAL_CORPUS')
    if not root:pytest.skip('set PGE_ELECTROTHERMAL_CORPUS for sealed evidence negative tests')
    return Path(root)


def test_saved_corpus_verdict_is_recomputed(qualified_corpus):
    from benchmarks.electrothermal_drive_acceptance import verify_result
    result=verify_result(qualified_corpus,os.environ['PGE_MOTULATOR_WHEEL'])
    assert result['verified'] and result['passed'] and result['cases']==7


@pytest.mark.parametrize('mutation',['limits','reference_identity','solver_controls','primary_producer','waveform','heat','summary','scenario'])
def test_resealed_modified_evidence_cannot_pass(qualified_corpus,tmp_path,mutation):
    import shutil
    from benchmarks.electrothermal_drive_acceptance import verify_result
    from power_grid_env.study_artifacts import read_json,encoded,seal
    root=tmp_path/'forged';shutil.copytree(qualified_corpus,root)
    name='warm_device_losses'
    if mutation=='limits':
        path=root/'protocol.json';value=read_json(path);value['limits']['flux_vs']*=100
    elif mutation=='reference_identity':
        path=root/name/'reference-0.json';value=read_json(path);value['native_source_sha256']={}
    elif mutation=='solver_controls':
        path=root/name/'reference-0.json';value=read_json(path);value['reference']['rtol']=.0001
    elif mutation=='primary_producer':
        path=root/name/'primary-0.json';value=read_json(path);value['code_sha256']='0'*64
    elif mutation=='waveform':
        path=root/name/'primary-0.json';value=read_json(path);value['channels']['motor_stator_flux_alpha_vs'][2]+=.01
    elif mutation=='heat':
        path=root/name/'primary-0.json';value=read_json(path);value['metrics']['switching_heat_j']+=1
    elif mutation=='summary':
        path=root/'summary.json';value=read_json(path);value['rows']=[]
    else:
        path=root/'protocol.json';value=read_json(path);value['cases'][0]='not-registered'
    path.write_bytes(encoded(value));(root/'manifest.json').unlink();seal(root)
    with pytest.raises(ValueError):verify_result(root,os.environ['PGE_MOTULATOR_WHEEL'])


@pytest.mark.parametrize('temperature',[25.,90.])
@pytest.mark.parametrize('gate_code',range(27))
def test_runtime_passive_port_matches_original_native_machine_all_gate_modes(temperature,gate_code):
    from power_grid_env.switching_drive import SwitchingDriveStudy
    from power_grid_env.switching_magnetics import SwitchingMagnetics
    p=document('warm_device_losses');c=ElectrothermalCircuit(p);x=c.initial();x[11]=temperature
    code=gate_code;gates=[]
    for _ in range(3):
        value=code%3;code//=3;gates.append([value==1,value==2])
    gates=np.asarray(gates,bool);c.gates=gates
    factor=1+p['thermal']['resistance_alpha_per_k']*(temperature-25.)
    current=SwitchingMagnetics(SwitchingDriveStudy.model_validate(p).motor).terminal_current(x[5:9],x[3:5],gates,p['mosfet_resistance_ohm']*factor,p['body_diode_resistance_ohm']*factor,p['body_diode_drop_v'],x[13:15])
    native_current=c.port(x)[0]
    np.testing.assert_allclose(current[:2],[native_current.real,native_current.imag],rtol=1e-10,atol=1e-9)


def test_captured_rectifier_hinge_state_matches_external_machine_without_reset():
    from power_grid_env.switching_magnetics import SwitchingMagnetics
    from power_grid_env.flux_machine import FluxMachinePassport
    from power_grid_env.study_artifacts import read_json
    state=read_json(Path(__file__).parent/'fixtures/electrothermal-core-port-hinge.json')
    p=document('rectifier_trip');c=ElectrothermalCircuit(p);x=c.initial()
    x[5:9]=state['flux'];x[3:5]=state['rails'];x[13:15]=state['temperature']
    # The captured channel/diode resistances encode electronics temperature.
    x[11]=25+(state['channel_r']/p['mosfet_resistance_ohm']-1)/p['thermal']['resistance_alpha_per_k']
    c.gates=np.asarray(state['gates'],bool)
    args={k:v for k,v in state.items() if k!='passport'}
    for key in ['flux','rails','gates','temperature']:args[key]=np.asarray(args[key])
    old_flux=args['flux'].copy();old_rails=args['rails'].copy()
    result=SwitchingMagnetics(FluxMachinePassport.model_validate(state['passport'])).terminal_current(**args)
    expected=c.port(x)[0]
    np.testing.assert_allclose(result[:2],[expected.real,expected.imag],atol=1e-9,rtol=1e-10)
    np.testing.assert_array_equal(args['flux'],old_flux);np.testing.assert_array_equal(args['rails'],old_rails)


def test_active_set_fallback_preserves_original_kcl_tolerance_when_newton_stalls(monkeypatch):
    from power_grid_env.switching_magnetics import SwitchingMagnetics
    from power_grid_env.switching_drive import SwitchingDriveStudy
    p=document('warm_device_losses');c=ElectrothermalCircuit(p);x=c.initial()
    gates=np.array([[1,0],[0,1],[0,0]],bool);c.gates=gates
    factor=c.factors(x)[1];args=(x[5:9],x[3:5],gates,p['mosfet_resistance_ohm']*factor,p['body_diode_resistance_ohm']*factor,p['body_diode_drop_v'],x[13:15])
    expected=c.port(x)[0]
    monkeypatch.setattr(np.linalg,'lstsq',lambda a,b,**kwargs:(np.zeros_like(b),None,None,None))
    result=SwitchingMagnetics(SwitchingDriveStudy.model_validate(p).motor).terminal_current(*args)
    np.testing.assert_allclose(result[:2],[expected.real,expected.imag],atol=1e-9,rtol=1e-10)
