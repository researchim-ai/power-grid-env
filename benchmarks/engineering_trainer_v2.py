"""Profile-admitted procedural SFT with broader text LoRA and resumable state.

Completing a schedule never means passing the closed-loop learning gate.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import random
from pathlib import Path
from pydantic import BaseModel,ConfigDict,Field
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_protocol import RunContract
from benchmarks.engineering_train import save_optimizer,restore_optimizer
from power_grid_env.study_artifacts import verify,read_json,seal


class LoraSpec(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    rank:int=Field(default=16,ge=1,le=64)
    alpha:float=Field(default=32.,gt=0,le=256)
    last_blocks:int|None=Field(default=2,ge=1,le=128)


def attach_language_lora(model,spec):
    import math,re,torch
    from torch import nn
    layers=model.config.text_config.num_hidden_layers
    if spec.last_blocks is not None and spec.last_blocks>layers:raise ValueError('LoRA layer span outside model')
    first=0 if spec.last_blocks is None else layers-spec.last_blocks
    modules=dict(model.named_modules());names=[]
    for name,module in modules.items():
        match=re.match(r'^model\.language_model\.layers\.(\d+)\.',name)
        if match and int(match[1])>=first and isinstance(module,nn.Linear):names.append(name)
    if not names:raise ValueError('no qualified text Linear projections found')
    class LowRankLinear(nn.Module):
        def __init__(self,base):
            super().__init__();self.base=base
            self.a=nn.Parameter(torch.empty(spec.rank,base.in_features,device=base.weight.device,dtype=base.weight.dtype))
            self.b=nn.Parameter(torch.zeros(base.out_features,spec.rank,device=base.weight.device,dtype=base.weight.dtype))
            nn.init.kaiming_uniform_(self.a,a=math.sqrt(5));self.scale=spec.alpha/spec.rank
        def forward(self,x):return self.base(x)+torch.nn.functional.linear(torch.nn.functional.linear(x,self.a),self.b)*self.scale
    model.requires_grad_(False)
    for name in names:
        parent,attribute=name.rsplit('.',1);setattr(modules[parent],attribute,LowRankLinear(modules[name]))
    if any(p.requires_grad and not n.startswith('model.language_model.layers.') for n,p in model.named_parameters()):
        raise ValueError('vision/projector/head must remain frozen')
    return names


def balanced_schedule(dataset,steps,seed,indices=None):
    if type(steps) is not int or steps<1:raise ValueError('positive steps required')
    if indices is not None:
        if len(indices)!=steps or any(type(i) is not int or not 0<=i<len(dataset) for i in indices):raise ValueError('explicit target indices differ from schedule')
        return list(indices)
    rng=random.Random(seed);groups=defaultdict(lambda:defaultdict(list))
    for index,(episode,turn) in enumerate(dataset.targets):
        e=dataset.episodes[episode];kind=dataset.scenarios[e['task_id']]['kind']
        groups[(kind,e['family_sha256'],e['variant'])][episode].append(index)
    keys=sorted(groups);order=[]
    while len(order)<steps:
        rng.shuffle(keys)
        for key in keys:
            episode=rng.choice(sorted(groups[key]));order.append(rng.choice(groups[key][episode]))
            if len(order)==steps:break
    return order


def check_preflight(root,manifest,data,train_manifest,target_sha,contract):
    if verify(root)!=manifest:raise ValueError('owner preflight manifest differs')
    r=read_json(Path(root)/'summary.json')
    if (r.get('schema')!='power-grid-env.procedural-preflight/v1' or r.get('passed') is not True or r.get('failures') or
        r.get('targets')!=len(data) or r.get('checked_targets')!=len(data) or r.get('train_manifest_sha256')!=train_manifest or
        r.get('target_sha256')!=target_sha or r.get('contract_sha256')!=contract.fingerprint() or
        r.get('loader_sha256')!=hashlib.sha256(Path(__file__).with_name('engineering_factory.py').read_bytes()).hexdigest() or
        r.get('protocol_sha256')!=hashlib.sha256(Path(__file__).with_name('engineering_protocol.py').read_bytes()).hexdigest()):
        raise ValueError('all-target native preflight does not match this training profile')


def train(target_path,target_sha,corpus,manifest,preflight,preflight_manifest,out,steps=2,indices=None,seed=42,
          protocol='qwen-native-v1',lora=None,resume=None,resume_manifest=None,max_new_steps=None,context_tokens=24576):
    import torch
    from safetensors.torch import save_file,load_file
    from power_grid_env.qwen35_training import (local_target,load_native,adapter_state,restore_adapters,frozen_fingerprint,supervised_forward)
    from power_grid_env.evidence import package_fingerprint
    root=Path(out)
    if root.exists():raise ValueError('fresh training output required')
    if bool(resume)!=bool(resume_manifest):raise ValueError('resume needs an owner manifest')
    contract=RunContract(protocol=protocol,maximum_actions=24,context_tokens=context_tokens)
    data=ProceduralDataset(corpus,manifest,contract);check_preflight(preflight,preflight_manifest,data,manifest,target_sha,contract)
    spec=LoraSpec.model_validate(lora or {})
    order=balanced_schedule(data,steps,seed,indices)
    recipe=dict(contract=contract.model_dump(),lora=spec.model_dump(),seed=seed,learning_rate=.0001,gradient_clip=1.,steps=steps,
        schedule=order,sampling='kind/family/path groups,episode uniform,next-expert action uniform' if indices is None else 'explicit_debug_targets')
    identity=dict(target_sha256=target_sha,train_manifest_sha256=manifest,preflight_manifest_sha256=preflight_manifest,
        core_sha256=package_fingerprint(),trainer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    if max_new_steps is not None and (type(max_new_steps) is not int or max_new_steps<1):raise ValueError('positive chunk size required')
    previous=None;cursor=0;history=[]
    if resume:
        if verify(resume)!=resume_manifest:raise ValueError('resume anchor differs')
        previous=read_json(Path(resume)/'summary.json')
        if previous.get('schema')!='power-grid-env.trainer-v2/v1' or previous.get('identity')!=identity or previous.get('recipe')!=recipe:raise ValueError('resume recipe/identity differs')
        cursor=previous['completed_steps'];history=previous['history']
        if type(cursor) is not int or not 0<cursor<steps or len(history)!=cursor:raise ValueError('invalid or completed resume cursor')
    target=local_target(target_path,target_sha);torch.manual_seed(seed)
    processor,model=load_native(target,4);names=attach_language_lora(model,spec)
    frozen_before=frozen_fingerprint(model)
    params=[p for p in model.parameters() if p.requires_grad];optimizer=torch.optim.AdamW(params,lr=recipe['learning_rate'],weight_decay=0.)
    if previous:
        if previous['adapter_names']!=names:raise ValueError('adapter module identities differ')
        restore_adapters(model,load_file(str(Path(resume)/'adapter.safetensors')))
        restore_optimizer(model,optimizer,Path(resume)/'optimizer.safetensors',previous['optimizer_parameter_names'],cursor)
    initial=adapter_state(model)
    stop=min(steps,cursor+(max_new_steps if max_new_steps is not None else steps))
    for step in range(cursor,stop):
        batch,meta=data.batch(processor,order[step]);optimizer.zero_grad(set_to_none=True)
        loss,logits=supervised_forward(model,batch);loss.backward()
        if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in params):raise ValueError('missing/nonfinite LoRA gradients')
        grad=torch.nn.utils.clip_grad_norm_(params,recipe['gradient_clip'],error_if_nonfinite=True)
        if float(grad)<=0:raise ValueError('zero LoRA gradient')
        optimizer.step()
        if any(not bool(torch.isfinite(p).all()) for p in params):raise ValueError('nonfinite adapter weights')
        history.append(dict(step=step,**meta,loss=float(loss.detach()),gradient_norm=float(grad)))
        print(json.dumps(dict(step=step+1,steps=steps,loss=history[-1]['loss'],expert_target=order[step])),flush=True)
        del loss,logits
    state=adapter_state(model)
    if all(torch.equal(initial[k],v) for k,v in state.items()):raise ValueError('optimizer did not update adapters')
    if frozen_fingerprint(model)!=frozen_before:raise ValueError('frozen base changed')
    with torch.inference_mode():expected_loss,expected=supervised_forward(model,batch)
    root.mkdir(parents=True,exist_ok=False);save_file(state,str(root/'adapter.safetensors'))
    optimizer_names=save_optimizer(model,optimizer,root/'optimizer.safetensors')
    restore_adapters(model,{k:torch.zeros_like(v) for k,v in state.items()});restore_adapters(model,load_file(str(root/'adapter.safetensors')))
    with torch.inference_mode():actual_loss,actual=supervised_forward(model,batch)
    if not torch.equal(expected,actual) or not torch.equal(expected_loss,actual_loss):raise ValueError('adapter roundtrip not exact')
    receipt=dict(schema='power-grid-env.trainer-v2/v1',identity=identity,recipe=recipe,completed_steps=stop,schedule_complete=stop==steps,resumed_from_manifest=resume_manifest,
        adapter_names=names,trainable_parameters=sum(p.numel() for p in params),optimizer_parameter_names=optimizer_names,history=history,
        frozen_base_unchanged=True,adapter_roundtrip_exact=True,closed_loop_quality_gate_passed=False,closed_loop_quality_gate_status='not_run',
        scope='Provisional synthetic native-action SFT optimization;completed steps and train loss are not policy quality')
    write_json(root/'summary.json',receipt);seal(root)
    return {k:v for k,v in receipt.items() if k not in ('history','adapter_names','optimizer_parameter_names')}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--target',required=True);p.add_argument('--target-sha256',required=True)
    p.add_argument('--train',required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--preflight',required=True);p.add_argument('--preflight-manifest-sha256',required=True)
    p.add_argument('--out',required=True);p.add_argument('--steps',type=int,default=2);p.add_argument('--indices',type=int,nargs='+');p.add_argument('--last-blocks',type=int,default=2)
    p.add_argument('--resume');p.add_argument('--resume-manifest-sha256');p.add_argument('--max-new-steps',type=int);p.add_argument('--context-tokens',type=int,default=24576)
    a=p.parse_args();print(json.dumps(train(a.target,a.target_sha256,a.train,a.manifest_sha256,a.preflight,a.preflight_manifest_sha256,a.out,a.steps,a.indices,lora=dict(last_blocks=a.last_blocks),resume=a.resume,resume_manifest=a.resume_manifest_sha256,max_new_steps=a.max_new_steps,context_tokens=a.context_tokens)))
