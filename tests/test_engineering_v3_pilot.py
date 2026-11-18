from pathlib import Path
import pytest
import torch
from safetensors.torch import save_file
from benchmarks.engineering_v3_pilot import compare_tensors,finish
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import seal,verify,read_json


def test_native_pilot_compares_optimizer_not_just_adapter_file(tmp_path):
    for folder in ('a','b'):
        (tmp_path/folder).mkdir()
        save_file(dict(x=torch.tensor([1.])),str(tmp_path/folder/'adapter.safetensors'))
        save_file(dict(moment=torch.tensor([2.])),str(tmp_path/folder/'optimizer.safetensors'))
    compare_tensors(tmp_path/'a',tmp_path/'b')
    save_file(dict(moment=torch.tensor([3.])),str(tmp_path/'b'/'optimizer.safetensors'))
    with pytest.raises(ValueError):compare_tensors(tmp_path/'a',tmp_path/'b')


def test_rejected_admission_never_starts_optimizer(tmp_path,monkeypatch):
    import benchmarks.engineering_v3_pilot as module
    write_json(tmp_path/'admission'/'summary.json',dict(schema='power-grid-env.student-data-campaign/v1',complete=True,admission_passed=False));seal(tmp_path/'admission')
    def forbidden(*args,**kwargs):raise AssertionError('optimizer must not run')
    monkeypatch.setattr(module,'train',forbidden)
    with pytest.raises(ValueError):finish(tmp_path/'admission','target','t'*64,'initial','i'*64,tmp_path/'pilot')
    assert verify(tmp_path/'pilot') and read_json(tmp_path/'pilot'/'failure.json')['failed_phase']=='wait_for_student_admission'


def test_partial_source_is_never_admitted_and_timeout_is_finite(tmp_path,monkeypatch):
    import benchmarks.engineering_v3_pilot as module
    def timeout(*args):raise TimeoutError('incomplete source')
    monkeypatch.setattr(module,'wait_sealed',timeout)
    with pytest.raises(TimeoutError):finish('missing','target','t'*64,'initial','i'*64,tmp_path/'pilot')
    assert verify(tmp_path/'pilot') and not (tmp_path/'pilot'/'full').exists()
    with pytest.raises(ValueError):finish('missing','target','t'*64,'initial','i'*64,tmp_path/'bad',wait_seconds=0)
