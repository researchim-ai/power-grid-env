"""Hard TRAIN retention constraints with transactional AdamW backtracking.

Witnesses cover parent-correct TRAIN replies through EOS, including six key-order
views. Those views now influence training and are NOT independent transfer tests.
No guarantee is made for unseen cases or non-equivalent generation kernels.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_balanced_training import generated,metrics,validate_result
from benchmarks.engineering_focused_balance import load_plan
from benchmarks.engineering_focused_curriculum import FocusedDataset
from benchmarks.engineering_focused_phase import parent_receipt
from benchmarks.engineering_focused_audit import regressions
from benchmarks.engineering_trainer_v2 import LoraSpec,attach_language_lora
from benchmarks.engineering_sft_runtime_v2 import NativePolicy
from power_grid_env.study_artifacts import read_json,seal,verify


SCALES=(1.,.5,.25,.125,.0625)


def witness_batch(processor,messages,completion):
    import torch
    settings=dict(tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)
    prompt=processor.apply_chat_template(messages,add_generation_prompt=True,**settings)
    full=processor.apply_chat_template(messages+[dict(role='assistant',content=completion)],add_generation_prompt=False,**settings)
    n=prompt['input_ids'].shape[1]
    if not torch.equal(prompt['input_ids'],full['input_ids'][:,:n]):raise ValueError('witness prefix differs')
    suffix=full['input_ids'][0,n:]
    ends=(suffix==processor.tokenizer.eos_token_id).nonzero().flatten()
    if not len(ends):raise ValueError('witness needs actual generation EOS')
    stop=n+int(ends[0])+1
    if stop>8192 or stop-n>128:raise ValueError('witness exceeds native generation caps')
    batch={k:v[:,:stop] if hasattr(v,'ndim') and v.ndim==2 and v.shape[1]==full['input_ids'].shape[1] else v for k,v in full.items()}
    batch['labels']=batch['input_ids'].clone();batch['labels'][:,:n]=-100
    return batch


def transactional_step(model,optimizer,check,scales=SCALES):
    """Keep the same clipped gradient; rollback BOTH weights and AdamW on retry."""
    import torch
    from power_grid_env.qwen35_training import adapter_state,restore_adapters
    if not scales or any(type(v) not in (int,float) or not 0<v<=1 for v in scales):
        raise ValueError('bounded positive backtracking scales required')
    weights=adapter_state(model);state=copy.deepcopy(optimizer.state_dict())
    rates=[g['lr'] for g in optimizer.param_groups]
    attempts=[]
    try:
        for scale in scales:
            restore_adapters(model,weights);optimizer.load_state_dict(copy.deepcopy(state))
            for group,rate in zip(optimizer.param_groups,rates):group['lr']=rate*scale
            optimizer.step()
            if any(not bool(torch.isfinite(p).all()) for p in model.parameters() if p.requires_grad):
                raise ValueError('nonfinite guarded candidate')
            result=check()
            attempts.append(dict(scale=scale,**result))
            if result['passed']:
                for group,rate in zip(optimizer.param_groups,rates):group['lr']=rate
                return dict(accepted=True,accepted_scale=scale,attempts=attempts)
        restore_adapters(model,weights);optimizer.load_state_dict(state)
        return dict(accepted=False,accepted_scale=None,attempts=attempts)
    except Exception:
        restore_adapters(model,weights);optimizer.load_state_dict(state);raise


FILES=('engineering_retention_guard.py','engineering_frozen_prefix_cache.py','engineering_balanced_training.py','engineering_focused_balance.py',
       'engineering_focused_curriculum.py','engineering_staged_data.py','engineering_trainer_v2.py')
def sources():return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in FILES}


def run(plan_root,anchor,target,target_sha,out):
    root=Path(out)
    if root.exists() or verify(plan_root)!=anchor:raise ValueError('fresh guarded run and matching plan required')
    header=read_json(Path(plan_root)/'summary.json')
    data=FocusedDataset(header['focused_release'],header['focused_manifest_sha256'],target_sha)
    p=load_plan(plan_root,anchor,data)
    prior=parent_receipt(p['parent_pilot'],p['parent_manifest_sha256'],data,target_sha)
    from power_grid_env.evidence import package_fingerprint
    if package_fingerprint()!=prior['identity']['core_sha256']:raise ValueError('guarded core differs')
    import torch
    from safetensors.torch import load_file,save_file
    from power_grid_env.qwen35_training import (local_target,load_native,restore_adapters,
        supervised_forward,frozen_fingerprint,adapter_state)
    torch.manual_seed(20261119)
    processor,model=load_native(local_target(target,target_sha),4)
    names=attach_language_lora(model,LoraSpec())
    if names!=prior['adapter_names']:raise ValueError('guarded adapter inventory differs')
    restore_adapters(model,load_file(str(Path(p['parent_pilot'])/'adapter.safetensors')))
    frozen=frozen_fingerprint(model)
    params=[v for v in model.parameters() if v.requires_grad]
    optimizer=torch.optim.AdamW(params,lr=.0001,weight_decay=0.)
    witnesses=[]
    for r in prior['after']:
        if not r['correct']:continue
        row=data.parent.example(r['index'])
        witnesses.append(dict(row_id=row['id'],index=r['index'],batch=witness_batch(processor,row['messages'],r['completion']),
                              presentation=row['nuisance_variant']))
    if len(witnesses)!=12:raise ValueError('twelve declared parent-correct TRAIN witnesses required')
    from benchmarks.engineering_frozen_prefix_cache import FrozenPrefixCache
    prefix_cache=FrozenPrefixCache(model)
    cache_proofs=[dict(row_id=w['row_id'],**prefix_cache.prepare(w['row_id'],w['batch'])) for w in witnesses]
    guard_forward_calls=0
    def check():
        nonlocal guard_forward_calls
        model.eval()
        for w in witnesses:
            with torch.inference_mode():logits=prefix_cache.forward(w['row_id'],w['batch'])
            targets=w['batch']['input_ids'][w['batch']['labels']!=-100]
            guard_forward_calls+=1
            wrong=(logits[0].argmax(-1)!=targets).nonzero().flatten()
            if len(wrong):return dict(passed=False,failed_row_id=w['row_id'],first_mismatch=int(wrong[0]))
        return dict(passed=True,failed_row_id=None,first_mismatch=None)
    initial_witness=check()
    if not initial_witness['passed']:raise ValueError('native parent greedy witness does not match through EOS')
    identity=dict(plan_manifest_sha256=anchor,parent_manifest_sha256=p['parent_manifest_sha256'],target_sha256=target_sha,
                  core_sha256=package_fingerprint(),source_sha256=sources())
    recipe=dict(proposals=60,schedule=p['schedule'],learning_rate=.0001,gradient_clip=1.,seed=20261119,
                lora=LoraSpec().model_dump(),objective='uniform_sft_ce_with_hard_parent_greedy_witness_constraints',
                backtracking_scales=list(SCALES),optimizer_initialization='fresh_AdamW_original_primitive30',
                guard_train_indices=[w['index'] for w in witnesses],
                guard_uses_key_order_training_views=True,guard_runtime='bit_exact_frozen_prefix_cache',stop_rule='fixed60_proposals_no_extra_epochs')
    root.mkdir(parents=True);write_json(root/'plan.json',dict(identity=identity,recipe=recipe))
    history=[]
    try:
        actor=NativePolicy(processor,model)
        before=generated(actor,data,p,'before')
        if [r['completion'] for r in before if r['source']=='retention']!=[r['completion'] for r in prior['after']]:
            raise ValueError('guarded parent generation replay differs')
        for step,entry in enumerate(p['schedule']):
            dataset=data.parent if entry['source']=='retention' else data
            batch,meta=dataset.batch(processor,entry['index'])
            model.eval();optimizer.zero_grad(set_to_none=True)
            loss,logits=supervised_forward(model,batch);loss.backward()
            if any(v.grad is None or not bool(torch.isfinite(v.grad).all()) for v in params):raise ValueError('invalid guarded gradient')
            grad=torch.nn.utils.clip_grad_norm_(params,1.,error_if_nonfinite=True)
            if float(grad)<=0:raise ValueError('zero guarded gradient')
            # Free the SFT graph before running inference witnesses.
            loss_value=float(loss.detach());del loss,logits
            decision=transactional_step(model,optimizer,check)
            history.append(dict(step=step,**entry,**meta,sft_loss=loss_value,gradient_norm=float(grad),**decision))
            print(json.dumps(dict(step=step+1,proposals=60,accepted=decision['accepted'],scale=decision['accepted_scale'])),flush=True)
            write_json(root/'partial.json',dict(complete=False,history=history))
        after=generated(actor,data,p,'after')
        final_cache_proofs=[dict(row_id=w['row_id'],**prefix_cache.verify(w['row_id'],w['batch'])) for w in witnesses]
        if frozen_fingerprint(model)!=frozen:raise ValueError('guard changed frozen base')
        state=adapter_state(model);save_file(state,str(root/'adapter.safetensors'))
        batch=witnesses[0]['batch']
        with torch.inference_mode():expected_loss,expected=supervised_forward(model,batch)
        restore_adapters(model,{k:torch.zeros_like(v) for k,v in state.items()})
        restore_adapters(model,load_file(str(root/'adapter.safetensors')))
        with torch.inference_mode():actual_loss,actual=supervised_forward(model,batch)
        if not torch.equal(expected,actual) or not torch.equal(expected_loss,actual_loss):raise ValueError('guarded roundtrip differs')
        changes=regressions(before,after)
        result=dict(schema='power-grid-env.retention-guard-native-training/v1',complete=True,identity=identity,recipe=recipe,
                    before=before,after=after,before_metrics=metrics(before),after_metrics=metrics(after),history=history,
                    changes=changes,no_retention_regressions=not any(r['source']=='retention' for r in changes['regressed']),
                    all_probe_gate_passed=all(r['correct'] for r in after),accepted_optimizer_updates=sum(r['accepted'] for r in history),
                    tentative_optimizer_steps=sum(len(r['attempts']) for r in history),guard_forward_calls=guard_forward_calls,
                    prefix_cache_initial_proofs=cache_proofs,prefix_cache_final_proofs=final_cache_proofs,
                    parent_generation_replay_exact=True,initial_teacher_witness_passed=True,
                    frozen_base_unchanged=True,adapter_roundtrip_exact=True,
                    full_workflow_quality_gate_passed=False,independent_heldout_evaluated=False,inference_release=False,
                    scope='TRAIN-constrained backtracking:60 SFT proposals, up to5 LR trials each; weights and AdamW rollback together. '
                          'All12 old correct TRAIN replies constrain updates, including key-order views, so they are not independent transfer tests. '
                          'Actual final greedy generation is evaluated separately; no unseen-case or workflow guarantee.')
        validate_result(result,data,p,prior)
        write_json(root/'summary.json',result);seal(root)
        return {k:v for k,v in result.items() if k not in ('before','after','history')}
    except Exception as exc:
        write_json(root/'failure.json',dict(complete=False,identity=identity,error=type(exc).__name__,detail=str(exc)[:1000]))
        seal(root);raise


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('plan','manifest-sha256','target','target-sha256','out'):p.add_argument('--'+key,required=True)
    a=p.parse_args();print(json.dumps(run(a.plan,a.manifest_sha256,a.target,a.target_sha256,a.out)))
