import pytest
import torch
from types import SimpleNamespace
from benchmarks.engineering_native_staged_policy import NativePolicy


class Processor:
    def __init__(self,n=4):
        self.n=n;self.tokenizer=SimpleNamespace(pad_token_id=0,eos_token_id=1,decode=lambda ids,**kw:'{"operation":"validate"}')
    def apply_chat_template(self,messages,**kw):
        assert kw['enable_thinking'] is False
        return dict(input_ids=torch.ones((1,self.n),dtype=torch.long),attention_mask=torch.ones((1,self.n),dtype=torch.long))


class Model:
    def __init__(self,extra=3):self.extra=extra;self.settings=None
    def eval(self):return self
    def generate(self,**kw):
        self.settings=kw
        return torch.ones((1,kw['input_ids'].shape[1]+self.extra),dtype=torch.long)


def request():return dict(stage='operation',messages=[dict(role='system',content='public instructions'),dict(role='user',content='public state')],maximum_completion_tokens=128)


def test_native_policy_enforces_real_generation_and_context_caps_without_output_repair():
    model=Model();p=NativePolicy(Processor(),model);raw=p(request())
    assert raw=='{"operation":"validate"}' and model.settings['max_new_tokens']==128
    assert model.settings['do_sample'] is False and p.calls==[dict(stage='operation',prompt_tokens=4,generated_tokens=3)]
    with pytest.raises(ValueError):NativePolicy(Processor(8192),Model())(request())
    with pytest.raises(ValueError):NativePolicy(Processor(),Model(129))(request())


@pytest.mark.parametrize('mutation',['extra','tokens','role','stage','multimodal'])
def test_candidate_packet_cannot_change_qualified_profile(mutation):
    q=request()
    if mutation=='extra':q['reference_answer']='hidden'
    if mutation=='tokens':q['maximum_completion_tokens']=256
    if mutation=='role':q['messages'][1]['role']='assistant'
    if mutation=='stage':q['stage']='teacher'
    if mutation=='multimodal':q['messages'][1]['content']=[{'type':'text','text':'unqualified image interface'}]
    with pytest.raises(ValueError):NativePolicy(Processor(),Model())(q)


def receipt(tmp_path,mutate=None):
    from power_grid_env.study_artifacts import seal
    from power_grid_env.evidence import package_fingerprint
    from benchmarks.engineering_factory import write_json
    from benchmarks.engineering_skill_tasks import identity
    from pathlib import Path
    p=tmp_path/'plan';r=tmp_path/'checkpoint';p.mkdir();r.mkdir()
    plan=dict(schema='power-grid-env.decision-pilot-plan/v1',target_sha256='a'*64,
              source_sha256={'engineering_native_staged_policy.py':identity(Path('benchmarks/engineering_native_staged_policy.py'))})
    if mutate=='source':plan['source_sha256']['engineering_native_staged_policy.py']='0'*64
    write_json(p/'summary.json',plan);pa=seal(p)
    result=dict(schema='power-grid-env.decision-pilot-result/v1',steps=40,history=[{}]*40,complete=True,
                frozen_base_unchanged=True,adapter_roundtrip_exact=True,plan_manifest_sha256=pa,core_sha256=package_fingerprint(),gate_passed=False)
    if mutate=='partial':result['complete']=False
    if mutate=='cursor':result['history']=result['history'][:-1]
    if mutate=='core':result['core_sha256']='0'*64
    if mutate=='roundtrip':result['adapter_roundtrip_exact']=False
    if mutate=='parent':result['plan_manifest_sha256']='0'*64
    if mutate!='missing_weights':(r/'adapter.safetensors').write_bytes(b'opaque test fixture,not loaded')
    write_json(r/'summary.json',result);ra=seal(r)
    return r,ra,p,pa


@pytest.mark.parametrize('mutation',['partial','cursor','core','roundtrip','parent','source','missing_weights'])
def test_incomplete_or_drifted_receipt_is_rejected_before_loading_model(tmp_path,mutation):
    from benchmarks.engineering_native_staged_policy import checkpoint
    r,ra,p,pa=receipt(tmp_path,mutation)
    with pytest.raises(ValueError):checkpoint(r,ra,p,pa,'a'*64)


def test_diagnostic_metadata_loading_keeps_failed_learning_gate_failed(tmp_path):
    from benchmarks.engineering_native_staged_policy import checkpoint
    r,ra,p,pa=receipt(tmp_path);record,plan=checkpoint(r,ra,p,pa,'a'*64)
    assert record['gate_passed'] is False # reader does not qualify fixture weights or promote policy
