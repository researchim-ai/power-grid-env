import pytest
from benchmarks.engineering_learning_gate import strict_contract
from power_grid_env.study_artifacts import verify
import benchmarks.engineering_student_preflight as module


class Data:
    def __init__(self,*a):self.contract=strict_contract();self.anchors={'correction_manifest_sha256':'a'*64}
    def __len__(self):return 3
    def batch(self,processor,index):
        if index==1:raise ValueError('context_budget_exceeded:no truncation')
        return {},dict(index=index,action='inspect_project',kind='repair',prompt_tokens=42,label_tokens=16,total_tokens=58)
    def example(self,index):return [],dict(name='inspect_project',arguments={}),{},index


def native_stub(monkeypatch,data):
    import power_grid_env.qwen35_training as native
    import power_grid_env.vlm_batches as batches
    monkeypatch.setattr(module,'StudentCorrectionDataset',data)
    monkeypatch.setattr(native,'local_target',lambda *a:dict(local_checkpoint='fixture',config_sha256='b'*64))
    monkeypatch.setattr(batches,'local_qwen35_processor',lambda *a:None)


def test_every_target_failure_is_retained_and_no_truncation_or_false_pass(tmp_path,monkeypatch):
    native_stub(monkeypatch,Data)
    r=module.qualify('corpus','a','train','b','suite','c','eval','d','target','e',tmp_path/'out')
    assert not r['passed'] and r['targets']==3 and r['checked_targets']==2 and r['failures'][0]['index']==1
    assert r['supervised_tokens']==32 and not r['weights_loaded'] and verify(tmp_path/'out')


def test_empty_corrections_never_pass_admission(tmp_path,monkeypatch):
    class Empty(Data):
        def __len__(self):return 0
    native_stub(monkeypatch,Empty)
    r=module.qualify('corpus','a','train','b','suite','c','eval','d','target','e',tmp_path/'out')
    assert not r['passed'] and r['checked_targets']==0 and r['failures'][0]['error']=='no successful correction targets'


def test_all_target_success_still_is_not_model_training(tmp_path,monkeypatch):
    class Valid(Data):
        def batch(self,processor,index):return {},dict(index=index,action='inspect_project',kind='repair',prompt_tokens=42,label_tokens=16,total_tokens=58)
    native_stub(monkeypatch,Valid)
    r=module.qualify('corpus','a','train','b','suite','c','eval','d','target','e',tmp_path/'out')
    assert r['passed'] and r['checked_targets']==r['targets']==3 and not r['weights_loaded'] and r['eligible_for_gold_sft'] is False
    assert r['unique_public_targets']==1
