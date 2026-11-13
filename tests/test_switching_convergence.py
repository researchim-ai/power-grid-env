import copy
import json
import numpy as np
import pytest
from benchmarks.extended_closed_loop_acceptance import document
from power_grid_env.switching_drive import SwitchingDriveStudy,SwitchingDrive,run_switching_drive
from power_grid_env.switching_convergence import SwitchingTemporalLimits,inventory,audited,differences,qualify_switching_drive,run_switching_qualification_artifact
from power_grid_env.study_artifacts import verify,read_json


def short():
    p=document('warm_four_winding');p.update(duration_s=.0012,grid_ln_rms_v=1e-6);return p


@pytest.fixture(scope='module')
def trace():
    p=short();return SwitchingDriveStudy.model_validate(p),run_switching_drive(p)


def test_default_step_budget_preserves_legacy_canonical_json():
    p=document('double_cage');assert 'step_budget' not in p
    assert SwitchingDriveStudy.model_validate(p).step_budget==300000
    with pytest.raises(ValueError,match='step_budget'):SwitchingDriveStudy.model_validate(dict(p,max_step_s=1.5625e-7))
    q=SwitchingDriveStudy.model_validate(dict(p,max_step_s=1.5625e-7,step_budget=600000))
    assert q.model_dump(mode='json')['step_budget']==600000


@pytest.mark.parametrize('value',[0,999,2000001,float('inf')])
def test_step_budget_is_bounded(value):
    with pytest.raises(ValueError):SwitchingDriveStudy.model_validate(dict(short(),step_budget=value))


def test_exhausted_budget_rejects_before_any_physical_advance():
    m=SwitchingDrive(SwitchingDriveStudy.model_validate(short()));m.steps=m.p.step_budget
    names=['line','flux','voltage','temperature','winding_temperature','latched','motor_latched'];before={k:getattr(m,k).copy() for k in names};work=m.work
    with pytest.raises(RuntimeError,match='before state advance'):m.step(0.,1e-6,np.zeros((3,2),bool))
    for k,v in before.items():np.testing.assert_array_equal(getattr(m,k),v)
    assert m.work==work and not m.events


@pytest.mark.parametrize('field',['dc_voltage_v','source_current_a','motor_current_a','flux_vs','speed_rad_s','temperature_c','event_time_s','reduction_factor'])
@pytest.mark.parametrize('value',[0.,float('nan'),float('inf')])
def test_nonphysical_or_nonfinite_limits_rejected(field,value):
    with pytest.raises(ValueError):SwitchingTemporalLimits.model_validate({field:value})


@pytest.mark.parametrize('cages',[1,2,3,4])
def test_every_rotor_cage_has_a_physical_unit_cap(cages):
    p=short();p['motor']['cages']=p['motor']['cages'][:cages];p['motor_initial_fluxes_ab_vs']=p['motor_initial_fluxes_ab_vs'][:cages+1]
    groups,flags=inventory(SwitchingDriveStudy.model_validate(p),SwitchingTemporalLimits())
    assert all(f'rotor_cage_{k}_flux_alpha_vs' in groups['machine_observer'] for k in range(1,cages+1))
    assert groups['thermal']['motor_stator_temperature_c']==.001 and 'motor_rotor_thermal_trip' in flags


@pytest.mark.parametrize('channel,amount',[('source_current_a',.02),('motor_current_alpha_a',.006),('rotor_cage_4_flux_beta_vs',2e-5),('motor_stator_temperature_c',.002),('torque_enabled',1.)])
def test_all_physical_groups_and_discrete_actions_are_enforced(trace,channel,amount):
    p,a=trace;b=copy.deepcopy(a);b['channels'][channel][1]+=amount
    assert not differences(p,a,b,SwitchingTemporalLimits())['passed']


@pytest.mark.parametrize('kind',['truncated','nan','clock','nonbinary','missing_cage','bad_audit','invalid_event'])
def test_incomplete_or_corrupted_trajectory_cannot_qualify(trace,kind):
    p,r=copy.deepcopy(trace)
    if kind=='truncated':r['time_s'].pop()
    if kind=='nan':r['channels']['motor_current_alpha_a'][1]=float('nan')
    if kind=='clock':r['time_s'][1]+=1e-9
    if kind=='nonbinary':r['channels']['gates_enabled'][1]=.5
    if kind=='missing_cage':r['channels'].pop('rotor_cage_4_flux_beta_vs')
    if kind=='bad_audit':r['metrics']['kcl_max_a']=float('nan')
    if kind=='invalid_event':r['event_log'].append(dict(kind='thermal_trip',time_s=.01))
    with pytest.raises((ValueError,KeyError)):audited(p,r,SwitchingTemporalLimits())


def test_event_identity_and_time_are_enforced(trace):
    p,a=copy.deepcopy(trace);a['event_log']=[dict(time_s=0.,kind='thermal_trip',component='motor_stator')];b=copy.deepcopy(a)
    b['event_log'][0]['component']='motor_rotor';assert not differences(p,a,b,SwitchingTemporalLimits())['passed']
    b=copy.deepcopy(a);b['event_log'][0]['time_s']+=1e-6;assert not differences(p,a,b,SwitchingTemporalLimits())['passed']


def test_inactive_voltage_limiter_roundoff_is_separate_from_physical_action(trace):
    p,a=copy.deepcopy(trace);b=copy.deepcopy(a);a['channels']['gates_enabled'][0]=b['channels']['gates_enabled'][0]=0.
    b['channels']['voltage_limited'][0]=1.-a['channels']['voltage_limited'][0]
    assert differences(p,a,b,SwitchingTemporalLimits())['passed']
    a['channels']['gates_enabled'][0]=b['channels']['gates_enabled'][0]=1.
    assert not differences(p,a,b,SwitchingTemporalLimits())['passed']


@pytest.mark.parametrize('levels',[0,2,10,True,3.5])
def test_level_budget_validated_before_creating_artifact(tmp_path,levels):
    out=tmp_path/'bad'
    with pytest.raises(ValueError):run_switching_qualification_artifact(short(),out,max_levels=levels)
    assert not out.exists()


def test_actual_short_qualification_and_artifact_are_reproducible(tmp_path):
    out=tmp_path/'qualification';r=run_switching_qualification_artifact(short(),out,max_levels=3)
    assert r['qualified'] and r['selected_step_s']==2.5e-6 and not r['absolute_error_certified']
    assert len(r['attempts'])==3 and all(all(a['physics_audits'].values()) for a in r['attempts'])
    assert verify(out) and read_json(out/'summary.json')==r
    with pytest.raises(FileExistsError):run_switching_qualification_artifact(short(),out,max_levels=3)


def test_planned_resource_exhaustion_is_saved_without_false_success(tmp_path):
    p=short();p.update(max_step_s=2e-6,step_budget=1000);out=tmp_path/'resource';r=run_switching_qualification_artifact(p,out,max_levels=6)
    assert not r['qualified'] and r['status']=='step_budget_exhausted' and r['attempts'][-1]['status']=='not_run'
    assert (out/'level-0/result.json').exists() and verify(out)


def test_solver_failure_breaks_three_mesh_chain_and_preserves_evidence(tmp_path,monkeypatch,trace):
    from power_grid_env import switching_convergence as module
    p,base=trace;calls=[]
    def solver(study):
        calls.append(study)
        if len(calls)==2:raise RuntimeError('intentional numerical failure')
        return copy.deepcopy(base)
    monkeypatch.setattr(module,'run_switching_drive',solver);out=tmp_path/'failed';r=module.run_switching_qualification_artifact(p.model_dump(mode='json'),out,max_levels=3)
    assert not r['qualified'] and r['attempts'][1]['status']=='solver_failed'
    assert 'intentional' in read_json(out/'level-1/assessment.json')['error'] and verify(out)


def test_small_difference_alone_cannot_hide_nonmonotonicity(monkeypatch,trace):
    from power_grid_env import switching_convergence as module
    p,base=trace;index=[0]
    def solver(study):
        result=copy.deepcopy(base);delta=[0.,1e-7,1e-6][index[0]];index[0]+=1
        result['channels']['motor_stator_flux_alpha_vs'][1]+=delta;return result
    monkeypatch.setattr(module,'run_switching_drive',solver);r=module.qualify_switching_drive(p.model_dump(mode='json'),max_levels=3)
    assert not r['qualified'] and r['attempts'][-1]['difference']['passed'] and not r['attempts'][-1]['reduction']['machine_observer']['passed']


def test_cli_qualifies_and_exports_limits(tmp_path,capsys):
    from power_grid_env.cli import main
    path=tmp_path/'study.json';path.write_text(json.dumps(short()));out=tmp_path/'cli'
    assert main(['switching-drive','qualify','--study',str(path),'--out',str(out),'--max-levels','3'])==0
    printed=json.loads(capsys.readouterr().out);assert printed['qualified'] and not printed['absolute_error_certified']
    assert verify(out) and read_json(out/'protocol.json')['limits']['flux_vs']==1e-5


def test_cli_uses_explicit_resource_budget_and_returns_failed_status(tmp_path,capsys):
    from power_grid_env.cli import main
    p=short();p['max_step_s']=2e-6;path=tmp_path/'study.json';path.write_text(json.dumps(p));out=tmp_path/'cli'
    assert main(['switching-drive','qualify','--study',str(path),'--out',str(out),'--step-budget','1000'])==2
    printed=json.loads(capsys.readouterr().out);assert printed['completed'] and not printed['qualified'] and printed['status']=='step_budget_exhausted'
    assert read_json(out/'study.json')['step_budget']==1000 and verify(out)


def test_cli_respects_custom_unit_limits_without_false_success(tmp_path,capsys):
    from power_grid_env.cli import main
    path=tmp_path/'study.json';path.write_text(json.dumps(short()));limits=tmp_path/'limits.json';limits.write_text(json.dumps(dict(dc_voltage_v=1e-30)));out=tmp_path/'cli'
    assert main(['switching-drive','qualify','--study',str(path),'--out',str(out),'--max-levels','3','--limits',str(limits)])==2
    assert not json.loads(capsys.readouterr().out)['qualified'] and read_json(out/'summary.json')['limits']['dc_voltage_v']==1e-30


def test_physics_audit_failure_cannot_be_hidden_by_identical_traces(monkeypatch,trace):
    from power_grid_env import switching_convergence as module
    p,base=trace;r=copy.deepcopy(base);r['metrics']['final_energy_relative_error']=.01
    monkeypatch.setattr(module,'run_switching_drive',lambda study:copy.deepcopy(r))
    result=module.qualify_switching_drive(p.model_dump(mode='json'),max_levels=3)
    assert not result['qualified'] and result['attempts'][-1]['difference']['passed']


def test_nonfinite_solver_result_is_saved_as_failed_attempt(tmp_path,monkeypatch,trace):
    from power_grid_env import switching_convergence as module
    p,base=trace;r=copy.deepcopy(base);r['channels']['source_current_a'][1]=float('nan')
    monkeypatch.setattr(module,'run_switching_drive',lambda study:copy.deepcopy(r));out=tmp_path/'nonfinite'
    result=module.run_switching_qualification_artifact(p.model_dump(mode='json'),out,max_levels=3)
    assert not result['qualified'] and all(a['status']=='solver_failed' for a in result['attempts'])
    assert not (out/'level-0/result.json').exists() and verify(out)


def test_numpy_scalar_physics_metrics_can_be_serialized_without_losing_audits(trace):
    p,result=copy.deepcopy(trace)
    for k in ['final_energy_relative_error','kcl_max_a','motor_constitutive_residual_v']:result['metrics'][k]=np.float64(result['metrics'][k])
    audits=audited(p,result,SwitchingTemporalLimits())
    assert all(type(v) is bool for v in audits.values()) and json.loads(json.dumps(audits))==audits
