"""Read-only semantic audit, coherent corruption detection and full campaign replay."""
from pathlib import Path
import copy,hashlib,json,shutil
import pytest
from power_grid_env.motor_restart import MotorRestartProtocol,run_motor_restart
from power_grid_env.motor_restart_audit import verify_motor_restart,replay_motor_restart
from power_grid_env.study_spec import StudySpec


def example():return StudySpec.model_validate_json(Path('cases/studies/motor_contactor_staggered_group.json').read_text())
def protocol():
    p=MotorRestartProtocol.model_validate_json(Path('cases/restart/direct-on-line-group.json').read_text());p.candidates=p.candidates[:2];return p


def signature(root):return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def reseal(root,name,change):
    path=root/name;value=json.loads(path.read_text());change(value);path.write_text(json.dumps(value))
    manifest=json.loads((root/'manifest.json').read_text());manifest[name]=hashlib.sha256(path.read_bytes()).hexdigest();(root/'manifest.json').write_text(json.dumps(manifest))


@pytest.fixture(scope='module')
def campaign(tmp_path_factory):
    root=tmp_path_factory.mktemp('audited-campaign');r=run_motor_restart(example(),protocol(),root);assert r['completed'];return root


def test_verification_is_read_only_recomputes_criteria_and_does_not_simulate(campaign,monkeypatch):
    import power_grid_env.motor_restart_audit as audit
    before=signature(campaign);monkeypatch.setattr(audit,'run_motor_restart',lambda *a,**k:pytest.fail('static check cannot simulate'))
    r=verify_motor_restart(campaign)
    assert r['verified'] and not r['trajectory_resimulated'] and r['candidates_within_sampled_limits']==['simultaneous']
    assert not r['provenance_authenticated'] and not r['industrial_profile_qualified'] and not r['field_validated']
    assert before==signature(campaign)


@pytest.mark.parametrize('defect',['checksum','extra_file','extra_dir','symlink','unsafe_manifest','missing_candidate','summary','study','raw_speed','promoted','nonfinite','nonfinite_metadata','duplicate_key','runtime','code','fractional_count','envelope'])
def test_invalid_or_coherently_rehashed_claims_never_verify(campaign,tmp_path,monkeypatch,defect):
    import power_grid_env.motor_restart_audit as audit
    root=tmp_path/'copy';shutil.copytree(campaign,root)
    monkeypatch.setattr(audit,'run_motor_restart',lambda *a,**k:pytest.fail('verification must not execute'))
    if defect=='checksum':(root/'base-study.json').write_bytes((root/'base-study.json').read_bytes()+b' ')
    elif defect=='extra_file':(root/'unlisted.json').write_text('{}')
    elif defect=='extra_dir':(root/'nested').mkdir()
    elif defect=='symlink':
        p=root/'case-000/result.json';p.unlink();p.symlink_to(campaign/'case-000/result.json')
    elif defect=='unsafe_manifest':
        p=root/'manifest.json';v=json.loads(p.read_text());v['../outside']='0'*64;p.write_text(json.dumps(v))
    elif defect=='missing_candidate':
        shutil.rmtree(root/'case-001');p=root/'manifest.json';v=json.loads(p.read_text());v={k:d for k,d in v.items() if not k.startswith('case-001/')};p.write_text(json.dumps(v))
    elif defect=='summary':reseal(root,'run.json',lambda v:v.update(candidates_within_sampled_limits=['staggered']))
    elif defect=='study':reseal(root,'case-000/study.json',lambda v:v['induction']['motors'][0].update(inertia_s=2.))
    elif defect=='raw_speed':reseal(root,'case-000/result.json',lambda v:v['channels']['m.speed_pu'].__setitem__(-1,.1))
    elif defect=='promoted':reseal(root,'run.json',lambda v:v.update(field_validated=True))
    elif defect=='nonfinite':
        p=root/'run.json';v=json.loads(p.read_text());v['elapsed_s']='OVERFLOW';p.write_bytes(json.dumps(v).encode().replace(b'"OVERFLOW"',b'1e309'));manifest=json.loads((root/'manifest.json').read_text());manifest['run.json']=hashlib.sha256(p.read_bytes()).hexdigest();(root/'manifest.json').write_text(json.dumps(manifest))
    elif defect=='nonfinite_metadata':
        p=root/'case-000/result.json';v=json.loads(p.read_text());v['execution']['elapsed_s']='OVERFLOW';p.write_bytes(json.dumps(v).encode().replace(b'"OVERFLOW"',b'1e309'));manifest=json.loads((root/'manifest.json').read_text());manifest['case-000/result.json']=hashlib.sha256(p.read_bytes()).hexdigest();(root/'manifest.json').write_text(json.dumps(manifest))
    elif defect=='duplicate_key':
        p=root/'run.json';p.write_bytes(b'{"status":"completed",'+p.read_bytes().lstrip()[1:]);manifest=json.loads((root/'manifest.json').read_text());manifest['run.json']=hashlib.sha256(p.read_bytes()).hexdigest();(root/'manifest.json').write_text(json.dumps(manifest))
    elif defect=='runtime':reseal(root,'environment.json',lambda v:v.update(python='different-runtime'))
    elif defect=='code':reseal(root,'run.json',lambda v:v.update(code_sha256='0'*64))
    elif defect=='fractional_count':reseal(root,'case-000/result.json',lambda v:v['channels']['m.contactor_restart_count'].__setitem__(0,.5))
    elif defect=='envelope':reseal(root,'run.json',lambda v:v['sampled_envelope']['m']['final_speed_pu'].update(maximum=10.))
    with pytest.raises((ValueError,OSError)):verify_motor_restart(root)


def test_full_replay_uses_creation_time_tolerances_and_preserves_original(campaign,tmp_path):
    before=signature(campaign);r=replay_motor_restart(campaign,tmp_path/'replay')
    assert r['completed'] and r['repeatable'] and r['candidate_decisions_match']
    assert all(c['event_audit_matches'] for c in r['cases'])
    assert r['repeatability']==json.loads((campaign/'protocol.json').read_text())['repeatability']
    assert before==signature(campaign)
    root=tmp_path/'replay'
    for name,digest in json.loads((root/'replay-manifest.json').read_text()).items():assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest


def test_static_decisions_are_not_a_physical_trajectory_authentication(campaign,tmp_path):
    root=tmp_path/'modified';shutil.copytree(campaign,root)
    # This state is not used by the stored engineering criteria. Static audit
    # can check those decisions, but only a fresh solve can check the waveform.
    reseal(root,'case-000/result.json',lambda v:v['channels']['m.e_real_pu'].__setitem__(20,v['channels']['m.e_real_pu'][20]+.01))
    assert verify_motor_restart(root)['verified']
    r=replay_motor_restart(root,tmp_path/'different-wave')
    assert r['completed'] and not r['repeatable'] and r['status']=='outside_repeatability_limits'
    assert r['candidate_decisions_match'] and 'm.e_real_pu' in r['cases'][0]['first_failed_channels']


def test_incomplete_and_negative_complete_experiments_are_distinct(tmp_path):
    root=tmp_path/'cancel';r=run_motor_restart(example(),protocol(),root,cancelled=lambda:True);assert not r['completed']
    with pytest.raises(ValueError,match='completed experiment'):verify_motor_restart(root)
    p=protocol()
    for o in p.observations:o.minimum_final_speed_pu=10.
    root=tmp_path/'negative';r=run_motor_restart(example(),p,root);assert r['completed'] and not r['candidates_within_sampled_limits']
    assert verify_motor_restart(root)['verified']


def test_cancelled_replay_and_mutated_original_never_pass(campaign,tmp_path):
    r=replay_motor_restart(campaign,tmp_path/'cancelled',cancelled=lambda:True)
    assert not r['completed'] and not r['repeatable'] and r['status']=='cancelled'
    root=tmp_path/'mutable';shutil.copytree(campaign,root);mutated=False
    def change():
        nonlocal mutated
        if not mutated:
            p=root/'run.json';p.write_bytes(p.read_bytes()+b' ');mutated=True
        return False
    r=replay_motor_restart(root,tmp_path/'raced',cancelled=change)
    assert not r['repeatable'] and not r['completed'] and r['status']=='verification_failed_after_replay'


def test_cli_cannot_write_into_original_and_schema_freezes_tolerances(campaign,tmp_path):
    from power_grid_env.cli import main
    from power_grid_env.schema_export import export_schemas
    before=signature(campaign)
    assert main(['motor-restart-check',str(campaign),'--out',str(campaign/'bad.json')])==2
    assert main(['motor-restart-replay',str(campaign),'--out',str(campaign/'bad')])==2
    assert main(['motor-restart-check',str(campaign),'--out',str(tmp_path/'check.json')])==0
    assert json.loads((tmp_path/'check.json').read_text())['verified']
    export_schemas(tmp_path/'schemas');schema=json.loads((tmp_path/'schemas/motor-restart.schema.json').read_text());assert 'RestartRepeatability' in schema['$defs']
    assert before==signature(campaign)


def test_audit_file_budget_is_enforced_before_semantic_parsing(campaign,monkeypatch):
    import power_grid_env.motor_restart_audit as audit
    monkeypatch.setattr(audit,'MAX_ARTIFACT_BYTES',1024)
    with pytest.raises(ValueError,match='byte budget'):verify_motor_restart(campaign)


@pytest.mark.parametrize('key,expected,actual',[('restart_count',1,2),('phase_defined_above_voltage_pu',1e-12,1e-8)])
def test_discrete_counts_and_fixed_phase_definition_are_not_relaxed_by_pu_tolerance(key,expected,actual):
    from power_grid_env.motor_bundle import _event_matches
    from power_grid_env.motor_restart import RestartRepeatability
    assert not _event_matches({key:actual},{key:expected},RestartRepeatability(pu_absolute=10.))
