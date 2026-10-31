"""Portable motor study artifacts, repeatability and strict failure semantics."""
from pathlib import Path
import copy,hashlib,json,shutil
import pytest
from power_grid_env.study_spec import StudySpec
from power_grid_env.motor_trace import MotorTraceProtocol
from power_grid_env.motor_bundle import MotorReplayCriteria,export_motor_bundle,verify_motor_bundle,replay_motor_bundle


def spec():return StudySpec.model_validate_json(Path('cases/studies/thermal_cold_trip.json').read_text())
def criteria():return MotorReplayCriteria.model_validate_json(Path('cases/replay/motor-start.json').read_text())


def create(root,with_trace=False):
    kwargs={}
    if with_trace:
        kwargs=dict(trace=Path('cases/measurements/synthetic_motor_cold_trip.csv'),trace_protocol=MotorTraceProtocol.model_validate_json(Path('cases/measurements/synthetic_motor_cold_trip.protocol.json').read_text()))
    return export_motor_bundle(spec(),criteria(),root,**kwargs)


def reseal(root,name,mutate):
    p=root/name;d=json.loads(p.read_text());mutate(d);p.write_text(json.dumps(d))
    m=json.loads((root/'manifest.json').read_text());m['files'][name]=hashlib.sha256(p.read_bytes()).hexdigest();(root/'manifest.json').write_text(json.dumps(m))


@pytest.fixture(scope='module')
def sealed(tmp_path_factory):
    root=tmp_path_factory.mktemp('bundle')/'sealed';r=create(root,True)
    assert r['completed'] and r['measurement_comparison_passed']
    return root


def test_export_verify_and_complete_channel_replay_without_repository_cwd(sealed,tmp_path,monkeypatch):
    before={p.name:p.read_bytes() for p in sealed.iterdir()}
    monkeypatch.chdir(tmp_path)
    s,c,result,comparison,signature=verify_motor_bundle(sealed)
    assert result['completed'] and comparison['passed'] and len(signature)==64
    replay=replay_motor_bundle(sealed)
    assert replay['completed'] and replay['repeatable'] and replay['event_audit_matches']
    assert set(replay['metrics'])==set(result['channels'])
    assert not replay['field_validated'] and not replay['industrial_profile_qualified']
    assert before=={p.name:p.read_bytes() for p in sealed.iterdir()}


@pytest.mark.parametrize('defect',['tamper','extra','missing','symlink','nested','unsafe_inventory','promoted_claim','stale_code','runtime','invalid_discrete','invalid_side','invalid_event'])
def test_corrupt_or_incompatible_bundles_reject_before_execution(sealed,tmp_path,defect,monkeypatch):
    import power_grid_env.motor_bundle as bundle
    root=tmp_path/'bundle';shutil.copytree(sealed,root)
    monkeypatch.setattr(bundle,'run_study',lambda *a,**k:pytest.fail('invalid bundle must not execute'))
    if defect=='tamper':(root/'study.json').write_text('{}')
    elif defect=='extra':(root/'hidden.txt').write_text('unexpected')
    elif defect=='missing':(root/'result.json').unlink()
    elif defect=='symlink':
        (root/'criteria.json').unlink();(root/'criteria.json').symlink_to(sealed/'criteria.json')
    elif defect=='nested':(root/'nested').mkdir()
    elif defect in {'unsafe_inventory','promoted_claim','stale_code'}:
        p=root/'manifest.json';d=json.loads(p.read_text())
        if defect=='unsafe_inventory':d['files']['../outside']='0'*64
        elif defect=='promoted_claim':d['field_validated']=True
        else:d['code_sha256']='0'*64
        p.write_text(json.dumps(d))
    elif defect=='runtime':reseal(root,'environment.json',lambda d:d.update(python='0.0.0'))
    elif defect=='invalid_discrete':reseal(root,'result.json',lambda d:d['channels']['m.connected'].__setitem__(0,.5))
    elif defect=='invalid_side':reseal(root,'result.json',lambda d:d['sample_side'].__setitem__(0,'unknown'))
    elif defect=='invalid_event':reseal(root,'result.json',lambda d:d['event_log'][0].update(time_s=float('inf')))
    with pytest.raises((ValueError,OSError)):replay_motor_bundle(root)


def test_frozen_repeatability_limits_can_fail_without_numerical_failure(sealed,tmp_path,monkeypatch):
    import power_grid_env.motor_bundle as bundle
    root=tmp_path/'bundle';shutil.copytree(sealed,root)
    # Remove trace artifacts so this test concerns numerical replay, not CSV scoring.
    manifest=json.loads((root/'manifest.json').read_text())
    for name in ['trace.csv','trace-protocol.json','comparison.json']:(root/name).unlink();del manifest['files'][name]
    (root/'manifest.json').write_text(json.dumps(manifest))
    reseal(root,'result.json',lambda d:d['channels']['m.speed_pu'].__setitem__(-1,d['channels']['m.speed_pu'][-1]+.01))
    r=replay_motor_bundle(root)
    assert r['completed'] and not r['repeatable'] and r['status']=='outside_repeatability_limits'
    assert not r['metrics']['m.speed_pu']['passed']


def test_measurement_failure_is_preserved_without_authentication_or_release(tmp_path):
    cfg=MotorTraceProtocol.model_validate_json(Path('cases/measurements/synthetic_motor_cold_trip.protocol.json').read_text())
    cfg.tolerances.current_a=1e-14
    root=tmp_path/'strict';r=export_motor_bundle(spec(),criteria(),root,trace=Path('cases/measurements/synthetic_motor_cold_trip.csv'),trace_protocol=cfg)
    assert r['completed'] and r['measurement_comparison_passed'] is False
    result=replay_motor_bundle(root)
    assert result['repeatable'] and result['measurement_comparison_passed'] is False
    assert not result['field_validated']


def test_timeout_cancel_output_budget_and_nonempty_directory_do_not_seal(tmp_path,monkeypatch):
    import power_grid_env.motor_bundle as bundle
    c=criteria();c.per_run_timeout_s=1e-6
    with pytest.raises(ValueError,match='incomplete'):export_motor_bundle(spec(),c,tmp_path/'timeout')
    with pytest.raises(ValueError,match='incomplete'):export_motor_bundle(spec(),criteria(),tmp_path/'cancel',cancelled=lambda:True)
    root=tmp_path/'occupied';root.mkdir();(root/'user.txt').write_text('keep')
    with pytest.raises(ValueError):export_motor_bundle(spec(),criteria(),root)
    assert (root/'user.txt').read_text()=='keep'
    monkeypatch.setattr(bundle,'MAX_TOTAL_BYTES',100)
    with pytest.raises(ValueError,match='size budget'):create(tmp_path/'budget')
    assert not (tmp_path/'budget/manifest.json').exists()


def test_manifest_change_during_replay_is_not_a_repeatability_success(sealed,tmp_path,monkeypatch):
    import power_grid_env.motor_bundle as bundle
    root=tmp_path/'bundle';shutil.copytree(sealed,root);original=bundle.run_study
    def changed(*args,**kwargs):
        result=original(*args,**kwargs);p=root/'manifest.json';p.write_bytes(p.read_bytes()+b' ');return result
    monkeypatch.setattr(bundle,'run_study',changed)
    with pytest.raises(ValueError,match='changed during replay'):replay_motor_bundle(root)


def test_cli_schema_and_no_writing_into_sealed_directory(sealed,tmp_path):
    from power_grid_env.cli import main
    from power_grid_env.schema_export import export_schemas
    import jsonschema
    assert main(['motor-bundle','verify',str(sealed),'--out',str(tmp_path/'verified.json')])==0
    assert main(['motor-bundle','replay',str(sealed),'--out',str(tmp_path/'replayed.json')])==0
    assert main(['motor-bundle','verify',str(sealed),'--out',str(sealed/'bad.json')])==2
    assert not (sealed/'bad.json').exists()
    export_schemas(tmp_path/'schemas');jsonschema.validate(criteria().model_dump(mode='json'),json.loads((tmp_path/'schemas/motor-replay.schema.json').read_text()))


@pytest.mark.parametrize('field',['level','threshold'])
def test_replay_compares_thermal_event_values_as_well_as_identity(sealed,tmp_path,field):
    root=tmp_path/'event-audit';create(root,False)
    reseal(root,'result.json',lambda d:d['event_log'][0].update({field:d['event_log'][0][field]+.01}))
    r=replay_motor_bundle(root)
    assert r['completed'] and not r['repeatable'] and not r['event_audit_matches']


def test_replay_compares_reclose_diagnostic_and_machine_terminal_channels(tmp_path):
    s=StudySpec.model_validate_json(Path('cases/studies/motor_coast_short.json').read_text())
    root=tmp_path/'reclose';export_motor_bundle(s,criteria(),root)
    r=replay_motor_bundle(root);assert r['repeatable'] and 'm.terminal_voltage_pu' in r['metrics']
    def change(d):
        event=next(e for e in d['event_log'] if e['kind']=='motor_close')
        event['reclose_diagnostic']['pre_batch_voltage_difference_pu']+=.01
    reseal(root,'result.json',change)
    r=replay_motor_bundle(root);assert not r['repeatable'] and not r['event_audit_matches']
