"""Storage/layout contracts; fake runs here are not physical qualifications."""
import copy
from pathlib import Path
import pytest
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest
from power_grid_env.protection_envelope_store import run_store,verify_store
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def envelope():return read_json(ROOT/'cases/full-protection-envelope/radau.json')

@pytest.fixture
def fake_backend(monkeypatch):
    import power_grid_env.protection_envelope_store as m
    def run(plan,out,**kwargs):
        out=Path(out);out.mkdir()
        result=dict(completed=True,numerically_qualified=True,passes_declared_profile=not plan.id.endswith('1111'),
            actions=[dict(grids=[dict(passed=True)]*3)]*6)
        (out/'plan.json').write_bytes(encoded(plan.model_dump(mode='json')))
        (out/'result.json').write_bytes(encoded(result));seal(out);return result
    def check(root,*,expected_plan=None):
        assert read_json(Path(root)/'plan.json')==expected_plan.model_dump(mode='json')
        return dict(verified=True,manifest_sha256=verify(root))
    monkeypatch.setattr(m,'run_closed_loop',run);monkeypatch.setattr(m,'verify_closed_loop',check)


def test_composite_store_negative_coverage_and_bytes(envelope,tmp_path,fake_backend):
    root=tmp_path/'store';r=run_store(envelope,root)
    assert r['completed'] and r['numerically_qualified'] and not r['passes_all_enumerated_corners']
    assert r['completed_grid_runs']==306 and r['expected_grid_runs']==306
    assert r['payload_bytes']>0 and r['reward'] is None
    assert not (root/'manifest.json').exists() and (root/'index/manifest.json').exists()
    assert verify_store(root,expected_envelope=envelope)['verified']
    with pytest.raises(ValueError,match='owner envelope'):
        other=copy.deepcopy(envelope);other['base']['primary']['pickup_a']=999
        verify_store(root,expected_envelope=other)


def test_cancelled_has_no_complete_store(envelope,tmp_path):
    root=tmp_path/'cancelled';r=run_store(envelope,root,cancelled=lambda:True)
    assert not r['completed'] and r['completed_grid_runs']==0 and r['reward'] is None
    with pytest.raises(ValueError,match='complete17'):verify_store(root)

@pytest.mark.parametrize('defect',['extra_root','extra_index','extra_study','missing_study','recipe','manifest','bytes','summary','reorder','nonobject'])
def test_tampering(envelope,tmp_path,fake_backend,defect):
    root=tmp_path/'store';run_store(envelope,root)
    index=root/'index'
    if defect=='extra_root':(root/'untracked').write_text('x')
    elif defect=='extra_index':(index/'extra.json').write_text('{}')
    elif defect=='extra_study':(root/'studies/extra').mkdir()
    elif defect=='missing_study':(root/'studies/nominal').rename(root/'studies/missing')
    else:
        name='result.json' if defect=='summary' else 'entries.json'
        value=read_json(index/name)
        if defect=='recipe':value[0]['recipe']['primary']['pickup_a']=1
        if defect=='manifest':value[0]['study_manifest_sha256']='0'*64
        if defect=='bytes':value[0]['payload_bytes']=0
        if defect=='summary':value['passes_all_enumerated_corners']=True
        if defect=='reorder':value.reverse()
        if defect=='nonobject':value[0]='unexpected'
        (index/name).write_bytes(encoded(value));(index/'manifest.json').unlink();seal(index)
    with pytest.raises(ValueError):verify_store(root)

@pytest.mark.parametrize('target',['index','studies','studies/nominal'])
def test_symlink_directories_rejected(envelope,tmp_path,fake_backend,target):
    root=tmp_path/'store';run_store(envelope,root)
    path=root/target;other=tmp_path/'moved';path.rename(other);path.symlink_to(other,target_is_directory=True)
    with pytest.raises(ValueError):verify_store(root)


def test_output_not_overwritten(envelope,tmp_path,fake_backend):
    root=tmp_path/'store';run_store(envelope,root);before=verify(root/'index')
    with pytest.raises(FileExistsError):run_store(envelope,root)
    assert verify(root/'index')==before


def test_cli_negative_is_verified_but_not_accepted(envelope,tmp_path,fake_backend,capsys):
    from power_grid_env.cli import main
    root=tmp_path/'store';run_store(envelope,root)
    assert main(['verify-protection-envelope-store',str(root)])==2
    assert '"verified": true' in capsys.readouterr().out

def test_per_artifact_limits_not_bypassed(envelope,tmp_path,fake_backend,monkeypatch):
    import power_grid_env.study_artifacts as a
    monkeypatch.setattr(a,'MAX_TOTAL',64)
    with pytest.raises(ValueError,match='budget'):run_store(envelope,tmp_path/'limited')
    assert not (tmp_path/'limited/index/manifest.json').exists()


def test_full_brief_criteria_preserved_on_all_corners(envelope):
    from power_grid_env.protection_envelope import effective_plans
    original=read_json(ROOT/'cases/inverse-protection/standard_inverse-radau.json')
    assert envelope['base']['equipment']['mandatory_cases']==original['equipment']['mandatory_cases']
    for _,plan,_ in effective_plans(envelope):
        assert len(plan.equipment.mandatory_cases)==3
        assert [c.study.induction.duration_s for c in plan.equipment.mandatory_cases]==[4.,4.,4.]
        assert all(c.motors[0].minimum_final_speed_pu==.9 for c in plan.equipment.mandatory_cases)
