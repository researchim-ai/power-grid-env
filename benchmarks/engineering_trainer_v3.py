"""Admitted text-mixture SFT with explicit warm start and exact phase resume.

A new phase resets AdamW explicitly; resume restores the SAME phase schedule,
adapter,AdamW and RNG. Neither operation means passing closed-loop quality.
"""
import argparse
from collections import Counter
import hashlib
import json
import re
from pathlib import Path
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_student_dataset import StudentCorrectionDataset,source_sha
from benchmarks.engineering_learning_mix import AdmittedTextMixture,check_student_preflight
from benchmarks.engineering_learning_gate import strict_contract
from benchmarks.engineering_trainer_v2 import LoraSpec,attach_language_lora,check_preflight
from benchmarks.engineering_train import save_optimizer,restore_optimizer
from benchmarks.engineering_target_semantics import verify_target
from power_grid_env.study_artifacts import verify,read_json,seal


DEPENDENCIES=('engineering_trainer_v3.py','engineering_trainer_v2.py','engineering_factory.py','engineering_student_dataset.py',
    'engineering_student_recovery.py','engineering_learning_mix.py','engineering_protocol.py','engineering_target_semantics.py','engineering_train.py')


def load_plan(root,anchor,target_sha):
    if verify(root)!=anchor:raise ValueError('owner mixture plan differs')
    r=read_json(Path(root)/'summary.json')
    if (r.get('schema')!='power-grid-env.admitted-text-mixture-plan/v1' or r.get('target_sha256')!=target_sha or
        r.get('contract')!=strict_contract().model_dump() or r.get('optimizer_run') is not False or
        r.get('source_sha256')!=source_sha('engineering_learning_mix.py')):raise ValueError('mixture plan target/profile/source differs')
    spec=r['sources']
    if spec.get('target_sha256')!=target_sha:raise ValueError('mixture source target differs')
    if not isinstance(r.get('spec_sha256'),str) or not re.fullmatch('[0-9a-f]{64}',r['spec_sha256']):
        raise ValueError('owner mixture source specification differs')
    a=ProceduralDataset(spec['train'],spec['train_manifest'],strict_contract())
    b=StudentCorrectionDataset(spec['corrections'],spec['corrections_manifest'],spec['train'],spec['train_manifest'],spec['suite'],spec['suite_manifest'],spec['student_eval'],spec['student_eval_manifest'])
    check_preflight(spec['train_preflight'],spec['train_preflight_manifest'],a,spec['train_manifest'],target_sha,strict_contract())
    check_student_preflight(spec['student_preflight'],spec['student_preflight_manifest'],b,target_sha)
    mix=AdmittedTextMixture(a,b);expected=mix.schedule(r['steps'],r['seed'])
    if (r.get('schedule')!=expected or r.get('available_targets')!={source:len(d) for source,d in mix.datasets.items()} or
        r.get('source_counts')!=dict(Counter(e['source'] for e in expected)) or
        r.get('duplicate_schedule_targets')!=len(expected)-len({(e['source'],e['index']) for e in expected})):raise ValueError('mixture schedule or inventory differs')
    return mix,r


def expected_action(mix,entry):
    data=mix.datasets[entry['source']];e,t=data.targets[entry['index']]
    return data.episodes[e]['events'][t]['action']


def state_digest(state):
    h=hashlib.sha256()
    for name,tensor in sorted(state.items()):
        h.update(json.dumps([name,list(tensor.shape),str(tensor.dtype)],separators=(',',':')).encode());h.update(tensor.detach().cpu().contiguous().numpy().tobytes())
    return h.hexdigest()


def train(target,target_sha,plan,plan_manifest,out,initial=None,initial_manifest=None,resume=None,resume_manifest=None,max_new_steps=None,lora=None):
    import torch
    from safetensors.torch import load_file,save_file
    from power_grid_env.qwen35_training import (local_target,load_native,adapter_state,restore_adapters,frozen_fingerprint,supervised_forward)
    from power_grid_env.evidence import package_fingerprint
    root=Path(out)
    if root.exists():raise ValueError('fresh v3 training output required')
    if bool(initial)!=bool(initial_manifest):raise ValueError('warm start requires an owner adapter manifest')
    if bool(resume)!=bool(resume_manifest):raise ValueError('resume requires an owner phase manifest')
    if max_new_steps is not None and (type(max_new_steps) is not int or max_new_steps<1):raise ValueError('positive chunk size required')
    mix,p=load_plan(plan,plan_manifest,target_sha);order=p['schedule'];contract=strict_contract();spec=LoraSpec.model_validate(lora or {})
    parent=None
    if initial:
        from benchmarks.engineering_v2_adapter import receipt
        parent=receipt(initial,initial_manifest,target_sha,contract)
        if parent['recipe']['lora']!=spec.model_dump():raise ValueError('warm-start LoRA architecture differs')
    recipe=dict(contract=contract.model_dump(),lora=spec.model_dump(),seed=p['seed'],steps=len(order),schedule=order,
        learning_rate=.0001,gradient_clip=1.,weight_decay=0.,optimizer_initialization='fresh_for_new_phase',runtime='cpu_float32')
    identity=dict(target_sha256=target_sha,plan_manifest_sha256=plan_manifest,initial_v2_manifest_sha256=initial_manifest,
        core_sha256=package_fingerprint(),source_sha256={n:source_sha(n) for n in DEPENDENCIES})
    previous=None;cursor=0;history=[]
    if resume:
        if verify(resume)!=resume_manifest:raise ValueError('owner phase resume differs')
        previous=read_json(Path(resume)/'summary.json')
        if previous.get('schema')!='power-grid-env.trainer-v3/v1' or previous.get('identity')!=identity or previous.get('recipe')!=recipe:raise ValueError('phase resume recipe/identity differs')
        cursor=previous['completed_steps'];history=previous['history']
        if type(cursor) is not int or not 0<cursor<len(order) or len(history)!=cursor:raise ValueError('invalid or completed phase cursor')
        if any(row.get('step')!=i or row.get('entry')!=order[i] for i,row in enumerate(history)):raise ValueError('phase history order differs')
    root.mkdir(parents=True,exist_ok=False);write_json(root/'run.json',dict(identity=identity,recipe=recipe))
    try:
        t=local_target(target,target_sha);torch.manual_seed(recipe['seed']);processor,model=load_native(t,4)
        names=attach_language_lora(model,spec);frozen_before=frozen_fingerprint(model)
        if parent:
            if parent['adapter_names']!=names:raise ValueError('warm-start module selection differs')
            restore_adapters(model,load_file(str(Path(initial)/'adapter.safetensors')))
        phase_initial=state_digest(adapter_state(model))
        if previous and previous.get('phase_initial_adapter_sha256')!=phase_initial:raise ValueError('phase initialization changed')
        params=[p for p in model.parameters() if p.requires_grad];optimizer=torch.optim.AdamW(params,lr=recipe['learning_rate'],weight_decay=0.)
        if previous:
            if previous.get('adapter_names')!=names:raise ValueError('phase resume module selection differs')
            restore_adapters(model,load_file(str(Path(resume)/'adapter.safetensors')))
            restore_optimizer(model,optimizer,Path(resume)/'optimizer.safetensors',previous['optimizer_parameter_names'],cursor)
        before=adapter_state(model);stop=min(len(order),cursor+(max_new_steps if max_new_steps is not None else len(order)))
        for step in range(cursor,stop):
            entry=order[step];batch,meta=mix.batch(processor,entry)
            semantic=verify_target(processor,batch,expected_action(mix,entry),contract)
            optimizer.zero_grad(set_to_none=True);loss,logits=supervised_forward(model,batch);loss.backward()
            if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in params):raise ValueError('missing/nonfinite phase gradients')
            grad=torch.nn.utils.clip_grad_norm_(params,recipe['gradient_clip'],error_if_nonfinite=True)
            if float(grad)<=0:raise ValueError('zero phase gradient')
            optimizer.step()
            if any(not bool(torch.isfinite(p).all()) for p in params):raise ValueError('nonfinite phase adapters')
            history.append(dict(step=step,entry=entry,**meta,**semantic,loss=float(loss.detach()),gradient_norm=float(grad)))
            print(json.dumps(dict(step=step+1,steps=len(order),source=entry['source'],action=entry['action'],loss=history[-1]['loss'])),flush=True)
            del loss,logits
        state=adapter_state(model)
        if all(torch.equal(before[k],v) for k,v in state.items()):raise ValueError('phase did not update adapters')
        if frozen_fingerprint(model)!=frozen_before:raise ValueError('phase changed frozen base weights')
        with torch.inference_mode():expected_loss,expected=supervised_forward(model,batch)
        save_file(state,str(root/'adapter.safetensors'));optimizer_names=save_optimizer(model,optimizer,root/'optimizer.safetensors')
        restore_adapters(model,{k:torch.zeros_like(v) for k,v in state.items()});restore_adapters(model,load_file(str(root/'adapter.safetensors')))
        with torch.inference_mode():actual_loss,actual=supervised_forward(model,batch)
        if not torch.equal(expected,actual) or not torch.equal(expected_loss,actual_loss):raise ValueError('phase adapter roundtrip differs')
        r=dict(schema='power-grid-env.trainer-v3/v1',identity=identity,recipe=recipe,completed_steps=stop,schedule_complete=stop==len(order),
            phase_initial_adapter_sha256=phase_initial,resumed_from_manifest=resume_manifest,adapter_names=names,optimizer_parameter_names=optimizer_names,
            trainable_parameters=sum(p.numel() for p in params),history=history,frozen_base_unchanged=True,adapter_roundtrip_exact=True,
            closed_loop_quality_gate_passed=False,closed_loop_quality_gate_status='not_run',eligible_for_gold_sft=False,
            scope='Admitted text-mixture optimization;explicit new phase or same-phase resume;no full-policy/VLM/GRPO qualification')
        write_json(root/'summary.json',r);seal(root)
        return {k:v for k,v in r.items() if k not in ('history','adapter_names','optimizer_parameter_names')}
    except Exception as exc:
        write_json(root/'failure.json',dict(complete=False,error=type(exc).__name__,detail=str(exc)[:1000],identity=identity));seal(root);raise


if __name__=='__main__':
    a=argparse.ArgumentParser()
    for name in ('target','target-sha256','plan','plan-manifest-sha256','out'):a.add_argument('--'+name,required=True)
    for name in ('initial','initial-manifest-sha256','resume','resume-manifest-sha256'):a.add_argument('--'+name)
    a.add_argument('--max-new-steps',type=int);a.add_argument('--last-blocks',type=int,default=2)
    p=a.parse_args();print(json.dumps(train(p.target,p.target_sha256,p.plan,p.plan_manifest_sha256,p.out,p.initial,p.initial_manifest_sha256,p.resume,p.resume_manifest_sha256,p.max_new_steps,dict(last_blocks=p.last_blocks))))
