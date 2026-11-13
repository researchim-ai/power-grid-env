"""Adaptive trials must own their state and preserve native accepted ledgers."""
import numpy as np
import pytest
from benchmarks.extended_closed_loop_acceptance import document,NAMES
from benchmarks.switching_hotpath_reference import LegacyDrive
from power_grid_env.switching_drive import SwitchingDrive,SwitchingDriveStudy
from power_grid_env.switching_adaptive import clone_trial,AdaptiveMidpointControl,_project_source
from power_grid_env.switching_convergence import qualify_switching_drive


def model(name='double_cage',**control):
    p=document(name);p['adaptive']=control
    return SwitchingDrive(SwitchingDriveStudy.model_validate(p))


def snapshot(m):
    return {k:v.copy() if isinstance(v,np.ndarray) else list(v) if isinstance(v,list) else v for k,v in vars(m).items() if isinstance(v,(np.ndarray,list,float,int,bool)) and not k.startswith('adaptive_') and k not in {'newton_calls','linear_direct_solves','linear_least_squares_solves','linear_direct_fallbacks','_adaptive_next_h'}}


def unchanged(a,m):
    b=snapshot(m)
    assert a.keys()==b.keys()
    for k in a:np.testing.assert_equal(a[k],b[k],err_msg=k)


@pytest.mark.parametrize('name',NAMES)
def test_trial_arrays_and_event_logs_are_isolated(name):
    m=model(name);before=snapshot(m);trial=clone_trial(m)
    for k,v in vars(trial).items():
        if isinstance(v,np.ndarray) and v.size and k in {'line','flux','voltage','temperature','winding_temperature','latched','motor_latched','previous_gates','_old_rails'}:v.flat[0]=not v.flat[0] if v.dtype==bool else 123.
    trial.events.append({'kind':'trial'});trial.adaptive_mesh_events.append({'kind':'trial'})
    unchanged(before,m);assert not m.adaptive_mesh_events


@pytest.mark.parametrize('name',NAMES)
def test_accepted_state_and_ledger_are_exact_native_half_steps(name):
    m=model(name);native=clone_trial(m);gates=np.zeros((3,2),bool);h=1e-9
    actual,q=m.step(0.,h,gates)
    assert actual==h and m.adaptive_rejections==0
    a,qa=native._midpoint_step(0.,h/2,gates);b,qb=native._midpoint_step(a,h/2,gates)
    unchanged(snapshot(native),m)
    assert q['observer_current_integral_as']==a*complex(*qa['currents_mid'][:2])+b*complex(*qb['currents_mid'][:2])
    assert q['observer_speed_integral_rad']==a*qa['speed_mid']+b*qb['speed_mid']
    assert q['interval_body_diode_heat_j']==a*qa['body_diode_power_w']+b*qb['body_diode_power_w']
    assert m.adaptive_probe_evaluations==2


def test_probe_budget_failure_rolls_back_physics():
    m=model(trial_budget=1);before=snapshot(m)
    with pytest.raises(RuntimeError,match='probe budget'):m.step(0.,1e-6,np.zeros((3,2),bool))
    unchanged(before,m);assert m.newton_calls>0 and m.adaptive_probe_evaluations==1


def test_solver_failure_rolls_back_and_bounds_retries(monkeypatch):
    m=model(min_step_s=1e-6);before=snapshot(m)
    def fail(self,*args):
        self.flux[:]=100;self.events.append({'kind':'bad'})
        raise RuntimeError('injected solve failure')
    monkeypatch.setattr(SwitchingDrive,'_midpoint_step',fail)
    with pytest.raises(RuntimeError,match='min_step_s'):m.step(0.,1e-6,np.zeros((3,2),bool))
    unchanged(before,m);assert m.adaptive_solver_failures==1


@pytest.mark.parametrize('name',NAMES)
def test_disabled_path_retains_frozen_native_state(name):
    p=SwitchingDriveStudy.model_validate(document(name));assert 'adaptive' not in p.model_dump(mode='json')
    a=SwitchingDrive(p);b=LegacyDrive(p);gates=np.zeros((3,2),bool)
    x,q=a.step(0.,1e-6,gates);y,r=b.step(0.,1e-6,gates)
    assert x==y
    for k in q:np.testing.assert_array_equal(q[k],r[k],err_msg=k)
    unchanged(snapshot(b),a)


def test_source_projection_has_separate_bounded_energy():
    m=model();trial=clone_trial(m);trial.line[:]=[1e-8,-1e-8,0.]
    loss=trial.external_loss
    energy,values=_project_source(m,dict(state=trial,source_phases=[0,1]))
    assert energy==pytest.approx(m.p.source_inductance_h*1e-16)
    assert values==[1e-8,-1e-8] and trial.external_loss==loss
    np.testing.assert_array_equal(trial.line,0.)
    trial.line[0]=1.
    with pytest.raises(RuntimeError,match='unresolved'):_project_source(m,dict(state=trial,source_phases=[0]))


@pytest.mark.parametrize('key,value',[('flux_vs',float('nan')),('source_current_a',float('inf')),('trial_budget',0),('local_floor_fraction',0),('error_budget_fraction',0),('min_step_s',0)])
def test_invalid_controls(key,value):
    with pytest.raises(ValueError):AdaptiveMidpointControl.model_validate({key:value})


def test_fixed_mesh_certificate_refuses_adaptive():
    with pytest.raises(ValueError,match='adaptive=None'):qualify_switching_drive(model().p.model_dump(mode='json'))


def test_winding_trip_localizes_without_destroying_flux():
    from benchmarks.switching_thermal_cases import document as thermal_document
    d=thermal_document('cold_winding');d['adaptive']={}
    m=SwitchingDrive(SwitchingDriveStudy.model_validate(d));m.flux[:2]=[.3,.1]
    m.p.motor_temperature_trip_c=(20.000001,140.)
    before=m.flux.copy();h,q=m.step(.01,1e-5,np.array([[1,0],[0,1],[0,1]],bool))
    assert 0<h<1e-5 and m.motor_latched[0]
    assert m.winding_temperature[0]==pytest.approx(20.000001,abs=1e-8)
    assert np.linalg.norm(m.flux-before)<.01 and np.linalg.norm(m.flux)>0
    assert m.adaptive_event_steps==1 and len(m.events)==1


def test_accepted_step_budget_fails_before_probing():
    m=model();m.steps=m.p.step_budget-1;before=snapshot(m)
    with pytest.raises(RuntimeError,match='two accepted'):m.step(0.,1e-6,np.zeros((3,2),bool))
    unchanged(before,m);assert m.adaptive_probe_evaluations==0


@pytest.mark.parametrize('h',[0.,-1.,float('nan'),float('inf')])
def test_invalid_interval_preserves_physics(h):
    m=model();before=snapshot(m)
    with pytest.raises(ValueError,match='positive finite'):m.step(0.,h,np.zeros((3,2),bool))
    unchanged(before,m)


def test_acceptance_rejects_false_refinement_protocol_before_output(tmp_path):
    from benchmarks.switching_adaptive_acceptance import run
    out=tmp_path/'invalid'
    with pytest.raises(ValueError,match='strictly decreasing'):run(out,tmp_path,fractions=(.2,.2,.2))
    assert not out.exists()


def test_diode_zero_restart_reports_tiny_correction_and_balances_energy():
    from power_grid_env.switching_drive import run_switching_drive
    from power_grid_env.switching_convergence import audited,SwitchingTemporalLimits
    d=document('double_cage');d.update(duration_s=.001,adaptive={})
    r=run_switching_drive(d);events=r['integration_event_log'];info=r['integration']
    assert events and info['source_boundaries']==len(events)
    times=[v['time_s'] for v in events]
    assert all(a<b for a,b in zip(times,times[1:]))
    assert 0<times[0]<=times[-1]<=d['duration_s']
    assert all(abs(v)<=1e-7 for e in events for v in e['current_correction_a'])
    assert info['source_event_energy_correction_j']==sum(e['energy_correction_j'] for e in events)<1e-12
    assert info['root_probes']>0 and info['rejections']>0
    assert info['probe_evaluations']>info['accepted_pairs']*2
    assert all(audited(SwitchingDriveStudy.model_validate(d),r,SwitchingTemporalLimits()).values())
    assert not info['absolute_error_certified']


def test_diagnostic_wrapper_restores_estimator_on_rejection(monkeypatch):
    from benchmarks import switching_adaptive_diagnostic as diagnostic
    import power_grid_env.switching_adaptive as adaptive
    original=adaptive._error
    def reject(doc):
        assert adaptive._error is not original
        raise RuntimeError('injected minimum-step rejection')
    monkeypatch.setattr(diagnostic,'run_switching_drive',reject)
    result=diagnostic.diagnose()
    assert not result['completed'] and 'injected' in result['error']
    assert adaptive._error is original


def hinge_model(adaptive=True):
    from pathlib import Path
    from power_grid_env.study_artifacts import read_json
    d=read_json(Path(__file__).parent/'fixtures/switching_adaptive_hinge.json')
    p=dict(d['study'])
    if not adaptive:p.pop('adaptive',None)
    m=SwitchingDrive(SwitchingDriveStudy.model_validate(p))
    for k,v in d['state'].items():
        previous=getattr(m,k)
        setattr(m,k,np.asarray(v,dtype=previous.dtype) if isinstance(previous,np.ndarray) else v)
    m.initial_energy=m.stored()
    return m,d


def test_weak_motor_hinge_no_longer_looks_like_local_error():
    from power_grid_env.switching_adaptive import _probe,_error
    m,d=hinge_model();g=np.asarray(d['gates'],bool);t=d['time_s'];h=d['step_s']
    coarse=_probe(m,t,h,g,False);fine=_probe(m,t,h,g,True)
    assert _error(m,coarse,fine,g,m.p.adaptive)<.001
    # Original failure jumped between ~-53 V and +246 V on the free pole.
    assert abs(coarse['state'].voltage[5]-fine['state'].voltage[5])<1e-4
    assert coarse['state'].voltage[5]<-299.
    assert max(coarse['state'].magnetic_residual_max,fine['state'].magnetic_residual_max)<1e-8


def test_historical_hinge_failure_remains_reproducible_with_frozen_solver():
    from benchmarks.switching_hotpath_reference import LegacyDrive
    m,d=hinge_model(False);legacy=LegacyDrive(m.p)
    for k in d['state']:setattr(legacy,k,getattr(m,k).copy() if isinstance(getattr(m,k),np.ndarray) else getattr(m,k))
    a=clone_trial(legacy);b=clone_trial(legacy);g=np.asarray(d['gates'],bool);t=d['time_s'];h=d['step_s']
    a.step(t,h,g);b.step(t,h/2,g);b.step(t+h/2,h/2,g)
    assert np.max(abs(a.flux-b.flux))>1e-8


@pytest.mark.parametrize('factor',[.5,1.,2.,10.])
def test_hinge_candidate_passes_independent_mixed_equations(factor):
    from benchmarks.switching_stage_reference import solve_stage
    m,d=hinge_model();g=np.asarray(d['gates'],bool);h=d['step_s']*factor;t=d['time_s']
    q=m.candidate(t,h,g)
    ref=solve_stage(m.p.model_dump(mode='json'),d['state'],t,h,g,m.bypassed)
    np.testing.assert_allclose(q['flux_mid'],ref['flux_mid'],rtol=0,atol=1e-10)
    np.testing.assert_allclose(q['voltage'][5:],ref['voltage'][5:],rtol=0,atol=1e-3)


def test_scaled_circuit_policy_is_available_for_fixed_mesh_studies():
    m,d=hinge_model();fixed,unused=hinge_model(False)
    fixed.p.circuit_solver='motor_scaled'
    g=np.asarray(d['gates'],bool);t=d['time_s'];h=d['step_s']
    a=m.candidate(t,h,g);b=fixed.candidate(t,h,g)
    for k in a:np.testing.assert_array_equal(a[k],b[k],err_msg=k)
    doc=fixed.p.model_dump(mode='json');assert doc['circuit_solver']=='motor_scaled' and 'adaptive' not in doc


def test_circuit_policy_rejects_unknown_solver():
    d=document('double_cage');d['circuit_solver']='guess'
    with pytest.raises(ValueError):SwitchingDriveStudy.model_validate(d)


@pytest.mark.parametrize('change',[{'max_step_s':1e-6},{'max_step_s':5e-6,'load_torque_nm':100.},{'max_step_s':5e-6,'adaptive':{}}])
def test_fixed_extension_rejects_changed_physics_or_wrong_mesh(change):
    from benchmarks.switching_hinge_tail import compatible
    a=document('double_cage');a['max_step_s']=1e-5;b=dict(a,**change)
    with pytest.raises(ValueError):compatible(a,b)


def test_fixed_extension_accepts_only_half_step_change():
    from benchmarks.switching_hinge_tail import compatible
    a=document('double_cage');a.update(max_step_s=1e-5,circuit_solver='motor_scaled')
    b=dict(a,max_step_s=5e-6)
    assert compatible(a,b).max_step_s==5e-6


def motor_zero_model(fixture="switching_motor_zero.json"):
    from pathlib import Path
    from power_grid_env.study_artifacts import read_json
    d=read_json(Path(__file__).parent/'fixtures'/fixture)
    m=SwitchingDrive(SwitchingDriveStudy.model_validate(d['study']))
    for k,v in d['state'].items():
        old=getattr(m,k);setattr(m,k,np.asarray(v,dtype=old.dtype) if isinstance(old,np.ndarray) else v)
    m.initial_energy=m.stored();m._adaptive_next_h=d.get('proposal_s',d['step_s'])
    return m,d


@pytest.mark.parametrize('gated,initial,final,expected',[
    (False,1.,-1.,[0]),(True,1.,-1.,[]),(False,1e-12,-1e-12,[]),
    (False,1.,.5,[]),(False,-1.,1.,[0]),(False,1.,0.,[0])])
def test_motor_zero_requires_a_resolved_sign_change_on_an_open_channel(gated,initial,final,expected):
    from power_grid_env.switching_adaptive import _motor_crossings
    g=np.zeros((3,2),bool);g[0,0]=gated
    assert _motor_crossings(np.array([initial,0,0]),np.array([final,0,0]),g,AdaptiveMidpointControl())==expected


def test_motor_zero_accepts_native_states_without_flux_projection():
    from power_grid_env.switching_adaptive import _motor_tolerance
    from power_grid_env.pwm_bridge import T
    m,d=motor_zero_model();before=clone_trial(m);g=np.asarray(d['gates'],bool);energy=m.stored()
    h,q=m.step(d['time_s'],d['step_s'],g)
    assert 0<h<d['step_s'] and m.adaptive_motor_boundaries==1
    event=m.adaptive_mesh_events[-1]
    assert event['kind']=='motor_current_zero' and event['flux_projection'] is False
    assert max(abs(np.asarray(event['current_residual_a'])))<=_motor_tolerance(m.p.adaptive)
    assert np.linalg.norm(m.flux)>0 and np.linalg.norm(m.flux-before.flux)<1e-3
    native=clone_trial(before)
    for k in range(m.steps):native._midpoint_step(d['time_s']+k*h/m.steps,h/m.steps,g)
    for key in ['flux','line','voltage','temperature','winding_temperature']:np.testing.assert_array_equal(getattr(m,key),getattr(native,key),err_msg=key)
    assert m.work==native.work and m.external_loss==native.external_loss
    assert m.stored()-energy==pytest.approx(m.work-m.external_loss-m.mechanical_work,abs=1e-7)
    for _ in range(8):
        t=d['time_s']+h;dt,q=m.step(t,1e-7,g);h+=dt
    assert m.adaptive_motor_boundaries==1


def test_motor_zero_half_steps_match_independent_mixed_network():
    from benchmarks.switching_stage_reference import solve_stage
    m,d=motor_zero_model();g=np.asarray(d['gates'],bool)
    h,q=m.step(d['time_s'],d['step_s'],g);native,unused=motor_zero_model();parts=m.steps
    for k in range(parts):
        dt=h/parts;t=d['time_s']+k*dt
        state={name:getattr(native,name).tolist() for name in ['flux','line','voltage','temperature','winding_temperature']};state['speed']=native.speed
        ref=solve_stage(native.p.model_dump(mode='json'),state,t,dt,g,native.bypassed)
        _,q=native._midpoint_step(t,dt,g)
        np.testing.assert_allclose(q['flux_mid'],ref['flux_mid'],rtol=0,atol=1e-10)
        np.testing.assert_allclose(q['voltage'],ref['voltage'],rtol=0,atol=1e-3)


@pytest.mark.parametrize('value',[0.,-1.,float('nan'),float('inf'),1.])
def test_invalid_motor_event_current_tolerance(value):
    with pytest.raises(ValueError):AdaptiveMidpointControl(motor_event_current_a=value)


def test_ablation_rejects_modified_driver_before_executing_it(tmp_path):
    from benchmarks.switching_motor_zero_ablation import frozen_driver
    source=tmp_path/'untrusted.py';marker=tmp_path/'marker'
    source.write_text(f'open({str(marker)!r}, "w").write("executed")')
    with pytest.raises(ValueError,match='hash differs'):frozen_driver(source)
    assert not marker.exists()


def test_vector_zero_groups_coincident_legs_despite_opposite_signed_roundoff():
    from power_grid_env.switching_adaptive import _motor_crossings
    cfg=AdaptiveMidpointControl();initial=np.array([0.,-1e-6,1e-6]);g=np.zeros((3,2),bool)
    for residual in [np.array([1e-16,-2e-16,1e-16]),np.array([-1e-16,2e-16,-1e-16])]:
        assert _motor_crossings(initial,residual,g,cfg)==[1,2]


def test_coincident_gate_block_event_accepts_without_altering_native_state():
    m,d=motor_zero_model('switching_motor_coincident.json');before=clone_trial(m);g=np.asarray(d['gates'],bool)
    h,q=m.step(d['time_s'],d['step_s'],g)
    assert m.adaptive_solver_failures==0 and m.adaptive_motor_boundaries==1
    event=m.adaptive_mesh_events[-1];assert event['phases']==[1,2] and not event['flux_projection']
    assert max(abs(np.asarray(event['current_residual_a'])))<=m.p.adaptive.motor_event_current_a
    native=clone_trial(before)
    for k in range(m.steps):native._midpoint_step(d['time_s']+k*h/m.steps,h/m.steps,g)
    for name in ['flux','line','voltage','temperature','winding_temperature']:np.testing.assert_array_equal(getattr(m,name),getattr(native,name))
    assert m.work==native.work and m.external_loss==native.external_loss


def test_signed_only_detector_reproduces_coincident_event_failure_and_rolls_back(monkeypatch):
    import power_grid_env.switching_adaptive as adaptive
    m,d=motor_zero_model('switching_motor_coincident.json');before=snapshot(m)
    def old_detector(initial,current,gates,cfg):
        eligible=(abs(initial)>adaptive._motor_tolerance(cfg))&(~np.any(gates,axis=1))
        return np.flatnonzero(eligible&(initial*current<=0.)).tolist()
    monkeypatch.setattr(adaptive,'_motor_crossings',old_detector)
    with pytest.raises(RuntimeError,match='current event identities differ'):m.step(d['time_s'],d['step_s'],np.asarray(d['gates'],bool))
    unchanged(before,m)


def test_ablation_restores_driver_when_baseline_run_fails(monkeypatch,tmp_path):
    import hashlib
    import types
    import power_grid_env.switching_adaptive as adaptive
    from benchmarks import switching_motor_zero_ablation as ablation
    reference=tmp_path/'reference.json';reference.write_text('{}');sha=hashlib.sha256(reference.read_bytes()).hexdigest()
    doc=document('double_cage');doc['adaptive']={'error_budget_fraction':.1};expected=SwitchingDriveStudy.model_validate(doc).model_dump(mode='json')
    original=adaptive.adaptive_step;replacement=object()
    monkeypatch.setattr(ablation,'frozen_driver',lambda path:types.SimpleNamespace(adaptive_step=replacement))
    monkeypatch.setattr(ablation,'verify',lambda path:'identity')
    monkeypatch.setattr(ablation,'package_fingerprint',lambda:'producer')
    def read(path):
        if path.name=='protocol.json':return dict(code_sha256='producer',references={'double_cage':sha})
        if path.name=='study.json':return expected
        return {}
    monkeypatch.setattr(ablation,'read_json',read)
    def fail(doc):
        assert adaptive.adaptive_step is replacement
        raise RuntimeError('injected baseline failure')
    monkeypatch.setattr(ablation,'run_switching_drive',fail)
    with pytest.raises(RuntimeError,match='injected baseline'):ablation.run(tmp_path/'driver',tmp_path/'current',tmp_path/'history',reference,tmp_path/'out')
    assert adaptive.adaptive_step is original and not (tmp_path/'out').exists()


def mesh_result():
    return dict(metrics={'steps':2},integration=dict(absolute_error_certified=False,trials=1,rejections=0,accepted_pairs=1,probe_evaluations=2,root_probes=0,motor_boundaries=1,source_boundaries=0,source_event_energy_correction_j=0.),integration_event_log=[dict(time_s=.001,kind='motor_current_zero',phases=[0],current_residual_a=[1e-9],flux_projection=False)])


def test_qualification_accepts_consistent_motor_event_metadata():
    from benchmarks.switching_adaptive_budget_qualification import mesh_audit
    assert mesh_audit(model().p,mesh_result())


@pytest.mark.parametrize('change',['unresolved','projection','phases','clock','count','work','absolute'])
def test_qualification_rejects_corrupt_or_unresolved_event_evidence(change):
    from benchmarks.switching_adaptive_budget_qualification import mesh_audit
    r=mesh_result();e=r['integration_event_log'][0];i=r['integration']
    if change=='unresolved':e['current_residual_a']=[1.]
    if change=='projection':e['flux_projection']=True
    if change=='phases':e['phases']=[0,0];e['current_residual_a']=[0.,0.]
    if change=='clock':e['time_s']=-1.
    if change=='count':i['motor_boundaries']=0
    if change=='work':i['accepted_pairs']=False
    if change=='absolute':i['absolute_error_certified']=True
    with pytest.raises(ValueError):mesh_audit(model().p,r)


def test_reported_sample_clock_and_gate_permission_at_block_boundaries():
    from power_grid_env.switching_drive import run_switching_drive
    p=document('saturated_single_core');p.update(duration_s=.03,gate_block_windows=[(.024,.028)])
    result=run_switching_drive(p);times=np.asarray(result['time_s'])
    np.testing.assert_array_equal(times,np.minimum(np.arange(len(times))*p['sample_s'],p['duration_s']))
    start=np.flatnonzero(times==.024);end=np.flatnonzero(times==.028)
    assert len(start)==len(end)==1
    assert result['channels']['gates_enabled'][start[0]]==0.
    assert result['channels']['gates_enabled'][end[0]]==1.


def test_reported_partial_period_ends_at_exact_requested_duration():
    from power_grid_env.switching_drive import run_switching_drive
    p=document('double_cage');p['duration_s']=.00107
    result=run_switching_drive(p)
    expected=np.r_[np.arange(6)*p['sample_s'],p['duration_s']]
    np.testing.assert_array_equal(result['time_s'],expected)
    assert all(len(v)==len(expected) for v in result['channels'].values())
