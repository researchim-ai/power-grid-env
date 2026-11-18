import copy
from pathlib import Path
import shutil
import pytest
from benchmarks.engineering_v3_policy import receipt
from benchmarks.engineering_trainer_v3 import DEPENDENCIES
from benchmarks.engineering_student_dataset import source_sha
from benchmarks.engineering_learning_gate import strict_contract
from benchmarks.engineering_factory import write_json
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import seal


def metadata():
    return dict(schema='power-grid-env.trainer-v3/v1',identity=dict(target_sha256='a'*64,core_sha256=package_fingerprint(),source_sha256={n:source_sha(n) for n in DEPENDENCIES}),
        recipe=dict(steps=2,contract=strict_contract().model_dump()),completed_steps=2,schedule_complete=True,frozen_base_unchanged=True,adapter_roundtrip_exact=True,adapter_names=['layer'])


def test_phase_receipt_requires_complete_owned_weights_and_current_sources(tmp_path):
    r=metadata();write_json(tmp_path/'ok'/'summary.json',r);(tmp_path/'ok'/'adapter.safetensors').write_bytes(b'fixture weights;not loaded in receipt test')
    anchor=seal(tmp_path/'ok');assert receipt(tmp_path/'ok',anchor,'a'*64)==r
    with pytest.raises(ValueError):receipt(tmp_path/'ok',anchor,'b'*64)
    with pytest.raises(ValueError):receipt(tmp_path/'ok','0'*64,'a'*64)


@pytest.mark.parametrize('change',['partial','source','profile','frozen','roundtrip','weights'])
def test_partial_or_drifted_phase_is_not_loadable(tmp_path,change):
    r=metadata()
    if change=='partial':r['completed_steps']=1
    elif change=='source':r['identity']['source_sha256']['engineering_trainer_v3.py']='bad'
    elif change=='profile':r['recipe']['contract']['context_tokens']=16384
    elif change=='frozen':r['frozen_base_unchanged']=False
    elif change=='roundtrip':r['adapter_roundtrip_exact']=False
    write_json(tmp_path/'bad'/'summary.json',r)
    if change!='weights':(tmp_path/'bad'/'adapter.safetensors').write_bytes(b'fixture')
    with pytest.raises(ValueError):receipt(tmp_path/'bad',seal(tmp_path/'bad'),'a'*64)
