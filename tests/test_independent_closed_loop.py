import copy
import numpy as np
import pytest
from benchmarks.closed_loop_acceptance import document,compare,CHANNELS
from benchmarks.closed_loop_reference import Controller,run,validate
from benchmarks.pwm_accuracy_budget import choose,summary_case,REPEATS
from power_grid_env.rotor_flux_control import RotorFluxController
from power_grid_env.switching_drive import SwitchingDriveStudy,run_switching_drive


def test_separately_expressed_controller_matches_measured_input_contract():
    p=document('nominal');a=Controller(p);validated=SwitchingDriveStudy.model_validate(p);b=RotorFluxController(validated.control,validated.motor)
    for k in range(200):
        t=k*.0004;current=complex(2*np.sin(70*t),3*np.cos(60*t));speed=12*np.sin(10*t);enabled=k%11!=0
        u=a.command(t,current,speed,540.,.0004,enabled);v,_=b.update(t,current,speed,540.,.0004,enabled)
        assert abs(u-v)<1e-9
        a.measure(current,speed,.0004);b.observe(current,speed,.0004)
        assert abs(a.psi-b.flux)<1e-12


@pytest.mark.parametrize('kind',['core','cages','thermal','saturation','switching','device_r','observer','initial_trip'])
def test_independent_reference_refuses_unsupported_profiles(kind):
    p=document('nominal')
    if kind=='core':p['motor']['core_loss_s']=.001
    if kind=='cages':p['motor']['cages']*=2
    if kind=='thermal':p['motor']['thermal']={'ambient_c':20.}
    if kind=='saturation':p['motor']['saturation_beta_per_vs']=.8
    if kind=='switching':p['switching_on_j']=.001
    if kind=='device_r':p['thermal']['resistance_alpha_per_k']=.001
    if kind=='observer':p['observer_mode']='multicage_current'
    if kind=='initial_trip':p['thermal']['initial_c'][0]=p['thermal']['trip_c'][0]
    with pytest.raises(ValueError):validate(p)


@pytest.mark.parametrize('name',['nominal','precharge'])
def test_full_independent_short_trajectory_matches_runtime(name):
    p=document(name);p['duration_s']=.006 if name=='precharge' else .002
    a=run_switching_drive(p);b=run(p);check=compare(p,a,b)
    assert check['passed'] and b['maximum_energy_residual_j']<1e-5
    assert len(b['rows'][0])==len(CHANNELS)


def test_truncated_full_loop_output_is_rejected():
    p=document('nominal');p['duration_s']=.002
    a=run_switching_drive(p);b=dict(time_s=[0.],rows=[[0.]*len(CHANNELS)])
    with pytest.raises(ValueError,match='coverage'):compare(p,a,b)


def test_calibration_selects_only_candidates_within_common_budget():
    rows=[dict(check=dict(passed=False),measurement=dict(wall_s=.001),request=dict(value=1)),dict(check=dict(passed=True),measurement=dict(wall_s=.3),request=dict(value=2)),dict(check=dict(passed=True),measurement=dict(wall_s=.2),request=dict(value=3))]
    assert choose(rows)==3
    with pytest.raises(ValueError):choose(rows[:1])


def test_resource_summary_requires_full_repeat_inventory_and_accuracy():
    calibration=[dict(check=dict(passed=True),measurement=dict(wall_s=.1),request=dict(provider=p,value=.0001)) for p in ['primary','reference']]
    repetitions=[dict(request=dict(provider=p,value=.0001),check=dict(passed=True),measurement=dict(wall_s=.1,cpu_s=.09,peak_rss_kib=100)) for p in ['primary','reference'] for _ in range(REPEATS)]
    oracle=dict(errors=dict(flux_vs=0.,current_a=0.,speed_rad_s=0.,torque_nm=0.))
    assert summary_case('nominal',calibration,repetitions,oracle)['passed']
    repetitions[0]['check']['passed']=False
    assert not summary_case('nominal',calibration,repetitions,oracle)['passed']
    with pytest.raises(ValueError,match='inventory'):summary_case('nominal',calibration,repetitions[:-1],oracle)


def test_reference_executes_with_runtime_imports_forbidden():
    import json,subprocess,sys
    p=document('nominal');p['duration_s']=.001
    code="""
import importlib.abc,json,sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,*args):
        if fullname.split('.')[0]=='power_grid_env':raise ImportError('runtime import forbidden')
sys.meta_path.insert(0,Block())
from benchmarks.closed_loop_reference import run
r=run(json.load(sys.stdin))
print(json.dumps({'samples':len(r['time_s']),'residual':r['maximum_energy_residual_j']}))
"""
    result=subprocess.run([sys.executable,'-c',code],input=json.dumps(p),capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    r=json.loads(result.stdout);assert r['samples']==6 and r['residual']<1e-6


def test_mixed_oracle_keeps_dynamic_increments_resolved_at_near_coincident_edges():
    from benchmarks.switching_stage_reference import solve_stage
    p=document('nominal');state=dict(flux=[.3,.1,.28,.09],line=[0.,0.,0.],voltage=[0.,0.,0.,270.,-270.,0.,0.,0.],speed=10.,temperature=[25.,25.,25.])
    q=solve_stage(p,state,.01,1e-12,np.array([[1,0],[0,1],[0,1]],bool),True)
    assert q['residual_max']<2e-6
    increment=np.asarray(q['flux_increment_mid'])
    assert np.all(np.isfinite(increment)) and np.max(abs(increment))<1e-8
    np.testing.assert_allclose(np.asarray(q['flux_mid']),np.array(state['flux'])+increment,atol=1e-15)
