import hashlib
import json
from pathlib import Path
import warnings
import zipfile
import pytest
from benchmarks.switching_qualification_reassessment import compatible_wheel,run
from power_grid_env import switching_convergence as engine
from power_grid_env.study_artifacts import read_json,verify
from benchmarks.extended_closed_loop_acceptance import document


def wheel(tmp_path,mutation=None):
    source={p.name:p.read_bytes() for p in Path(engine.__file__).parent.glob('*.py')}
    if mutation=='missing':source.pop('switching_drive.py')
    elif mutation in {'switching_convergence.py','switching_drive.py'}:source[mutation]+=b'\n# altered source\n'
    h=hashlib.sha256()
    for name,content in sorted(source.items()):h.update(name.encode());h.update(content)
    out=tmp_path/'producer.whl'
    with zipfile.ZipFile(out,'w') as z:
        for name,content in source.items():z.writestr('power_grid_env/'+name,content)
        if mutation=='duplicate':
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',UserWarning);z.writestr('power_grid_env/switching_drive.py',source['switching_drive.py'])
    return out,h.hexdigest()


@pytest.mark.parametrize('mutation',[None,'switching_convergence.py','switching_drive.py','missing','duplicate'])
def test_cached_assessment_requires_byte_identical_numerical_runtime(tmp_path,mutation):
    archive,expected=wheel(tmp_path,mutation)
    if mutation in {None,'switching_convergence.py'}:
        r=compatible_wheel(archive,expected);assert r['identical_numerical_runtime']
        assert r['changed_files']==(['switching_convergence.py'] if mutation else [])
    else:
        with pytest.raises(ValueError):compatible_wheel(archive,expected)


def test_wrong_waveform_producer_hash_is_rejected(tmp_path):
    archive,_=wheel(tmp_path)
    with pytest.raises(ValueError,match='recorded waveform producer'):compatible_wheel(archive,'0'*64)


def source(tmp_path):
    p=document('warm_four_winding');p.update(duration_s=.0012,grid_ln_rms_v=1e-6);out=tmp_path/'source'
    result=engine.run_switching_qualification_artifact(p,out,max_levels=3);assert result['qualified'];return out


def test_reassessment_retains_two_producers_and_never_simulates(tmp_path,monkeypatch):
    old=source(tmp_path);archive,_=wheel(tmp_path);out=tmp_path/'reassessment'
    def forbidden(*args):raise AssertionError('numerical rerun forbidden in reassessment')
    monkeypatch.setattr(engine,'run_switching_drive',forbidden);run(old,archive,out)
    r=read_json(out/'reassessment.json');assert r['assessment']['qualified'] and r['new_numerical_trajectories']==0 and not r['numerical_replay']
    assert r['trajectories_reassessed']==3 and verify(out) and engine.run_switching_drive is forbidden


def test_tampered_saved_trajectory_cannot_be_reassessed(tmp_path):
    old=source(tmp_path);archive,_=wheel(tmp_path);out=tmp_path/'reassessment'
    p=old/'level-1/result.json';r=json.loads(p.read_text());r['channels']['motor_current_alpha_a'][1]+=1.;p.write_text(json.dumps(r))
    with pytest.raises(ValueError):run(old,archive,out)
    assert not out.exists()
