"""Finite40-step named-operation/payload-weighted VLM diagnostic.

The recipe changes the objective from menu IDs to teacher-preferred operations.
Weighted loss is a hypothesis, not a proven cause of any measured improvement.
"""
import argparse,json,random
from pathlib import Path
from collections import Counter
from benchmarks.engineering_decision_tasks import DecisionDataset
from benchmarks.engineering_value_loss import objective
from benchmarks.engineering_skill_tasks import identity,score
from benchmarks.engineering_trainer_v2 import LoraSpec,attach_language_lora
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,read_json,seal

FILES=('engineering_decision_pilot.py','engineering_decision_tasks.py','engineering_value_loss.py',
       'engineering_operation_probe.py','engineering_grounding_runtime.py','engineering_grounding.py',
       'engineering_trainer_v2.py')
TARGET_SHA='e763c82b7f4f1d5acea273b43674e45bcdb949fc91d000cb7537ea54b5fa0262'


def source_spec():
    names=dict(card='engineering-card-curriculum-release',replacement='engineering-replacement-teaching-v2',
        replacement_audit='engineering-replacement-challenge-v2',catalog_parent='engineering-catalog-selection-final',visual='engineering-visual-grounding-release')
    return {k:dict(path='reports/'+v+'-2026-11-18',manifest_sha256=verify('reports/'+v+'-2026-11-18')) for k,v in names.items()}


def schedule(indices):
    rng=random.Random(421);result=[]
    for repeat in range(4):
        order=list(indices);rng.shuffle(order);result.extend(order)
    return result


def plan(target,target_sha,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh decision recipe required')
    if target_sha!=TARGET_SHA:raise ValueError('pinned decision target required')
    sources=source_spec();data=DecisionDataset(sources);indices=data.pilot_indices();transfer=data.transfer_indices()
    if len(indices)!=10 or len(set(indices))!=10 or set(indices)&set(transfer):raise ValueError('ten distinct exemplars and disjoint presentation probes required')
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    import torch
    torch.set_num_threads(4);t=local_target(target,target_sha);p=local_qwen35_processor(t['local_checkpoint'],t['config_sha256'])
    admission=[]
    for i in indices+transfer:
        _,_,meta=data.batch(p,i);admission.append(dict(index=i,**meta))
    result=dict(schema='power-grid-env.decision-pilot-plan/v1',sources=sources,target_sha256=target_sha,seed=421,
        steps=40,indices=indices,transfer_indices=transfer,schedule=schedule(indices),
        row_ids={str(i):data.rows[i]['id'] for i in indices+transfer},inventory=data.inventory,
        native_admission=dict(passed=True,checked_targets=len(admission),rows=admission),
        lora=LoraSpec().model_dump(),learning_rate=.0001,gradient_clip=1.,value_weight=4.,
        context_tokens=8192,completion_tokens=128,thinking=False,
        source_sha256={f:identity(Path(__file__).with_name(f)) for f in FILES},optimizer_run=False,
        gate='all_ten_preferred_teacher_answers_and_two_untrained_question_presentations_exact',
        scope='Fresh-base40-step train memorization diagnostic,new named-operation task and weighted objective;not full workflow,optimal action or isolated causal ablation')
    write_json(root/'summary.json',result);seal(root);return result


def load_plan(root,anchor):
    if verify(root)!=anchor:raise ValueError('owner decision plan differs')
    p=read_json(Path(root)/'summary.json')
    if p.get('schema')!='power-grid-env.decision-pilot-plan/v1' or p.get('target_sha256')!=TARGET_SHA or p.get('steps')!=40 or p.get('optimizer_run') is not False:
        raise ValueError('unsupported finite decision recipe')
    for f,sha in p['source_sha256'].items():
        if identity(Path(__file__).with_name(f))!=sha:raise ValueError('decision source differs:'+f)
    data=DecisionDataset(p['sources']);indices=data.pilot_indices();transfer=data.transfer_indices()
    if (p['indices']!=indices or p['transfer_indices']!=transfer or p['schedule']!=schedule(indices) or
        p['row_ids']!={str(i):data.rows[i]['id'] for i in indices+transfer} or
        p['lora']!=LoraSpec().model_dump() or p['value_weight']!=4. or p['seed']!=421 or
        p['learning_rate']!=.0001 or p['gradient_clip']!=1. or p['context_tokens']!=8192 or
        p['completion_tokens']!=128 or p['thinking'] is not False):raise ValueError('decision selection/recipe differs')
    if p['native_admission']['passed'] is not True or p['native_admission']['checked_targets']!=12:
        raise ValueError('all selected targets require admission')
    return data,p


def evaluate(model,processor,data,p,candidate):
    import torch
    model.eval();rows=[]
    for i in p['indices']+p['transfer_indices']:
        m,answer=data.messages(i);inputs=processor.apply_chat_template(m,add_generation_prompt=True,tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)
        n=inputs['input_ids'].shape[1]
        if n+128>8192:raise ValueError('decision inference context exceeds budget')
        with torch.inference_mode():ids=model.generate(**inputs,do_sample=False,num_beams=1,max_new_tokens=128,use_cache=True,logits_to_keep=1,
            pad_token_id=processor.tokenizer.pad_token_id,eos_token_id=processor.tokenizer.eos_token_id)
        raw=processor.tokenizer.decode(ids[0,n:],skip_special_tokens=True)
        r=data.rows[i];row=dict(index=i,row_id=r['id'],skill=r['skill'],candidate=candidate,
            group='train_exemplar' if i in p['indices'] else 'untrained_question_presentation',
            expected=answer,completion=raw,**score(raw,answer));rows.append(row);print(json.dumps(row),flush=True)
    return rows


def run(plan_root,anchor,target,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh decision run required')
    data,p=load_plan(plan_root,anchor)
    from power_grid_env.qwen35_training import local_target,load_native,supervised_forward,adapter_state,restore_adapters,frozen_fingerprint
    from power_grid_env.evidence import package_fingerprint
    from benchmarks.engineering_train import save_optimizer
    from safetensors.torch import load_file,save_file
    import torch
    torch.manual_seed(p['seed']);processor,model=load_native(local_target(target,p['target_sha256']),4)
    # Recheck every admitted row and its actual payload mask before any update.
    rows=[]
    for i in p['indices']+p['transfer_indices']:
        _,_,meta=data.batch(processor,i);rows.append(dict(index=i,**meta))
    if rows!=p['native_admission']['rows']:raise ValueError('actual decision admission differs')
    root.mkdir(parents=True);core=package_fingerprint()
    write_json(root/'state.json',dict(phase='base_evaluation',complete=False));before=evaluate(model,processor,data,p,'base')
    write_json(root/'before.json',dict(complete=True,rows=before))
    names=attach_language_lora(model,LoraSpec.model_validate(p['lora']));frozen=frozen_fingerprint(model);initial=adapter_state(model)
    params=[v for v in model.parameters() if v.requires_grad];optim=torch.optim.AdamW(params,lr=p['learning_rate'],weight_decay=0.)
    history=[];visual=[0]
    def count(*args):visual[0]+=1
    hook=model.model.visual.register_forward_hook(count)
    for step,i in enumerate(p['schedule']):
        model.train();batch,mask,meta=data.batch(processor,i);optim.zero_grad(set_to_none=True)
        mean_loss,logits=supervised_forward(model,batch);targets=batch['labels'][batch['labels']!=-100]
        loss,metrics=objective(logits,targets,mask,p['value_weight'])
        if abs(metrics['mean_ce']-float(mean_loss.detach()))>1e-5:raise ValueError('causal unweighted CE alignment differs')
        loss.backward()
        if any(v.grad is None or not bool(torch.isfinite(v.grad).all()) for v in params):raise ValueError('missing/nonfinite decision gradients')
        grad=torch.nn.utils.clip_grad_norm_(params,1.,error_if_nonfinite=True)
        if float(grad)<=0:raise ValueError('zero decision gradient')
        optim.step()
        if any(not bool(torch.isfinite(v).all()) for v in params):raise ValueError('nonfinite decision weights')
        row=dict(step=step+1,**meta,metrics=metrics,weighted_loss=float(loss.detach()),gradient_norm=float(grad));history.append(row)
        write_json(root/'state.json',dict(phase='training',complete=False,completed_steps=step+1,total_steps=40));print(json.dumps(row),flush=True)
        del loss,mean_loss,logits
    model.eval()
    if frozen_fingerprint(model)!=frozen:raise ValueError('frozen base or vision changed')
    state=adapter_state(model)
    if all(torch.equal(v,initial[k]) for k,v in state.items()):raise ValueError('decision optimizer made no update')
    save_file(state,str(root/'adapter.safetensors'));optimizer_names=save_optimizer(model,optim,root/'optimizer.safetensors')
    with torch.inference_mode():expected_loss,expected_logits=supervised_forward(model,batch)
    restore_adapters(model,{k:torch.zeros_like(v) for k,v in state.items()});restore_adapters(model,load_file(str(root/'adapter.safetensors')))
    with torch.inference_mode():actual_loss,actual_logits=supervised_forward(model,batch)
    if not torch.equal(expected_loss,actual_loss) or not torch.equal(expected_logits,actual_logits):raise ValueError('decision adapter roundtrip differs')
    write_json(root/'state.json',dict(phase='after_evaluation',complete=False,completed_steps=40));after=evaluate(model,processor,data,p,'adapter40');hook.remove()
    if visual[0]<12 or package_fingerprint()!=core:raise ValueError('decision vision pathway/core changed')
    for f,sha in p['source_sha256'].items():
        if identity(Path(__file__).with_name(f))!=sha:raise ValueError('source changed during decision pilot')
    result=dict(schema='power-grid-env.decision-pilot-result/v1',complete=True,plan_manifest_sha256=anchor,steps=40,core_sha256=core,
        adapter_names=names,optimizer_parameter_names=optimizer_names,frozen_base_unchanged=True,adapter_roundtrip_exact=True,
        visual_encoder_forward_calls=visual[0],before=before,after=after,history=history,gate_passed=all(x['exact'] for x in after),
        exact_counts={c:dict(correct=sum(x['exact'] for x in values),total=len(values),
            operation=sum(x['exact'] for x in values if x['skill']=='operation'),perception=sum(x['exact'] for x in values if x['skill']=='perception')) for c,values in [('base',before),('adapter40',after)]},
        full_workflow_or_generalization_claim=False,scope='Ten train preferred-answer exemplars plus two untrained question presentations;new weighted objective;not a controlled weight-only superiority claim')
    write_json(root/'summary.json',result);write_json(root/'state.json',dict(phase='complete',complete=True,gate_passed=result['gate_passed']));seal(root)
    return {k:v for k,v in result.items() if k not in ('before','after','history')}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['plan','run']);parser.add_argument('--target',required=True);parser.add_argument('--out',required=True)
    parser.add_argument('--plan');parser.add_argument('--plan-manifest-sha256');a=parser.parse_args()
    if a.command=='plan':r=plan(a.target,TARGET_SHA,a.out)
    else:
        if not a.plan or not a.plan_manifest_sha256:parser.error('run requires plan and owner manifest')
        r=run(a.plan,a.plan_manifest_sha256,a.target,a.out)
    print(json.dumps(r))
