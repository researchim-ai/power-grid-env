import json
import pytest
from benchmarks.engineering_adapter import adapter_receipt
from power_grid_env.study_artifacts import seal


def artifact(tmp_path,**changes):
    receipt=dict(schema='power-grid-env.engineering-sft-qualification/v1',target_sha256='a'*64,
        optimizer_steps=1,frozen_unchanged=True,adapter_roundtrip_exact=True,
        eligible_for_gold_sft=False,training_plan={'indices':[0]},adapter_names=['projection'])
    receipt.update(changes)
    (tmp_path/'summary.json').write_text(json.dumps(receipt))
    (tmp_path/'adapter.safetensors').write_bytes(b'fixture-only-not-model-weights')
    return seal(tmp_path)


def test_adapter_receipt_owner_anchors(tmp_path):
    manifest=artifact(tmp_path)
    assert adapter_receipt(tmp_path,manifest,'a'*64)['optimizer_steps']==1
    with pytest.raises(ValueError):adapter_receipt(tmp_path,'b'*64,'a'*64)
    with pytest.raises(ValueError):adapter_receipt(tmp_path,manifest,'c'*64)
    (tmp_path/'adapter.safetensors').write_bytes(b'tampered')
    with pytest.raises(ValueError):adapter_receipt(tmp_path,manifest,'a'*64)


@pytest.mark.parametrize('changes',[{'optimizer_steps':True},{'optimizer_steps':0},{'frozen_unchanged':False},
 {'adapter_roundtrip_exact':False},{'eligible_for_gold_sft':True},{'training_plan':None},
 {'schema':'wrong'},{'adapter_names':None}])
def test_incompatible_receipt(tmp_path,changes):
    manifest=artifact(tmp_path,**changes)
    with pytest.raises(ValueError):adapter_receipt(tmp_path,manifest,'a'*64)


def test_completed_epoch_adapter_is_supported(tmp_path):
    manifest=artifact(tmp_path,schema='power-grid-env.engineering-action-training/v1',
        optimizer_steps=3,schedule=[2,0,1],schedule_complete=True)
    assert adapter_receipt(tmp_path,manifest,'a'*64)['optimizer_steps']==3


def test_incomplete_epoch_not_accepted_as_finished_adapter(tmp_path):
    manifest=artifact(tmp_path,schema='power-grid-env.engineering-action-training/v1',
        optimizer_steps=2,schedule=[2,0,1],schedule_complete=False)
    with pytest.raises(ValueError):adapter_receipt(tmp_path,manifest,'a'*64)
