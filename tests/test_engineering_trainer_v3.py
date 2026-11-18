import copy
from types import SimpleNamespace
from pathlib import Path
import pytest
import torch
from benchmarks.engineering_trainer_v3 import train,state_digest
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_learning_gate import strict_contract
from power_grid_env.study_artifacts import seal,verify,read_json


class Tiny(torch.nn.Module):
    def __init__(self):
        super().__init__();self.config=SimpleNamespace(text_config=SimpleNamespace(num_hidden_layers=2))
        self.model=torch.nn.Module();self.model.language_model=torch.nn.Module();self.model.language_model.layers=torch.nn.ModuleList([torch.nn.Sequential(torch.nn.Linear(4,4),torch.nn.Tanh()) for _ in range(2)])
        self.vision=torch.nn.Linear(4,4);self.head=torch.nn.Linear(4,8)
    def forward(self,input_ids,attention_mask,use_cache,logits_to_keep):
        x=torch.nn.functional.one_hot(input_ids%4,4).float()
        for layer in self.model.language_model.layers:x=layer(x)
        return SimpleNamespace(logits=self.head(x)[:,logits_to_keep,:])


class Mixture:
    def __init__(self):
        self.entry=[dict(source=source,index=i,kind='repair',family_sha256='f',action='simulate') for i,source in enumerate(['procedural','student_correction'])]
        ep=dict(events=[dict(action=dict(name='simulate',arguments={}))]);data=SimpleNamespace(targets=[(0,0),(0,0)],episodes=[ep])
        self.datasets={'procedural':data,'student_correction':data}
    def batch(self,p,entry):
        ids=torch.tensor([[0,1,2,3]])
        return dict(input_ids=ids,attention_mask=torch.ones_like(ids),labels=torch.tensor([[-100,-100,2,3]])),dict(index=entry['index'],corpus_source=entry['source'])


@pytest.fixture
def native(monkeypatch):
    import benchmarks.engineering_trainer_v3 as module
    import power_grid_env.qwen35_training as native
    mix=Mixture();monkeypatch.setattr(module,'load_plan',lambda *a:(mix,dict(seed=42,schedule=mix.entry)))
    monkeypatch.setattr(module,'verify_target',lambda *a:dict(argument_fields=0))
    monkeypatch.setattr(native,'local_target',lambda *a:{})
    monkeypatch.setattr(native,'load_native',lambda *a:(None,Tiny()))
    return module


def test_exact_same_phase_resume_matches_full_adapters_and_adamw(tmp_path,native):
    from safetensors.torch import load_file
    args=('target','a'*64,'plan','b'*64)
    full=train(*args,tmp_path/'full',lora=dict(rank=2,last_blocks=1))
    head=train(*args,tmp_path/'head',max_new_steps=1,lora=dict(rank=2,last_blocks=1))
    tail=train(*args,tmp_path/'tail',resume=tmp_path/'head',resume_manifest=verify(tmp_path/'head'),lora=dict(rank=2,last_blocks=1))
    assert full['schedule_complete'] and tail['schedule_complete'] and not head['schedule_complete']
    assert not tail['closed_loop_quality_gate_passed'] and tail['recipe']['optimizer_initialization']=='fresh_for_new_phase'
    for name in ('adapter.safetensors','optimizer.safetensors'):
        a=load_file(str(tmp_path/'full'/name));b=load_file(str(tmp_path/'tail'/name));assert a.keys()==b.keys() and all(torch.equal(a[k],b[k]) for k in a)
    with pytest.raises(ValueError):train(*args,tmp_path/'again',resume=tmp_path/'tail',resume_manifest=verify(tmp_path/'tail'),lora=dict(rank=2,last_blocks=1))


def test_warm_start_really_loads_parent_and_resets_new_phase_optimizer(tmp_path,native,monkeypatch):
    from safetensors.torch import load_file
    import benchmarks.engineering_v2_adapter as old
    args=('target','a'*64,'plan','b'*64)
    parent=train(*args,tmp_path/'parent',lora=dict(rank=2,last_blocks=1));meta=read_json(tmp_path/'parent'/'summary.json')
    monkeypatch.setattr(old,'receipt',lambda *a:meta)
    r=train(*args,tmp_path/'warm',initial=tmp_path/'parent',initial_manifest=verify(tmp_path/'parent'),lora=dict(rank=2,last_blocks=1))
    assert r['phase_initial_adapter_sha256']==state_digest(load_file(str(tmp_path/'parent'/'adapter.safetensors')))
    assert r['phase_initial_adapter_sha256']!=parent['phase_initial_adapter_sha256'] and r['completed_steps']==2
    tensors=load_file(str(tmp_path/'warm'/'optimizer.safetensors'));assert all(float(v)==2 for k,v in tensors.items() if k.endswith('/step'))
    with pytest.raises(ValueError):train(*args,tmp_path/'bad',initial=tmp_path/'parent',initial_manifest='a'*64,lora=dict(rank=2,last_blocks=2))


def test_semantically_wrong_target_stops_before_successful_checkpoint(tmp_path,native,monkeypatch):
    def wrong(*a):raise ValueError('arguments dropped')
    monkeypatch.setattr(native,'verify_target',wrong)
    with pytest.raises(ValueError):train('target','a'*64,'plan','b'*64,tmp_path/'bad',lora=dict(rank=2,last_blocks=1))
    assert verify(tmp_path/'bad') and not (tmp_path/'bad'/'summary.json').exists()
    assert not (tmp_path/'bad'/'adapter.safetensors').exists()


def test_incomplete_or_changed_history_cannot_resume(tmp_path,native):
    import shutil
    args=('target','a'*64,'plan','b'*64);train(*args,tmp_path/'head',max_new_steps=1,lora=dict(rank=2,last_blocks=1))
    shutil.copytree(tmp_path/'head',tmp_path/'forged',ignore=shutil.ignore_patterns('manifest.json'))
    r=read_json(tmp_path/'forged'/'summary.json');r['history'][0]['entry']['source']='unknown';write_json(tmp_path/'forged'/'summary.json',r)
    with pytest.raises(ValueError):train(*args,tmp_path/'tail',resume=tmp_path/'forged',resume_manifest=seal(tmp_path/'forged'),lora=dict(rank=2,last_blocks=1))
    with pytest.raises(ValueError):train(*args,tmp_path/'zero',max_new_steps=0)
    with pytest.raises(ValueError):train(*args,tmp_path/'missing',initial='parent')
