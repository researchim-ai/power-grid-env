"""Source-pinned balanced60 consumer and a predeclared retention-KL arm.

Both arms start from the same primitive30 parent and share the SFT schedule.
The anchor arm adds payload-distribution KL on previously correct TRAIN originals.
No held-out labels, automatic extra updates, checkpoint selection or inference release.
"""
import argparse
import hashlib
import json
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_focused_balance import load_plan
from benchmarks.engineering_focused_curriculum import FocusedDataset
from benchmarks.engineering_focused_phase import parent_receipt
from benchmarks.engineering_focused_audit import regressions
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.engineering_sft_runtime_v2 import NativePolicy
from benchmarks.engineering_staged_agent import bounded_json
from benchmarks.engineering_trainer_v2 import LoraSpec,attach_language_lora
from benchmarks.engineering_value_loss import payload_mask
from power_grid_env.study_artifacts import read_json,seal,verify


FILES=('engineering_balanced_training.py','engineering_focused_balance.py','engineering_focused_curriculum.py',
       'engineering_focused_phase.py','engineering_sft_runtime_v2.py','engineering_staged_data.py',
       'engineering_value_loss.py','engineering_trainer_v2.py')
def sources():return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in FILES}


def payload_kl(student_logits,teacher_log_probs):
    import torch
    if student_logits.ndim!=2 or student_logits.shape!=teacher_log_probs.shape or student_logits.shape[0]<1:
        raise ValueError('aligned nonempty payload logits required')
    teacher=teacher_log_probs.detach().float()
    if not bool(torch.isfinite(teacher).all()) or not bool(torch.isfinite(student_logits).all()):
        raise ValueError('finite distillation logits required')
    if not torch.allclose(torch.logsumexp(teacher,-1),torch.zeros(teacher.shape[0]),atol=1e-4,rtol=0):
        raise ValueError('normalized parent log probabilities required')
    student=student_logits.float().log_softmax(-1)
    return (teacher.exp()*(teacher-student)).sum(-1).mean()


def probe_entries(data,p):
    original,nuisance=data.parent.pilot_indices()
    return ([dict(source='retention',index=i,group='common_retention') for i in original+nuisance]+
            [dict(source='focused',index=i,group='common_focused') for i in p['common_focused_probe_indices']]+
            [dict(source='focused',index=i,group='additional_contrasts') for i in p['additional_focused_probe_indices']])


def generated(actor,data,p,phase):
    rows=[]
    for entry in probe_entries(data,p):
        dataset=data.parent if entry['source']=='retention' else data
        row=dataset.example(entry['index'])
        raw=actor(dict(messages=row['messages'],maximum_completion_tokens=128))
        try:correct=sha(bounded_json(raw))==sha(row['answer'])
        except (ValueError,TypeError):correct=False
        rows.append(dict(**entry,row_id=row['id'],presentation=row['nuisance_variant'],phase=phase,
                         expected=row['answer'],completion=raw,correct=correct))
        print(json.dumps(dict(phase=phase,group=entry['group'],index=entry['index'],correct=correct)),flush=True)
    return rows


def metrics(rows):
    return {g:dict(correct=sum(r['correct'] for r in rows if r['group']==g),total=sum(r['group']==g for r in rows))
            for g in ('common_retention','common_focused','additional_contrasts')}


def validate_result(report,data,p,prior):
    for phase in ('before','after'):
        if len(report[phase])!=len(probe_entries(data,p)):raise ValueError('complete paired probes required')
        for result,entry in zip(report[phase],probe_entries(data,p)):
            dataset=data.parent if entry['source']=='retention' else data
            row=dataset.example(entry['index'])
            if any(result[k]!=v for k,v in entry.items()) or result['row_id']!=row['id'] or sha(result['expected'])!=sha(row['answer']):
                raise ValueError('balanced generated attribution differs')
            try:correct=sha(bounded_json(result['completion']))==sha(row['answer'])
            except (ValueError,TypeError):correct=False
            if result['correct'] is not correct:raise ValueError('balanced generated score differs')
        if report[phase+'_metrics']!=metrics(report[phase]):raise ValueError('balanced aggregate differs')
    before=[r['completion'] for r in report['before'] if r['source']=='retention']
    if before!=[r['completion'] for r in prior['after']]:raise ValueError('balanced native parent replay differs')
    changes=regressions(report['before'],report['after'])
    if report['changes']!=changes:raise ValueError('individual regressions differ')
    preserved=not any(r['source']=='retention' for r in changes['regressed'])
    if report['no_retention_regressions'] is not preserved or report['all_probe_gate_passed'] is not all(r['correct'] for r in report['after']):
        raise ValueError('balanced quality promotion differs')
    if report['full_workflow_quality_gate_passed'] is not False or report['inference_release'] is not False:
        raise ValueError('TRAIN pilot cannot promote workflow/inference quality')


def run(plan_root,plan_anchor,target,target_sha,arm,out):
    if arm not in ('balanced','anchored'):raise ValueError('predeclared balanced or anchored arm required')
    root=Path(out)
    if root.exists():raise ValueError('fresh balanced phase required')
    if verify(plan_root)!=plan_anchor:raise ValueError('owner balanced plan differs')
    header=read_json(Path(plan_root)/'summary.json')
    data=FocusedDataset(header['focused_release'],header['focused_manifest_sha256'],target_sha)
    p=load_plan(plan_root,plan_anchor,data) # Coverage/replay BEFORE opening model weights.
    if p['target_sha256']!=target_sha:raise ValueError('balanced target differs')
    prior=parent_receipt(p['parent_pilot'],p['parent_manifest_sha256'],data,target_sha)
    from power_grid_env.evidence import package_fingerprint
    if prior['identity']['core_sha256']!=package_fingerprint():raise ValueError('parent core differs')
    import torch
    from safetensors.torch import load_file,save_file
    from power_grid_env.qwen35_training import (local_target,load_native,restore_adapters,
        supervised_forward,frozen_fingerprint,adapter_state)
    torch.manual_seed(20261119)
    processor,model=load_native(local_target(target,target_sha),4)
    names=attach_language_lora(model,LoraSpec())
    if names!=prior['adapter_names']:raise ValueError('balanced adapter inventory differs')
    restore_adapters(model,load_file(str(Path(p['parent_pilot'])/'adapter.safetensors')))
    frozen=frozen_fingerprint(model)
    params=[v for v in model.parameters() if v.requires_grad]
    optimizer=torch.optim.AdamW(params,lr=.0001,weight_decay=0.)
    original=data.parent.pilot_indices()[0]
    anchor_indices=[r['index'] for r in prior['after'] if r['index'] in original and r['correct']]
    if len(set(anchor_indices))!=len(anchor_indices) or not anchor_indices:raise ValueError('correct TRAIN original anchors required')
    identity=dict(plan_manifest_sha256=plan_anchor,parent_manifest_sha256=p['parent_manifest_sha256'],
                  target_sha256=target_sha,core_sha256=package_fingerprint(),source_sha256=sources())
    recipe=dict(arm=arm,steps=60,schedule=p['schedule'],seed=20261119,lora=LoraSpec().model_dump(),
                learning_rate=.0001,gradient_clip=1.,uniform_sft_ce=True,
                payload_kl_weight=2. if arm=='anchored' else 0.,anchor_indices=anchor_indices if arm=='anchored' else [],
                optimizer_initialization='fresh_AdamW_from_original_primitive30',stop_rule='fixed60_no_extensions')
    root.mkdir(parents=True);write_json(root/'plan.json',dict(identity=identity,recipe=recipe))
    history=[]
    try:
        actor=NativePolicy(processor,model)
        before=generated(actor,data,p,'before')
        if [r['completion'] for r in before if r['source']=='retention']!=[r['completion'] for r in prior['after']]:
            raise ValueError('balanced parent replay differs before updates')
        write_json(root/'before.json',before)
        cache={};anchor_meta=[]
        if arm=='anchored':
            for index in anchor_indices:
                batch,meta=data.parent.batch(processor,index)
                model.eval()
                with torch.inference_mode():_,logits=supervised_forward(model,batch)
                ids=batch['input_ids'][batch['labels']!=-100]
                mask=torch.tensor(payload_mask(processor.tokenizer,ids,data.parent.rows[index]['answer']),dtype=torch.bool)
                teacher=logits[0,mask].float().log_softmax(-1).detach().clone()
                witness=float((teacher.argmax(-1)==ids[mask]).float().mean())
                if witness!=1.:raise ValueError('correct-parent payload teacher witness differs')
                cache[index]=(batch,mask,teacher)
                anchor_meta.append(dict(index=index,row_id=data.parent.rows[index]['id'],payload_tokens=int(mask.sum()),gold_top1_fraction=witness))
                del logits
        initial=adapter_state(model)
        for step,entry in enumerate(p['schedule']):
            dataset=data.parent if entry['source']=='retention' else data
            batch,meta=dataset.batch(processor,entry['index'])
            model.eval();optimizer.zero_grad(set_to_none=True)
            sft,logits=supervised_forward(model,batch)
            loss=sft;kl=None;anchor_index=None
            if arm=='anchored':
                anchor_index=anchor_indices[step%len(anchor_indices)]
                anchor_batch,mask,teacher=cache[anchor_index]
                _,anchor_logits=supervised_forward(model,anchor_batch)
                kl=payload_kl(anchor_logits[0,mask],teacher)
                loss=sft+2.*kl
            loss.backward()
            if any(v.grad is None or not bool(torch.isfinite(v.grad).all()) for v in params):raise ValueError('invalid balanced gradient')
            grad=torch.nn.utils.clip_grad_norm_(params,1.,error_if_nonfinite=True)
            if float(grad)<=0:raise ValueError('zero balanced gradient')
            optimizer.step()
            if any(not bool(torch.isfinite(v).all()) for v in params):raise ValueError('nonfinite balanced weights')
            history.append(dict(step=step,**entry,**meta,sft_loss=float(sft.detach()),
                                payload_kl=float(kl.detach()) if kl is not None else 0.,
                                anchor_index=anchor_index,objective_loss=float(loss.detach()),gradient_norm=float(grad)))
            print(json.dumps(dict(step=step+1,total=60,arm=arm,loss=history[-1]['objective_loss'])),flush=True)
            write_json(root/'partial.json',dict(complete=False,history=history))
            del loss,sft,logits
            if kl is not None:del kl,anchor_logits
        after=generated(actor,data,p,'after')
        state=adapter_state(model)
        if all(torch.equal(initial[k],v) for k,v in state.items()):raise ValueError('no balanced update')
        if frozen_fingerprint(model)!=frozen:raise ValueError('balanced frozen base changed')
        save_file(state,str(root/'adapter.safetensors'))
        batch,_=data.parent.batch(processor,original[0])
        with torch.inference_mode():expected_loss,expected=supervised_forward(model,batch)
        restore_adapters(model,{k:torch.zeros_like(v) for k,v in state.items()})
        restore_adapters(model,load_file(str(root/'adapter.safetensors')))
        with torch.inference_mode():actual_loss,actual=supervised_forward(model,batch)
        if not torch.equal(expected,actual) or not torch.equal(expected_loss,actual_loss):raise ValueError('balanced roundtrip differs')
        changes=regressions(before,after)
        result=dict(schema='power-grid-env.balanced-native-training/v1',complete=True,identity=identity,recipe=recipe,
                    before=before,after=after,before_metrics=metrics(before),after_metrics=metrics(after),history=history,
                    changes=changes,no_retention_regressions=not any(r['source']=='retention' for r in changes['regressed']),
                    all_probe_gate_passed=all(r['correct'] for r in after),anchor_cache=anchor_meta,
                    parent_generation_replay_exact=True,frozen_base_unchanged=True,adapter_roundtrip_exact=True,
                    full_workflow_quality_gate_passed=False,independent_heldout_evaluated=False,inference_release=False,
                    scope='Two predeclared TRAIN-only correction arms: identical balanced SFT schedule/parent/LR/span; '
                          'anchored arm adds parent-payload KL and extra forward computation on correct TRAIN originals. '
                          'Common20+20 and additional12 reported separately; no unseen-family or workflow qualification.')
        validate_result(result,data,p,prior)
        write_json(root/'summary.json',result);seal(root)
        return {k:v for k,v in result.items() if k not in ('before','after','history')}
    except Exception as exc:
        write_json(root/'failure.json',dict(complete=False,identity=identity,error=type(exc).__name__,detail=str(exc)[:1000]))
        seal(root);raise


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('plan','manifest-sha256','target','target-sha256','out'):p.add_argument('--'+key,required=True)
    p.add_argument('--arm',choices=['balanced','anchored'],required=True)
    a=p.parse_args();print(json.dumps(run(a.plan,a.manifest_sha256,a.target,a.target_sha256,a.arm,a.out)))
