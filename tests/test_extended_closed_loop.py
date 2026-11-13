"""Independent nonlinear full-drive reference: physical and evidence contracts."""
import copy
import itertools
import json
import subprocess
import sys
import numpy as np
import pytest
from benchmarks.extended_closed_loop_acceptance import document,compare
from benchmarks.extended_closed_loop_reference import run,channels,terminal_current,magnetic_energy,stage_currents,validate
from power_grid_env.switching_drive import SwitchingDrive,SwitchingDriveStudy,run_switching_drive


@pytest.mark.parametrize('name',['saturated_single_core','double_cage','saturated_four_core','warm_four_winding'])
def test_independent_full_electrothermal_trajectory_matches_every_winding(name):
    p=document(name);p['duration_s']=.004;p['grid_envelope']=[(0.,1.)];p['gate_block_windows']=[]
    a=run_switching_drive(p);b=run(p);check=compare(p,a,b)
    assert check['passed'],check
    assert len(b['row_channels'])==len(set(b['row_channels']))==len(b['rows'][0])
    assert b['maximum_energy_residual_j']<1e-6
    assert set(check['heat_errors_j'])=={'motor_core_heat_j','motor_stator_heat_j','motor_rotor_heat_j'}


@pytest.mark.parametrize('phases',list(itertools.product([0,1,2],repeat=3)))
def test_endpoint_core_feedthrough_solves_all_27_switch_states(phases):
    p=document('warm_four_winding');m=SwitchingDrive(SwitchingDriveStudy.model_validate(p));gates=np.array([[s==1,s==2] for s in phases])
    state=dict(flux=m.flux.copy(),voltage=m.voltage.copy(),winding_temperature=m.winding_temperature.copy())
    before=copy.deepcopy(state);actual=terminal_current(p,state,gates)
    expected=m.magnetics.terminal_current(m.flux,m.voltage[3:5],gates,p['mosfet_resistance_ohm'],p['body_diode_resistance_ohm'],p['body_diode_drop_v'],m.winding_temperature)
    np.testing.assert_allclose([actual.real,actual.imag],expected[:2],atol=1e-8,rtol=1e-10)
    for k in state:np.testing.assert_array_equal(state[k],before[k])


@pytest.mark.parametrize('exponent',[1,2,7,12])
@pytest.mark.parametrize('mode',['unequal','equal_radius','tiny_increment'])
def test_independent_discrete_gradient_retains_exact_radial_energy(exponent,mode):
    m=document('warm_four_winding')['motor'];m['saturation_exponent']=exponent
    old=np.tile([.5,.1],5);old[2:]*=.94
    new=old+np.tile([.003,-.002],5)
    if mode=='equal_radius':new=old.reshape(-1,2)@np.array([[np.cos(.01),-np.sin(.01)],[np.sin(.01),np.cos(.01)]]);new=new.ravel()
    if mode=='tiny_increment':new=old+np.tile([1e-12,-2e-12],5)
    m['core_loss_s']=0.;mid=(new+old)/2;is_,ir=stage_currents(m,old,mid,(new-old)/2,1e-5);current=np.r_[is_.real,is_.imag,np.column_stack([ir.real,ir.imag]).ravel()]
    energy_change=magnetic_energy(m,new)-magnetic_energy(m,old)
    assert energy_change==pytest.approx(1.5*current@(new-old),abs=2e-12,rel=1e-9)


@pytest.mark.parametrize('kind',['switching','device_r','observer','locked','initial_trip','initial_winding_trip'])
def test_extended_reference_refuses_unqualified_event_physics(kind):
    p=document('warm_four_winding')
    if kind=='switching':p['switching_on_j']=.001
    if kind=='device_r':p['thermal']['resistance_alpha_per_k']=.001
    if kind=='observer':p['observer_mode']='multicage_current'
    if kind=='locked':p['motor']['locked_rotor']=True
    if kind=='initial_trip':p['thermal']['initial_c'][0]=p['thermal']['trip_c'][0]
    if kind=='initial_winding_trip':p['motor_initial_temperature_c'][0]=p['motor_temperature_trip_c'][0]
    with pytest.raises(ValueError):validate(p)


@pytest.fixture(scope='module')
def short_pair():
    p=document('warm_four_winding');p['duration_s']=.002
    return p,run_switching_drive(p),run(p)


@pytest.mark.parametrize('mutation',['truncated','inventory','nan','missing_cage','wrong_heat'])
def test_comparison_refuses_incomplete_or_corrupted_evidence(short_pair,mutation):
    p,a,b=copy.deepcopy(short_pair)
    if mutation=='truncated':b['time_s'].pop()
    if mutation=='inventory':b['row_channels']=b['row_channels'][::-1]
    if mutation=='nan':b['rows'][1][2]=float('nan')
    if mutation=='missing_cage':b['rows'][1].pop()
    if mutation=='wrong_heat':b['motor_rotor_heat_j']+=1.
    if mutation=='wrong_heat':assert not compare(p,a,b)['passed']
    else:
        with pytest.raises(ValueError):compare(p,a,b)


def test_extended_reference_runs_without_any_production_imports():
    p=document('warm_four_winding');p['duration_s']=.001
    script="""
import importlib.abc,json,sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,*args):
        if fullname.split('.')[0]=='power_grid_env':raise ImportError('production import forbidden')
sys.meta_path.insert(0,Block())
from benchmarks.extended_closed_loop_reference import run
r=run(json.load(sys.stdin));print(json.dumps({'samples':len(r['rows']),'channels':len(r['row_channels']),'energy':r['maximum_energy_residual_j']}))
"""
    r=subprocess.run([sys.executable,'-c',script],input=json.dumps(p),capture_output=True,text=True,timeout=45)
    assert r.returncode==0,r.stderr
    result=json.loads(r.stdout);assert result['samples']==6 and result['channels']==25 and result['energy']<1e-6


@pytest.mark.parametrize('mode',['converging','diverging','nonfinite','roundoff'])
def test_refinement_requires_reduction_in_each_physical_group(mode):
    from benchmarks.extended_closed_loop_refinement import assessment
    p=document('warm_four_winding');n=len(channels(p));a=np.zeros((2,n));b=np.full((2,n),.004);c=np.full((2,n),.005)
    if mode=='diverging':c[:]=.01
    if mode=='nonfinite':c[:,channels(p).index('motor_current_alpha_a')]=float('nan')
    if mode=='roundoff':a[:]=0.;b[:]=1e-16;c[:]=3e-16
    check=assessment(p,a,b,c)
    assert check['passed']==(mode in {'converging','roundoff'})
    assert set(check['groups'])=={'network','machine_observer','thermal'}


@pytest.mark.parametrize('kind',['roundoff','larger_error','time','counter','boolean','keys','nan'])
def test_replay_tolerance_never_relaxes_events_structure_or_counters(kind):
    from benchmarks.numerical_replay import assert_replay_equal
    a=dict(time_s=[0.,.001],rows=[[1.]],steps=100,passed=True);b=copy.deepcopy(a)
    if kind=='roundoff':b['rows'][0][0]+=1e-12
    if kind=='larger_error':b['rows'][0][0]+=1e-8
    if kind=='time':b['time_s'][1]+=1e-10
    if kind=='counter':b['steps']+=1
    if kind=='boolean':b['passed']=False
    if kind=='keys':b['extra']=0
    if kind=='nan':b['rows'][0][0]=float('nan')
    if kind=='roundoff':assert_replay_equal(a,b)
    else:
        with pytest.raises(ValueError):assert_replay_equal(a,b)


def test_three_cage_inventory_also_matches_independent_network_and_heat():
    p=document('warm_four_winding');p['duration_s']=.003;p['motor']['cages'].pop();p['motor_initial_fluxes_ab_vs'].pop()
    a=run_switching_drive(p);b=run(p)
    assert compare(p,a,b)['passed'] and len(channels(p))==23


@pytest.mark.parametrize('name',['saturated_single_core','double_cage','saturated_four_core','warm_four_winding'])
def test_public_study_fixtures_match_registered_inputs(name):
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'cases'/'closed-loop-extended-independent'/(name+'.json')
    assert json.loads(path.read_text())==document(name)


def test_numpy_float_outputs_have_same_replay_contract_as_saved_json():
    from benchmarks.numerical_replay import assert_replay_equal
    current={'rows':[[np.float64(1.),np.float64(.1)]],'steps':2}
    saved=json.loads(json.dumps(current));assert_replay_equal(saved,current)


@pytest.mark.parametrize('channel,change,passed',[('source_current_a',.005,True),('source_current_a',.02,False),('rotor_cage_4_flux_alpha_vs',2e-5,False),('gates_enabled',1.,False)])
def test_temporal_caps_keep_physical_units_and_discrete_gates(channel,change,passed):
    from benchmarks.extended_temporal_budget import change_budget
    p=document('warm_four_winding');keys=channels(p);a=np.zeros((2,len(keys)));b=a.copy();b[1,keys.index(channel)]=change
    check=change_budget(p,a.tolist(),b.tolist());assert check['passed']==passed
    assert check['errors'][channel]==change


def test_frozen_command_replay_keeps_physical_trace_at_its_source_step():
    from benchmarks.closed_loop_command_replay import trajectory
    p=document('warm_four_winding');p['duration_s']=.002;tape=[]
    a=trajectory(p,tape,True);tape=json.loads(json.dumps(tape,allow_nan=False));b=trajectory(p,tape,False,freeze_pwm=True)
    assert len(tape)==5 and a['channels']==b['channels'] and a['energy']==b['energy']


def test_failed_command_replay_restores_production_factory():
    from benchmarks.closed_loop_command_replay import trajectory
    from power_grid_env import switching_drive as runtime
    original=runtime.SwitchingDrive;p=document('warm_four_winding');p['duration_s']=.002
    with pytest.raises(ValueError,match='exhausted'):trajectory(p,[],False)
    assert runtime.SwitchingDrive is original


@pytest.mark.parametrize('field,value',[('time_s',.001),('period_s',.002)])
def test_command_replay_refuses_a_different_clock(field,value):
    from benchmarks.closed_loop_command_replay import Tape
    row=dict(time_s=0.,period_s=.0004,voltage_ab_v=[1.,2.],control={});row[field]=value
    with pytest.raises(ValueError,match='clock'):Tape([row]).update(0.,0j,0.,540.,.0004,True)


def test_failed_command_replay_restores_pwm_planner_too():
    from benchmarks.closed_loop_command_replay import trajectory
    from power_grid_env import switching_drive as runtime
    original=runtime.pwm_plan;p=document('warm_four_winding');p['duration_s']=.001
    with pytest.raises(ValueError):trajectory(p,[],False,freeze_pwm=True)
    assert runtime.pwm_plan is original
