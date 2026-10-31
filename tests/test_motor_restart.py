"""Finite restart candidates: identity, complete coverage, evidence and failures."""
from pathlib import Path
import copy,hashlib,json
import numpy as np
import pytest
from power_grid_env.motor_restart import MotorRestartProtocol,build_restart_points,run_motor_restart,_voltage_assessment
from power_grid_env.motor_sensitivity import _assess
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.model import canonical_hash


def example():return StudySpec.model_validate_json(Path('cases/studies/motor_contactor_staggered_group.json').read_text())
def protocol():return MotorRestartProtocol.model_validate_json(Path('cases/restart/direct-on-line-group.json').read_text())


def small_protocol():
    p=protocol();p.candidates=p.candidates[:2];p.baseline_candidate_id='simultaneous';return p


@pytest.fixture(scope='module')
def completed(tmp_path_factory):
    root=tmp_path_factory.mktemp('restart');result=run_motor_restart(example(),protocol(),root)
    return root,result


def test_explicit_absolute_variants_freeze_inputs_and_preserve_physics():
    s,p=example(),protocol();before=(canonical_hash(s),canonical_hash(p));points=build_restart_points(s,p)
    assert points[0]['candidate_id']=='staggered' and points[0]['nominal']
    assert [x['candidate_id'] for x in points]==['staggered','simultaneous','late','outside_horizon']
    assert (canonical_hash(s),canonical_hash(p))==before
    for point in points:
        v=point['study'];assert canonical_hash(v.network)==canonical_hash(s.network)
        assert v.induction.events==s.induction.events
        for original,modified in zip(s.induction.motors,v.induction.motors):
            a=original.model_dump();b=modified.model_dump()
            for key in ['pickup_delay_s','minimum_off_time_s']:a['contactor'].pop(key);b['contactor'].pop(key)
            assert a==b


@pytest.mark.parametrize('defect',['missing_timing','missing_observation','missing_recovery','unknown_node','off_grid_window','no_contactor','missing_baseline'])
def test_incomplete_or_ambiguous_protocol_rejects_before_execution(tmp_path,defect):
    s,p=example(),protocol()
    if defect=='missing_timing':p.candidates[0].timings.pop()
    elif defect=='missing_observation':p.observations.pop()
    elif defect=='missing_recovery':p.observations[0].recovery=None
    elif defect=='unknown_node':p.voltage_windows[0].node_id='missing'
    elif defect=='off_grid_window':p.voltage_windows[0].from_s=.501
    elif defect=='no_contactor':
        for m in s.induction.motors:m.contactor=None
    else:p.baseline_candidate_id='missing'
    with pytest.raises(ValueError):run_motor_restart(s,p,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


def test_real_workers_separate_numerical_success_from_candidate_limits_and_hash_everything(completed):
    root,r=completed
    assert r['completed'] and not r['all_cases_within_limits']
    assert r['candidates_within_sampled_limits']==['simultaneous']
    assert r['engineering_assessment']=='finite_candidates_within_limits'
    assert all(row['numerically_completed'] for row in r['cases'])
    assert not r['automatic_recommendation'] and not r['global_optimum_claim'] and not r['continuous_extrema_certified']
    assert not r['field_validated'] and not r['industrial_profile_qualified']
    assert all(row['voltage_windows']['recovery-bus']['within_limits'] for row in r['cases'])
    assert not r['cases'][-1]['observations']['m']['checks']['connected']
    for name,digest in json.loads((root/'manifest.json').read_text()).items():assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
    assert not (root/'.sensitivity.lock').exists()


def test_post_event_voltage_window_excludes_left_limit_at_start_and_includes_interior_pre_limit(completed):
    root,r=completed;point=r['cases'][1];s=StudySpec.model_validate_json((root/point['id']/'study.json').read_text());report=json.loads((root/point['id']/'result.json').read_text());p=protocol()
    times=np.array(report['time_s']);sides=np.array(report['sample_side']);bus=next(iter(report['mapping']['motor_buses'].values()))
    index=np.flatnonzero((times==.5)&(sides=='pre_event'))[0];report['channels'][bus+'.v'][index]=.01
    assert _voltage_assessment(s,report,p)['additional_within_limits']
    index=np.flatnonzero((times>.5)&(times<1.5)&(sides=='pre_event'))[0];report['channels'][bus+'.v'][index]=2.
    assert not _voltage_assessment(s,report,p)['additional_within_limits']


def test_failed_variant_makes_selection_inconclusive_even_after_a_passing_candidate(tmp_path,monkeypatch):
    import power_grid_env.motor_sensitivity as engine
    original=engine.run_study;calls=0
    def fail_second(*a,**k):
        nonlocal calls
        calls+=1
        return original(*a,**k) if calls==1 else dict(completed=False,status='worker_failed')
    monkeypatch.setattr(engine,'run_study',fail_second)
    r=run_motor_restart(example(),small_protocol(),tmp_path/'partial')
    assert r['cases'][0]['within_limits'] and not r['completed'] and r['status']=='partial_failure'
    assert r['candidates_within_sampled_limits']==[] and r['engineering_assessment']=='inconclusive'


def test_missing_network_channel_and_missing_grid_samples_cannot_pass(tmp_path,monkeypatch):
    import power_grid_env.motor_sensitivity as engine
    original=engine.run_study
    def missing(*args,**kwargs):
        r=original(*args,**kwargs);bus=next(iter(r['mapping']['motor_buses'].values()));del r['channels'][bus+'.v'];return r
    monkeypatch.setattr(engine,'run_study',missing)
    r=run_motor_restart(example(),small_protocol(),tmp_path/'missing');assert not r['completed'] and r['candidates_within_sampled_limits']==[]
    s=example();raw=run_study(s)
    # Remove an internal stretch while preserving lengths and horizon.
    keep=[i for i,t in enumerate(raw['time_s']) if not .1<t<.13]
    for key in ['time_s','sample_side']:raw[key]=[raw[key][i] for i in keep]
    for key in raw['channels']:raw['channels'][key]=[raw['channels'][key][i] for i in keep]
    with pytest.raises(ValueError,match='incomplete sampled'): _assess(s,raw,protocol().observations[0])


def test_cancellation_timeouts_and_output_budget_never_produce_candidates(tmp_path,monkeypatch):
    s,p=example(),small_protocol();r=run_motor_restart(s,p,tmp_path/'cancel',cancelled=lambda:True)
    assert r['status']=='cancelled' and not r['completed'] and r['candidates_within_sampled_limits']==[]
    p.total_timeout_s=1e-6;r=run_motor_restart(s,p,tmp_path/'total');assert r['status']=='total_timeout' and r['candidates_within_sampled_limits']==[]
    p=small_protocol();p.per_case_timeout_s=1e-6;r=run_motor_restart(s,p,tmp_path/'timeout');assert not r['completed'] and r['candidates_within_sampled_limits']==[]
    import power_grid_env.motor_sensitivity as engine
    monkeypatch.setattr(engine,'run_study',lambda *a,**k:dict(completed=False,status='worker_failed',reason='x'*7_000_000))
    p=small_protocol();p.maximum_output_bytes=8_000_000;root=tmp_path/'budget';r=run_motor_restart(s,p,root)
    assert r['status']=='output_budget_exceeded' and r['candidates_within_sampled_limits']==[]
    assert sum(f.stat().st_size for f in root.rglob('*') if f.is_file())<=p.maximum_output_bytes


def test_source_change_after_success_remains_inconclusive(tmp_path,monkeypatch):
    import power_grid_env.motor_sensitivity as engine
    original=engine.package_fingerprint;calls=0
    def changed():
        nonlocal calls
        calls+=1
        return original() if calls<=2 else '0'*64
    monkeypatch.setattr(engine,'package_fingerprint',changed)
    r=run_motor_restart(example(),small_protocol(),tmp_path/'stale')
    assert r['cases'][0]['within_limits'] and not r['completed'] and r['status']=='stale_code' and r['candidates_within_sampled_limits']==[]


def test_cli_and_schema_expose_finite_selection_semantics(tmp_path):
    from power_grid_env.cli import main
    from power_grid_env.schema_export import export_schemas
    from jsonschema import validate
    export_schemas(tmp_path/'schemas');validate(protocol().model_dump(mode='json'),json.loads((tmp_path/'schemas/motor-restart.schema.json').read_text()))
    out=tmp_path/'cli';assert main(['motor-restart','--study','cases/studies/motor_contactor_staggered_group.json','--protocol','cases/restart/direct-on-line-group.json','--out',str(out)])==0
    cancel=tmp_path/'cancel.flag';cancel.touch()
    assert main(['motor-restart','--study','cases/studies/motor_contactor_staggered_group.json','--protocol','cases/restart/direct-on-line-group.json','--out',str(tmp_path/'cancelled'),'--cancel-file',str(cancel)])==3


def test_running_restart_freezes_caller_timings_criteria_and_physical_inputs(tmp_path):
    s,p=example(),small_protocol();identity=canonical_hash(s)
    def mutate():
        s.induction.motors[0].inertia_s=100.
        p.candidates[0].timings[0].pickup_delay_s=10.
        p.observations[0].minimum_final_speed_pu=10.
        return False
    r=run_motor_restart(s,p,tmp_path/'frozen',cancelled=mutate)
    assert r['completed'] and r['candidates_within_sampled_limits']==['simultaneous']
    assert r['base_study_sha256']==identity and r['protocol']['observations'][0]['minimum_final_speed_pu']==.95


def test_complete_but_no_passing_candidate_is_distinct_from_inconclusive(tmp_path):
    p=small_protocol()
    for o in p.observations:o.minimum_final_speed_pu=10.
    r=run_motor_restart(example(),p,tmp_path/'none')
    assert r['completed'] and not r['all_cases_within_limits'] and r['candidates_within_sampled_limits']==[]
    assert r['engineering_assessment']=='no_declared_candidate_within_limits'


def test_runtime_change_after_success_withholds_candidate_list(tmp_path,monkeypatch):
    import power_grid_env.motor_bundle as bundle
    original=bundle.runtime_identity;calls=0
    def changed():
        nonlocal calls
        calls+=1
        r=original()
        if calls>=3:r['python']='changed-during-run'
        return r
    monkeypatch.setattr(bundle,'runtime_identity',changed)
    root=tmp_path/'runtime';r=run_motor_restart(example(),small_protocol(),root)
    assert r['cases'][0]['within_limits'] and not r['completed'] and r['status']=='stale_runtime'
    assert r['candidates_within_sampled_limits']==[] and (root/'environment.json').exists()
