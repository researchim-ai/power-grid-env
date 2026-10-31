"""Finite clearing protocols: meaningful metrics, identity and failure semantics."""
from pathlib import Path
import copy,json,hashlib
import pytest
from power_grid_env.study_spec import run_study
from power_grid_env.fault_clearing import FaultClearingProtocol,clearing_variants,assess_clearing,run_fault_clearing
from test_coupled_faults import example


def protocol(**updates):
    data=json.loads(Path('cases/fault-clearing/genrou.json').read_text());data.update(updates)
    return FaultClearingProtocol.model_validate(data)


def test_frozen_duration_experiment_reinitializes_and_persists_identical_hashes(tmp_path):
    s=example();before=s.model_dump_json();p=protocol();r=run_fault_clearing(s,p,tmp_path/'run')
    assert r['completed'] and r['all_cases_within_limits'],r
    assert r['engineering_assessment']=='within_sampled_limits'
    assert not r['critical_clearing_time_certified'] and not r['industrial_profile_qualified']
    assert s.model_dump_json()==before and len(r['cases'])==3
    manifest=json.loads((tmp_path/'run/manifest.json').read_text())
    for name,checksum in manifest.items():assert hashlib.sha256((tmp_path/'run'/name).read_bytes()).hexdigest()==checksum
    for i,row in enumerate(r['cases']):
        d=json.loads((tmp_path/'run'/f'case-{i:03d}/study.json').read_text())
        assert d['coupled']['faults'][0]['clear_s']==pytest.approx(.2+p.durations_s[i])
        assert row['assessment']['motors']['m']['connected_through_window']
    assert not (tmp_path/'run/.clearing.lock').exists()
    with pytest.raises(ValueError):run_fault_clearing(s,p,tmp_path/'run')


def test_numerical_success_can_be_engineering_failure(tmp_path):
    p=protocol(maximum_angle_excursion_rad=.001)
    r=run_fault_clearing(example(),p,tmp_path/'strict')
    assert r['completed'] and not r['all_cases_within_limits']
    assert r['engineering_assessment']=='outside_sampled_limits'
    assert all(c['numerically_completed'] for c in r['cases'])


def test_missing_failed_point_cannot_be_interpreted_as_safe(tmp_path,monkeypatch):
    import power_grid_env.fault_clearing as runner
    original=runner.run_study;count=0
    def failing(*args,**kwargs):
        nonlocal count
        count+=1
        if count==2:return dict(completed=False,status='timeout')
        return original(*args,**kwargs)
    monkeypatch.setattr(runner,'run_study',failing)
    r=run_fault_clearing(example(),protocol(),tmp_path/'partial')
    assert not r['completed'] and not r['all_cases_within_limits']
    assert r['status']=='partial_failure' and r['engineering_assessment']=='inconclusive'
    assert r['cases'][1]['status']=='timeout'


@pytest.mark.parametrize('mode',['cancel','total_timeout','output_budget','worker_timeout'])
def test_resource_and_cancel_failures_are_inconclusive(tmp_path,mode):
    updates={'maximum_output_bytes':300000} if mode=='output_budget' else {'total_timeout_s':1e-12} if mode=='total_timeout' else {'per_case_timeout_s':1e-6} if mode=='worker_timeout' else {}
    r=run_fault_clearing(example(),protocol(**updates),tmp_path/mode,cancelled=(lambda:True) if mode=='cancel' else None)
    assert not r['completed'] and r['engineering_assessment']=='inconclusive',r
    expected={'cancel':'cancelled','total_timeout':'total_timeout','output_budget':'output_budget_exceeded','worker_timeout':'partial_failure'}[mode]
    assert r['status']==expected
    assert sum(p.stat().st_size for p in (tmp_path/mode).rglob('*') if p.is_file())<=protocol(**updates).maximum_output_bytes


def test_stale_source_and_result_identity_refuse_assessment(tmp_path,monkeypatch):
    import power_grid_env.fault_clearing as runner
    s=example();p=protocol();v=clearing_variants(s,p)[0];r=run_study(v);assert r['completed']
    for field,value in [('code_sha256','wrong'),('network_sha256','wrong'),('settings',{}),('time_s',[0.,1.8])]:
        bad=copy.deepcopy(r);bad[field]=value
        with pytest.raises((ValueError,KeyError)):assess_clearing(v,bad,p)
    bad=copy.deepcopy(r);bad['event_log']=[]
    with pytest.raises(ValueError):assess_clearing(v,bad,p)
    bad=copy.deepcopy(r);bad['channels']['m.speed_pu'][-1]=float('nan')
    with pytest.raises(ValueError):assess_clearing(v,bad,p)
    original=runner.package_fingerprint;count=0
    def changed():
        nonlocal count
        count+=1
        return original() if count==1 else 'changed'
    monkeypatch.setattr(runner,'package_fingerprint',changed)
    result=run_fault_clearing(s,p,tmp_path/'stale')
    assert result['status']=='stale_code' and result['engineering_assessment']=='inconclusive'


def test_invalid_protocols_and_targets_reject_without_running(monkeypatch):
    import power_grid_env.fault_clearing as runner
    monkeypatch.setattr(runner,'run_study',lambda *a,**kw:pytest.fail('must reject before worker'))
    for updates in [dict(durations_s=[.1,.1]),dict(durations_s=[.2,.1]),dict(durations_s=[0.,.1])]:
        with pytest.raises(ValueError):protocol(**updates)
    for p in [protocol(fault_id='absent'),protocol(durations_s=[1.,1.5]),protocol(final_dwell_s=.001),protocol(motors=[dict(motor_id='absent',minimum_speed_pu=.9,minimum_voltage_pu=.9)])]:
        with pytest.raises(ValueError):clearing_variants(example(),p)


def test_cli_schema_and_cancel_file(tmp_path):
    from power_grid_env.cli import main
    from power_grid_env.schema_export import export_schemas
    import jsonschema
    study=tmp_path/'study.json';study.write_text(example().model_dump_json())
    p=tmp_path/'protocol.json';p.write_text(protocol().model_dump_json())
    export_schemas(tmp_path/'schemas')
    for name,payload in [('study',example().model_dump(mode='json')),('fault-clearing',protocol().model_dump(mode='json'))]:
        jsonschema.validate(payload,json.loads((tmp_path/'schemas'/f'{name}.schema.json').read_text()))
    cancel=tmp_path/'cancel';cancel.touch()
    assert main(['fault-clearing','--study',str(study),'--protocol',str(p),'--out',str(tmp_path/'cancelled'),'--cancel-file',str(cancel)])==3
    assert main(['fault-clearing','--study',str(study),'--protocol',str(p),'--out',str(tmp_path/'ok')])==0


def test_metadata_overflow_keeps_public_cli_failure_contract(tmp_path,monkeypatch):
    import power_grid_env.fault_clearing as runner
    from power_grid_env.cli import main
    monkeypatch.setattr(runner,'run_study',lambda *a,**kw:dict(completed=True,status='completed'))
    monkeypatch.setattr(runner,'assess_clearing',lambda *a,**kw:dict(within_limits=True,diagnostic='x'*100000))
    study=tmp_path/'study.json';study.write_text(example().model_dump_json())
    p=tmp_path/'protocol.json';p.write_text(protocol().model_dump_json())
    assert main(['fault-clearing','--study',str(study),'--protocol',str(p),'--out',str(tmp_path/'overflow')])==2
    r=json.loads((tmp_path/'overflow/run.json').read_text())
    assert r['status']=='metadata_budget_exceeded' and not r['all_cases_within_limits']
    assert r['engineering_assessment']=='inconclusive'
