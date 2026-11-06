import json,zipfile
from pathlib import Path
import pytest
from power_grid_env.engineering_project import EngineeringProject,import_engineering_project
from power_grid_env.engineering_report import compare_runs
from power_grid_env.study_spec import StudySpec
from power_grid_env.study_artifacts import verify,read_json,extract_zip


def flux():
    return StudySpec.model_validate_json(Path('cases/studies/flux-saturated_dol.json').read_text())


def qsts():
    spec=StudySpec.model_validate_json(Path('cases/studies/qsts_runtime.json').read_text());spec.scenario.horizon_s=180.;spec.scenario.control_interval_s=60.
    return spec


def test_project_run_revision_compare_export_restore_and_replay(tmp_path):
    project=EngineeringProject.create(tmp_path/'project',flux());assert project.check()['ok']
    a=project.run('baseline');b=project.run('repeat');assert a['completed'] and b['completed']
    assert compare_runs(project.root/'runs/baseline',project.root/'runs/repeat')['passed']
    spec=flux();spec.flux_network.motors[0].stator_r_ohm*=1.2;revision=project.revise(spec);assert revision=='r0002'
    c=project.run('candidate');assert c['completed']
    assert not compare_runs(project.root/'runs/baseline',project.root/'runs/candidate')['passed']
    with pytest.raises(ValueError):project.run('baseline')
    archive=tmp_path/'export.zip';project.export('baseline',archive)
    restored=EngineeringProject.from_export(archive,tmp_path/'restored');restored.verify_run('imported')
    repeated=restored.run('replay');assert repeated['completed']
    assert compare_runs(restored.root/'runs/imported',restored.root/'runs/replay')['passed']


def test_QSTS_paused_checkpoint_resumes_into_new_immutable_run(tmp_path):
    p=EngineeringProject.create(tmp_path/'qsts',qsts());a=p.run('part',max_steps=1,checkpoint_every=1)
    assert a['status']=='paused' and not a['completed']
    manifest=verify(p.root/'runs/part');b=p.run('continued',resume_from='part',checkpoint_every=1);assert b['completed'],b
    assert verify(p.root/'runs/part')==manifest
    c=p.run('full');assert c['completed']
    assert compare_runs(p.root/'runs/continued',p.root/'runs/full')['passed']


def test_cancel_timeout_and_lock_do_not_create_success(tmp_path):
    p=EngineeringProject.create(tmp_path/'p',flux());p.request_cancel();r=p.run('cancelled');assert r['status']=='cancelled' and not r['completed']
    p.clear_cancel();r=p.run('deadline',timeout_s=.000001);assert r['status']=='timeout' and not r['completed']
    (p.root/'.project.lock').write_text('existing')
    with pytest.raises(FileExistsError):p.run('locked')

@pytest.mark.parametrize('damage',['study','result','extra','symlink','manifest'])
def test_run_artifact_tampering_is_rejected(tmp_path,damage):
    p=EngineeringProject.create(tmp_path/'p',flux());p.run('a');root=p.root/'runs/a'
    if damage=='study':(root/'study.json').write_text('{}')
    elif damage=='result':(root/'result.json').write_text('{}')
    elif damage=='extra':(root/'extra.txt').write_text('extra')
    elif damage=='symlink':(root/'link').symlink_to(root/'result.json')
    else:(root/'manifest.json').write_text('{}')
    with pytest.raises(ValueError):p.verify_run('a')


def test_project_definition_and_import_provenance_tampering_rejected(tmp_path):
    p=EngineeringProject.create(tmp_path/'p',flux(),import_report={'commands':[]})
    (p.root/'revisions/r0001-import.json').write_text('{}')
    with pytest.raises(ValueError,match='provenance'):p.check()

@pytest.mark.parametrize('name',['../x','a/b','.','a:b',''])
def test_unsafe_run_ids_do_not_escape_project(tmp_path,name):
    p=EngineeringProject.create(tmp_path/'p',flux())
    with pytest.raises(ValueError):p.run(name)


def test_local_import_has_transfer_counts_and_does_not_reuse_rejected_model(tmp_path):
    source=tmp_path/'network.json';source.write_text(flux().network.model_dump_json())
    report=import_engineering_project(source,tmp_path/'imported');assert report['equipment']['equipment_counts']['sources']==1
    source.write_text('{invalid');r=import_engineering_project(source,tmp_path/'rejected');assert not r['ok']
    assert not (tmp_path/'rejected/project.json').exists()


def test_offline_report_preserves_data_and_cannot_close_script_tag(tmp_path):
    spec=flux();spec.id='</script><script>alert(1)</script>'
    p=EngineeringProject.create(tmp_path/'p',spec);assert p.run('a')['completed']
    page=(p.root/'runs/a/report.html').read_text();assert '<script>alert(1)</script>' not in page
    payload=page.split('<script id="payload" type="application/json">',1)[1].split('</script>',1)[0]
    assert json.loads(payload)['study']['id']==spec.id
    assert "connect-src 'none'" in page

@pytest.mark.parametrize('name',['../escape','/absolute','a\\b'])
def test_malicious_data_archive_cannot_escape_extraction(tmp_path,name):
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr(name,'data')
    with pytest.raises(ValueError):extract_zip(archive,tmp_path/'destination')
    assert not (tmp_path/'destination').exists()


def test_rejected_resume_and_budget_do_not_consume_run_id(tmp_path):
    p=EngineeringProject.create(tmp_path/'p',qsts())
    with pytest.raises(ValueError,match='unknown run'):p.run('retry',resume_from='absent')
    assert not (p.root/'runs/retry').exists()
    assert p.run('retry',max_steps=1)['status']=='paused'
    f=EngineeringProject.create(tmp_path/'f',flux())
    with pytest.raises(ValueError,match='QSTS-only'):f.run('retry',max_steps=1)
    assert not (f.root/'runs/retry').exists()


@pytest.mark.parametrize('points',[[[1,1]],[[0,1],[0,2]],[[0,1,2,3]]])
def test_preflight_rejects_invalid_qsts_profiles(tmp_path,points):
    spec=qsts();spec.scenario.load_profile={spec.network.loads[0].id:points}
    p=EngineeringProject.create(tmp_path/'p',spec)
    assert not p.check()['ok']
    assert p.run('bad')['status']=='rejected'


def test_failed_export_removes_only_its_partial_archive(tmp_path,monkeypatch):
    from power_grid_env import study_artifacts as artifacts
    root=tmp_path/'artifact';root.mkdir();(root/'a.json').write_text('{}');artifacts.seal(root)
    def fail(*args,**kwargs):raise OSError('disk full')
    monkeypatch.setattr(zipfile.ZipFile,'writestr',fail)
    target=tmp_path/'partial.zip'
    with pytest.raises(OSError,match='disk full'):artifacts.export_zip(root,target)
    assert not target.exists()
    existing=tmp_path/'existing.zip';existing.write_bytes(b'keep')
    with pytest.raises(ValueError):artifacts.export_zip(root,existing)
    assert existing.read_bytes()==b'keep'


def test_report_units_distinguish_angles_and_dimensionless_residuals():
    from power_grid_env.engineering_report import unit_for
    assert unit_for('node:x.angle_rad')=='rad'
    assert unit_for('m.speed_rad_s')=='rad/s'
    assert unit_for('kcl_relative_residual')=='1'
