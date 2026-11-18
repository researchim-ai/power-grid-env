import hashlib
from pathlib import Path
import pytest
from benchmarks.engineering_v2_adapter import receipt
from benchmarks.engineering_protocol import RunContract
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import seal
import benchmarks.engineering_trainer_v2 as trainer


def test_v2_adapter_requires_target_contract_source_and_complete_schedule(tmp_path):
    c=RunContract(maximum_actions=24,context_tokens=24576)
    from power_grid_env.evidence import package_fingerprint
    r=dict(adapter_names=['projection'],schema='power-grid-env.trainer-v2/v1',identity=dict(target_sha256='a'*64,core_sha256=package_fingerprint(),trainer_sha256=hashlib.sha256(Path(trainer.__file__).read_bytes()).hexdigest()),
        recipe=dict(contract=c.model_dump(),steps=2),completed_steps=2,schedule_complete=True,frozen_base_unchanged=True,adapter_roundtrip_exact=True)
    write_json(tmp_path/'ok'/'summary.json',r);(tmp_path/'ok'/'adapter.safetensors').write_bytes(b'fixture');anchor=seal(tmp_path/'ok')
    assert receipt(tmp_path/'ok',anchor,'a'*64,c)['completed_steps']==2
    for target,contract in [('b'*64,c),('a'*64,RunContract())]:
        with pytest.raises(ValueError):receipt(tmp_path/'ok',anchor,target,contract)
    r['schedule_complete']=False;write_json(tmp_path/'partial'/'summary.json',r)
    with pytest.raises(ValueError):receipt(tmp_path/'partial',seal(tmp_path/'partial'),'a'*64,c)
