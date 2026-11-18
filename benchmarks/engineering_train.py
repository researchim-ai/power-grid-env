"""Finite, resumable CPU action SFT. Safetensors checkpoints, no pickle."""
import argparse
import hashlib
import json
import random
from pathlib import Path
from power_grid_env.study_artifacts import verify, read_json, seal


def schedule(size, epochs, seed):
    if type(size) is not int or size < 1 or type(epochs) is not int or not 1 <= epochs <= 8:
        raise ValueError('invalid schedule')
    rng = random.Random(seed)
    result = []
    for _ in range(epochs):
        order = list(range(size)); rng.shuffle(order); result.extend(order)
    return result


def save_optimizer(model, optimizer, path):
    import torch
    from safetensors.torch import save_file
    tensors = {'cpu_rng_state': torch.get_rng_state()}
    names = []
    for name, p in model.named_parameters():
        if not p.requires_grad:
            continue
        names.append(name)
        state = optimizer.state.get(p, {})
        if set(state) != {'step','exp_avg','exp_avg_sq'}:
            raise ValueError('complete AdamW state required')
        for key,value in state.items():
            tensors[name+'/'+key] = value.detach().cpu().contiguous().clone()
    save_file(tensors,str(path))
    return names


def restore_optimizer(model, optimizer, path, names, cursor):
    import torch
    from safetensors.torch import load_file
    tensors = load_file(str(path))
    params = {n:p for n,p in model.named_parameters() if p.requires_grad}
    if list(params) != names:
        raise ValueError('optimizer parameter order differs')
    expected = {'cpu_rng_state'} | {n+'/'+k for n in names for k in ('step','exp_avg','exp_avg_sq')}
    if set(tensors) != expected:
        raise ValueError('optimizer tensors differ')
    for name,p in params.items():
        state = {}
        for key in ('step','exp_avg','exp_avg_sq'):
            value = tensors[name+'/'+key]
            if not bool(torch.isfinite(value).all()):
                raise ValueError('nonfinite optimizer state')
            if key == 'step':
                if value.numel()!=1 or float(value)!=cursor:
                    raise ValueError('AdamW step differs from cursor')
            elif value.shape!=p.shape or value.dtype!=p.dtype:
                raise ValueError('optimizer moment shape/dtype differs')
            state[key] = value.clone()
        optimizer.state[p] = state
    torch.set_rng_state(tensors['cpu_rng_state'])


def run(target_path,target_sha,corpus,manifest,out,epochs=1,seed=42,max_steps=None,resume=None,resume_manifest=None):
    import torch
    from safetensors.torch import save_file,load_file
    from benchmarks.engineering_sft import ActionDataset
    from power_grid_env.qwen35_training import (local_target,load_native,AdapterPlan,attach_adapters,
        adapter_state,restore_adapters,frozen_fingerprint,supervised_forward)
    from power_grid_env.evidence import package_fingerprint
    root=Path(out)
    if root.exists():raise ValueError('fresh training output required')
    if bool(resume)!=bool(resume_manifest):raise ValueError('resume path and owner manifest required together')
    if max_steps is not None and (type(max_steps) is not int or max_steps<1):raise ValueError('positive step budget required')
    dataset=ActionDataset(corpus,manifest,target_sha)
    order=schedule(len(dataset),epochs,seed)
    plan=AdapterPlan(seed=seed,indices=[0])
    identity=dict(target_sha256=target_sha,corpus_manifest_sha256=manifest,
        core_sha256=package_fingerprint(),trainer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        loader_sha256=hashlib.sha256(Path(__file__).with_name('engineering_sft.py').read_bytes()).hexdigest())
    cursor=0;history=[];previous=None
    if resume:
        if verify(resume)!=resume_manifest:raise ValueError('owner resume manifest differs')
        previous=read_json(Path(resume)/'summary.json')
        if (previous.get('schema')!='power-grid-env.engineering-action-training/v1' or
            previous.get('identity')!=identity or previous.get('schedule')!=order or
            previous.get('training_plan')!=plan.model_dump(mode='json')):
            raise ValueError('resume training identity or schedule differs')
        cursor=previous['optimizer_steps'];history=previous['history']
        if type(cursor) is not int or not 0<cursor<len(order) or len(history)!=cursor:
            raise ValueError('resume cursor/history invalid or schedule already complete')
        if any(r.get('step')!=i or r.get('index')!=order[i] for i,r in enumerate(history)):
            raise ValueError('resume history order differs')
    target=local_target(target_path,target_sha)
    torch.manual_seed(seed)
    processor,model=load_native(target,plan.threads)
    names=attach_adapters(model,plan)
    frozen_before=frozen_fingerprint(model)
    params=[p for p in model.parameters() if p.requires_grad]
    optimizer=torch.optim.AdamW(params,lr=plan.learning_rate,weight_decay=0.)
    if previous:
        if previous.get('adapter_names')!=names:raise ValueError('resume adapter modules differ')
        restore_adapters(model,load_file(str(Path(resume)/'adapter.safetensors')))
        restore_optimizer(model,optimizer,Path(resume)/'optimizer.safetensors',previous['optimizer_parameter_names'],cursor)
    stop=min(len(order),cursor+(max_steps if max_steps is not None else len(order)))
    for step in range(cursor,stop):
        batch,meta=dataset.batch(processor,order[step])
        optimizer.zero_grad(set_to_none=True)
        loss,logits=supervised_forward(model,batch)
        loss.backward()
        if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in params):
            raise ValueError('missing/nonfinite adapter gradients')
        grad=torch.nn.utils.clip_grad_norm_(params,plan.gradient_clip,error_if_nonfinite=True)
        if float(grad)<=0:raise ValueError('zero adapter gradient')
        optimizer.step()
        if any(not bool(torch.isfinite(p).all()) for p in params):raise ValueError('nonfinite adapter')
        history.append(dict(step=step,**meta,loss=float(loss.detach()),gradient_norm=float(grad)))
        print(json.dumps(dict(step=step+1,total=len(order),index=order[step],loss=history[-1]['loss'])),flush=True)
        del loss,logits
    if frozen_fingerprint(model)!=frozen_before:raise ValueError('frozen base changed')
    with torch.inference_mode():expected_loss,expected=supervised_forward(model,batch)
    root.mkdir(parents=True,exist_ok=False)
    save_file(adapter_state(model),str(root/'adapter.safetensors'))
    parameter_names=save_optimizer(model,optimizer,root/'optimizer.safetensors')
    restore_adapters(model,{k:torch.zeros_like(v) for k,v in adapter_state(model).items()})
    restore_adapters(model,load_file(str(root/'adapter.safetensors')))
    with torch.inference_mode():restored_loss,restored=supervised_forward(model,batch)
    if not torch.equal(expected,restored) or not torch.equal(expected_loss,restored_loss):
        raise ValueError('adapter roundtrip differs')
    receipt=dict(schema='power-grid-env.engineering-action-training/v1',identity=identity,
        target_sha256=target_sha,training_plan=plan.model_dump(mode='json'),schedule=order,
        optimizer_steps=stop,schedule_complete=stop==len(order),examples=len(dataset),epochs=epochs,
        adapter_names=names,optimizer_parameter_names=parameter_names,history=history,
        frozen_unchanged=True,adapter_roundtrip_exact=True,eligible_for_gold_sft=False,
        resumed_from_manifest=resume_manifest,
        scope='Provisional authored train action SFT; train loss is not heldout engineering quality; no VLM/GRPO qualification')
    (root/'summary.json').write_text(json.dumps(receipt,indent=2)+'\n');seal(root)
    return {k:v for k,v in receipt.items() if k not in ('history','schedule','adapter_names','optimizer_parameter_names')}


def main():
    p=argparse.ArgumentParser();p.add_argument('--target',required=True);p.add_argument('--target-sha256',required=True)
    p.add_argument('--corpus',required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--out',required=True)
    p.add_argument('--epochs',type=int,default=1);p.add_argument('--seed',type=int,default=42);p.add_argument('--max-steps',type=int)
    p.add_argument('--resume');p.add_argument('--resume-manifest-sha256')
    a=p.parse_args();print(json.dumps(run(a.target,a.target_sha256,a.corpus,a.manifest_sha256,a.out,a.epochs,a.seed,a.max_steps,a.resume,a.resume_manifest_sha256)))

if __name__=='__main__':main()
