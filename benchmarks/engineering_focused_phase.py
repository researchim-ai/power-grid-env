"""Fixed60-update corrective phase with native parent replay and retention.

Warm start is not same-phase resume: AdamW resets, parent weights are preserved.
Both scores stay TRAIN-only; failed quality gates are never promoted implicitly.
"""
import argparse
import hashlib
import json
import random
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_focused_curriculum import FocusedDataset
from benchmarks.engineering_primitive_pilot import order as parent_order
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.engineering_sft_runtime_v2 import NativePolicy
from benchmarks.engineering_staged_agent import bounded_json
from benchmarks.engineering_trainer_v2 import LoraSpec, attach_language_lora
from power_grid_env.study_artifacts import read_json, seal, verify


FILES=('engineering_focused_phase.py','engineering_focused_curriculum.py','engineering_primitive_pilot.py',
       'engineering_primitive_admission.py','engineering_primitive_curriculum.py','engineering_sft_runtime_v2.py',
       'engineering_staged_data.py','engineering_trainer_v2.py')


def sources():return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in FILES}


def parent_receipt(root,anchor,data,target_sha):
    if verify(root)!=anchor:raise ValueError('parent pilot manifest differs')
    r=read_json(Path(root)/'summary.json')
    if (r.get('schema')!='power-grid-env.primitive-native-pilot/v1' or r.get('complete') is not True or
            r.get('frozen_base_unchanged') is not True or r.get('adapter_roundtrip_exact') is not True or r.get('inference_release') is not False):
        raise ValueError('complete research parent required')
    if (r['identity']['target_sha256']!=target_sha or
            r['identity']['admission_manifest_sha256']!=data.plan['parent_manifest_sha256']):
        raise ValueError('parent target/ancestry differs')
    for name,expected in r['identity']['source_sha256'].items():
        if hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()!=expected:
            raise ValueError('parent implementation changed:'+name)
    original,nuisance=data.parent.pilot_indices()
    if (r['recipe']['indices']!=original or r['recipe']['nuisance_indices']!=nuisance or
            r['recipe']['schedule']!=parent_order(original) or r['recipe']['steps']!=30 or
            r['recipe']['lora']!=LoraSpec().model_dump() or len(r['history'])!=30 or len(r['after'])!=20):
        raise ValueError('complete parent recipe differs')
    for result,index in zip(r['after'],original+nuisance):
        row=data.parent.example(index)
        if result['row_id']!=row['id'] or sha(result['expected'])!=sha(row['answer']):
            raise ValueError('parent generated attribution differs')
        try:correct=sha(bounded_json(result['completion']))==sha(row['answer'])
        except (ValueError,TypeError):correct=False
        if result['correct'] is not correct:raise ValueError('parent generated score differs')
    if sum(x['correct'] for x in r['after'])!=r['after_correct']:raise ValueError('parent total differs')
    return r


def schedule(data):
    focused,_,original,_=data.selection()
    targets=[dict(source='focused',index=i,row_id=data.rows[i]['id']) for i in focused]
    targets += [dict(source='retention',index=i,row_id=data.parent.rows[i]['id']) for i in original]
    rng,result=random.Random(20261119),[]
    for _ in range(3):
        values=list(targets);rng.shuffle(values);result.extend(values)
    if len(result)!=60:raise ValueError('fixed60 schedule required')
    return result


def probes(data):
    a,b,c,d=data.selection()
    return ([dict(source='retention',index=i) for i in c+d]+
            [dict(source='focused',index=i) for i in a+b])


def generated(actor,data,phase):
    result=[]
    for entry in probes(data):
        dataset=data.parent if entry['source']=='retention' else data
        row=dataset.example(entry['index'])
        raw=actor(dict(messages=row['messages'],maximum_completion_tokens=128))
        try:correct=sha(bounded_json(raw))==sha(row['answer'])
        except (ValueError,TypeError):correct=False
        result.append(dict(**entry,row_id=row['id'],skill=row['skill'],phase=phase,
                           presentation=row['nuisance_variant'],completion=raw,expected=row['answer'],correct=correct))
        print(json.dumps(dict(phase=phase,source=entry['source'],index=entry['index'],correct=correct)),flush=True)
    return result


def totals(rows):
    return {source:sum(r['correct'] for r in rows if r['source']==source) for source in ('retention','focused')}


def run(focused,anchor,parent,parent_anchor,target,target_sha,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh focused phase required')
    data=FocusedDataset(focused,anchor,target_sha)
    prior=parent_receipt(parent,parent_anchor,data,target_sha)
    order=schedule(data)
    from power_grid_env.evidence import package_fingerprint
    if prior['identity']['core_sha256']!=package_fingerprint():raise ValueError('parent physics core differs')
    from power_grid_env.qwen35_training import (local_target,load_native,frozen_fingerprint,
        supervised_forward,adapter_state,restore_adapters)
    import torch
    from safetensors.torch import load_file,save_file
    torch.manual_seed(20261119)
    processor,model=load_native(local_target(target,target_sha),4)
    names=attach_language_lora(model,LoraSpec())
    if names!=prior['adapter_names']:raise ValueError('parent adapter modules differ')
    restore_adapters(model,load_file(str(Path(parent)/'adapter.safetensors')))
    frozen=frozen_fingerprint(model)
    params=[p for p in model.parameters() if p.requires_grad]
    optimizer=torch.optim.AdamW(params,lr=.0001,weight_decay=0.)
    identity=dict(focused_manifest_sha256=anchor,parent_manifest_sha256=parent_anchor,target_sha256=target_sha,
                  core_sha256=package_fingerprint(),source_sha256=sources())
    recipe=dict(seed=20261119,schedule=order,steps=60,learning_rate=.0001,gradient_clip=1.,lora=LoraSpec().model_dump(),
                objective='assistant_only_uniform_cross_entropy',optimizer_initialization='fresh_AdamW_preserve_parent_adapters',
                stop_rule='fixed60_no_automatic_extra_epochs',training_mix='ten_focused_and_ten_retention_originals_each_three_times')
    root.mkdir(parents=True);write_json(root/'plan.json',dict(identity=identity,recipe=recipe))
    history=[]
    try:
        actor=NativePolicy(processor,model)
        before=generated(actor,data,'before')
        retention=[r for r in before if r['source']=='retention']
        if [r['completion'] for r in retention]!=[r['completion'] for r in prior['after']]:
            raise ValueError('native parent generation replay differs')
        write_json(root/'before.json',before)
        initial=adapter_state(model)
        for step,entry in enumerate(order):
            dataset=data.parent if entry['source']=='retention' else data
            batch,meta=dataset.batch(processor,entry['index'])
            model.eval();optimizer.zero_grad(set_to_none=True)
            loss,logits=supervised_forward(model,batch);loss.backward()
            if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in params):raise ValueError('invalid focused gradients')
            grad=torch.nn.utils.clip_grad_norm_(params,1.,error_if_nonfinite=True)
            if float(grad)<=0:raise ValueError('zero focused gradient')
            optimizer.step()
            if any(not bool(torch.isfinite(p).all()) for p in params):raise ValueError('nonfinite focused weights')
            history.append(dict(step=step,**entry,**meta,loss=float(loss.detach()),gradient_norm=float(grad)))
            print(json.dumps(dict(step=step+1,total=60,source=entry['source'],loss=history[-1]['loss'])),flush=True)
            write_json(root/'partial.json',dict(complete=False,history=history))
            del loss,logits
        after=generated(actor,data,'after')
        state=adapter_state(model)
        if all(torch.equal(initial[k],v) for k,v in state.items()):raise ValueError('focused adapters did not update')
        if frozen_fingerprint(model)!=frozen:raise ValueError('focused phase changed frozen base')
        save_file(state,str(root/'adapter.safetensors'))
        batch,_=data.batch(processor,data.selection()[0][0])
        with torch.inference_mode():expected_loss,expected=supervised_forward(model,batch)
        restore_adapters(model,{k:torch.zeros_like(v) for k,v in state.items()})
        restore_adapters(model,load_file(str(root/'adapter.safetensors')))
        with torch.inference_mode():actual_loss,actual=supervised_forward(model,batch)
        if not torch.equal(expected,actual) or not torch.equal(expected_loss,actual_loss):raise ValueError('focused roundtrip differs')
        result=dict(schema='power-grid-env.focused-native-phase/v1',complete=True,identity=identity,recipe=recipe,
                    before=before,after=after,before_correct=totals(before),after_correct=totals(after),history=history,
                    targets_per_source=20,train_originals_per_source=10,train_base_networks=1,
                    parent_generation_replay_exact=True,frozen_base_unchanged=True,adapter_roundtrip_exact=True,
                    focused_and_retention_gate_passed=all(r['correct'] for r in after),
                    full_workflow_quality_gate_passed=False,independent_heldout_evaluated=False,inference_release=False,
                    scope='New fixed60-update corrective TRAIN phase from complete primitive pilot; '
                          'ten focused/ten retention originals and reordered-key probes; not causal isolation, heldout, policy or VLM qualification. No same-phase resume.')
        write_json(root/'summary.json',result);seal(root)
        return {k:v for k,v in result.items() if k not in ('before','after','history')}
    except Exception as exc:
        write_json(root/'failure.json',dict(complete=False,identity=identity,error=type(exc).__name__,detail=str(exc)[:1000]))
        seal(root);raise


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('focused-release','manifest-sha256','parent-pilot','parent-manifest-sha256','target','target-sha256','out'):
        p.add_argument('--'+key,required=True)
    a=p.parse_args()
    print(json.dumps(run(a.focused_release,a.manifest_sha256,a.parent_pilot,a.parent_manifest_sha256,a.target,a.target_sha256,a.out)))
