import copy
import json
from types import SimpleNamespace
import pytest
import torch
from benchmarks.engineering_trainer_v2 import LoraSpec,attach_language_lora,balanced_schedule,check_preflight,train
from benchmarks.engineering_protocol import RunContract
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import seal,verify


class Tiny(torch.nn.Module):
    def __init__(self):
        super().__init__();self.config=SimpleNamespace(text_config=SimpleNamespace(num_hidden_layers=2))
        self.model=torch.nn.Module();self.model.language_model=torch.nn.Module()
        self.model.language_model.layers=torch.nn.ModuleList([torch.nn.Sequential(torch.nn.Linear(4,4),torch.nn.Tanh()) for _ in range(2)])
        self.vision=torch.nn.Linear(4,4);self.head=torch.nn.Linear(4,8)
    def forward(self,input_ids,attention_mask,use_cache,logits_to_keep):
        x=torch.nn.functional.one_hot(input_ids%4,4).float()
        for layer in self.model.language_model.layers:x=layer(x)
        return SimpleNamespace(logits=self.head(x)[:,logits_to_keep,:])


class Data:
    def __init__(self,*args):
        self.episodes=[dict(task_id='task'+str(i),family_sha256='family'+str(i%2),variant='clean') for i in range(6)]
        self.targets=[(i,0) for i in range(6)]
        self.scenarios={'task'+str(i):dict(kind=('assemble','diagnose','repair')[i%3]) for i in range(6)}
    def __len__(self):return 6
    def batch(self,processor,index):
        ids=torch.tensor([[0,1,2,3]])
        return dict(input_ids=ids,attention_mask=torch.ones_like(ids),labels=torch.tensor([[-100,-100,2,3]])),dict(index=index,source='public_expert')


def test_lora_span_and_frozen_modalities():
    model=Tiny();before={n:p.detach().clone() for n,p in model.named_parameters()}
    names=attach_language_lora(model,LoraSpec(rank=2,last_blocks=1))
    assert len(names)==1 and '.layers.1.' in names[0]
    assert all(not p.requires_grad for p in model.vision.parameters())
    assert all(not p.requires_grad for p in model.head.parameters())
    assert all(n.startswith('model.language_model.layers.1.') for n,p in model.named_parameters() if p.requires_grad)
    model=Tiny();assert len(attach_language_lora(model,LoraSpec(rank=2,last_blocks=None)))==2
    with pytest.raises(ValueError):attach_language_lora(Tiny(),LoraSpec(last_blocks=3))


def test_episode_balanced_schedule_is_deterministic_and_bounded():
    data=Data();a=balanced_schedule(data,12,42)
    assert a==balanced_schedule(data,12,42) and set(a[:6])==set(range(6))
    assert balanced_schedule(data,2,42,[1,4])==[1,4]
    with pytest.raises(ValueError):balanced_schedule(data,2,42,[True,2])


def test_preflight_must_match_the_entire_train_profile(tmp_path):
    import hashlib
    from pathlib import Path
    import benchmarks.engineering_factory as loader
    import benchmarks.engineering_protocol as protocol
    contract=RunContract(maximum_actions=24,context_tokens=24576)
    receipt=dict(schema='power-grid-env.procedural-preflight/v1',passed=True,failures=[],targets=6,checked_targets=6,
        train_manifest_sha256='a'*64,target_sha256='b'*64,contract_sha256=contract.fingerprint(),
        loader_sha256=hashlib.sha256(Path(loader.__file__).read_bytes()).hexdigest(),protocol_sha256=hashlib.sha256(Path(protocol.__file__).read_bytes()).hexdigest())
    write_json(tmp_path/'ok'/'summary.json',receipt);anchor=seal(tmp_path/'ok')
    check_preflight(tmp_path/'ok',anchor,Data(),'a'*64,'b'*64,contract)
    with pytest.raises(ValueError):check_preflight(tmp_path/'ok',anchor,Data(),'a'*64,'b'*64,RunContract())
    receipt['passed']=False;write_json(tmp_path/'bad'/'summary.json',receipt)
    with pytest.raises(ValueError):check_preflight(tmp_path/'bad',seal(tmp_path/'bad'),Data(),'a'*64,'b'*64,contract)


def test_training_resume_matches_uninterrupted_and_does_not_pass_learning_gate(tmp_path,monkeypatch):
    import benchmarks.engineering_trainer_v2 as module
    import power_grid_env.qwen35_training as native
    from safetensors.torch import load_file
    monkeypatch.setattr(module,'ProceduralDataset',Data);monkeypatch.setattr(module,'check_preflight',lambda *args:None)
    monkeypatch.setattr(native,'local_target',lambda *args:{})
    monkeypatch.setattr(native,'load_native',lambda *args:(None,Tiny()))
    args=('target','a'*64,'data','b'*64,'preflight','c'*64)
    full=train(*args,tmp_path/'full',steps=2,indices=[0,1],lora=dict(rank=2,last_blocks=1))
    head=train(*args,tmp_path/'head',steps=2,indices=[0,1],lora=dict(rank=2,last_blocks=1),max_new_steps=1)
    assert head['completed_steps']==1 and not head['schedule_complete']
    tail=train(*args,tmp_path/'tail',steps=2,indices=[0,1],lora=dict(rank=2,last_blocks=1),resume=tmp_path/'head',resume_manifest=verify(tmp_path/'head'))
    assert tail['completed_steps']==2 and not tail['closed_loop_quality_gate_passed']
    for filename in ('adapter.safetensors','optimizer.safetensors'):
        a=load_file(str(tmp_path/'full'/filename));b=load_file(str(tmp_path/'tail'/filename))
        assert a.keys()==b.keys() and all(torch.equal(a[k],b[k]) for k in a)
