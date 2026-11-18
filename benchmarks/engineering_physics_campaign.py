"""Finite78-step new physics phase from adapter40,with matched full episodes.

Separate phase,fresh AdamW,same LoRA span. No automatic extra epochs,teacher
fallback,heldout tuning or checkpoint selection. Failed gates stay failed.
"""
import argparse,json,random
from pathlib import Path
from collections import Counter
from benchmarks.engineering_physics_curriculum import PhysicsDataset
from benchmarks.engineering_native_staged_policy import NativePolicy,load as load_policy,checkpoint
from benchmarks.engineering_staged_agent import run as episode
from benchmarks.engineering_value_loss import objective
from benchmarks.engineering_skill_tasks import identity,score
from benchmarks.engineering_trainer_v2 import LoraSpec
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,read_json,seal

TARGET='e763c82b7f4f1d5acea273b43674e45bcdb949fc91d000cb7537ea54b5fa0262'
FILES=('engineering_physics_campaign.py','engineering_physics_curriculum.py','engineering_native_staged_policy.py',
 'engineering_staged_agent.py','engineering_staged_data.py','engineering_constraint_tasks.py',
 'engineering_value_loss.py','engineering_grounding_runtime.py','engineering_grounding.py','engineering_trainer_v2.py')


def sources():
    names=dict(staged='engineering-staged-teaching-admission',paired='engineering-matched-stage-physics',visual='engineering-visual-grounding-release',
               audit='engineering-replacement-challenge-v2',catalog='engineering-catalog-selection-final')
    return {k:dict(path='reports/'+v+'-2026-11-18',manifest_sha256=verify('reports/'+v+'-2026-11-18')) for k,v in names.items()}


def order(indices):
    rng=random.Random(423);result=[]
    for _ in range(6):
        items=list(indices);rng.shuffle(items);result.extend(items)
    return result


def initial():
    r='reports/engineering-decision-payload-pilot-2026-11-18';p='reports/engineering-decision-payload-plan-admitted-2026-11-18'
    return dict(checkpoint=r,manifest_sha256=verify(r),plan=p,plan_manifest_sha256=verify(p))


def plan(target,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh physics recipe required')
    source=sources();data=PhysicsDataset(source);indices,transfer,case_ids=data.select();parent=initial()
    r,p=checkpoint(parent['checkpoint'],parent['manifest_sha256'],parent['plan'],parent['plan_manifest_sha256'],TARGET)
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    import torch
    torch.set_num_threads(4);t=local_target(target,TARGET);processor=local_qwen35_processor(t['local_checkpoint'],t['config_sha256'])
    rows=[]
    for i in indices+transfer:
        _,_,meta=data.batch(processor,i);rows.append(dict(index=i,**meta))
    cases=data.cases(case_ids)
    result=dict(schema='power-grid-env.physics-curriculum-plan/v1',target_sha256=TARGET,sources=source,initial=parent,seed=423,
        steps=78,indices=indices,transfer_indices=transfer,schedule=order(indices),row_ids={str(i):data.rows[i]['id'] for i in indices+transfer},
        case_ids=case_ids,full_episode_ids=[c['id'] for c in cases],full_episode_base_networks=len({c['base_project_id'] for c in cases}),
        native_admission=dict(passed=True,checked_targets=15,rows=rows),source_sha256={f:identity(Path(__file__).with_name(f)) for f in FILES},
        lora=p['lora'],learning_rate=.0001,gradient_clip=1.,value_weight=4.,context_tokens=8192,completion_tokens=128,
        optimizer_initialization='fresh_AdamW_for_new_phase_preserve_adapter40_weights',maximum_tool_calls_per_episode=24,
        selected_skills=dict(Counter(data.rows[i]['skill'] for i in indices)),thinking=False,optimizer_run=False,
        gate='all15_generated_answers_and_all3_full_v2_workflows',
        scope='New finite train-physics phase;3 workflows share one base network with catalog renaming/healthy initial variants;not heldout generalization or automatic continuation of old recipe')
    write_json(root/'summary.json',result);seal(root);return result


def load_plan(root,anchor):
    if verify(root)!=anchor:raise ValueError('owner physics plan differs')
    p=read_json(Path(root)/'summary.json')
    if p.get('schema')!='power-grid-env.physics-curriculum-plan/v1' or p.get('target_sha256')!=TARGET or p.get('steps')!=78 or p.get('optimizer_run') is not False:
        raise ValueError('unsupported physics phase')
    if set(p['source_sha256'])!=set(FILES):raise ValueError('complete physics source inventory required')
    for f,sha in p['source_sha256'].items():
        if identity(Path(__file__).with_name(f))!=sha:raise ValueError('physics phase source differs:'+f)
    data=PhysicsDataset(p['sources']);indices,transfer,ids=data.select()
    if (p['indices']!=indices or p['transfer_indices']!=transfer or p['case_ids']!=ids or p['schedule']!=order(indices) or
        p['row_ids']!={str(i):data.rows[i]['id'] for i in indices+transfer} or
        p['lora']!=LoraSpec().model_dump() or p['value_weight']!=4. or p['learning_rate']!=.0001 or p['gradient_clip']!=1. or
        p['seed']!=423 or p['context_tokens']!=8192 or p['completion_tokens']!=128 or p['thinking'] is not False or p['maximum_tool_calls_per_episode']!=24):
        raise ValueError('physics recipe selection/budgets differ')
    if p['full_episode_ids']!=[c['id'] for c in data.cases(ids)] or p['native_admission']['passed'] is not True or p['native_admission']['checked_targets']!=15:
        raise ValueError('physics evaluation/admission differs')
    parent=p['initial'];checkpoint(parent['checkpoint'],parent['manifest_sha256'],parent['plan'],parent['plan_manifest_sha256'],TARGET)
    return data,p


def skills(actor,data,p,candidate):
    import torch
    result=[]
    for i in p['indices']+p['transfer_indices']:
        messages,answer=data.messages(i);inputs=actor.processor.apply_chat_template(messages,add_generation_prompt=True,tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)
        n=inputs['input_ids'].shape[1]
        if n+128>8192:raise ValueError('physics skill generation context exceeds budget')
        actor.model.eval()
        with torch.inference_mode():ids=actor.model.generate(**inputs,do_sample=False,num_beams=1,max_new_tokens=128,use_cache=True,logits_to_keep=1,
            pad_token_id=actor.processor.tokenizer.pad_token_id,eos_token_id=actor.processor.tokenizer.eos_token_id)
        raw=actor.processor.tokenizer.decode(ids[0,n:],skip_special_tokens=True)
        row=dict(candidate=candidate,index=i,skill=data.rows[i]['skill'],group='train' if i in p['indices'] else 'untrained_question',expected=answer,completion=raw,**score(raw,answer))
        result.append(row);print(json.dumps(row),flush=True)
    return result


def workflows(actor,data,p,candidate,root):
    result=[]
    for c in data.cases(p['case_ids']):
        start=len(actor.calls);r=episode(c,actor);r['native_calls']=actor.calls[start:];r['candidate']=candidate
        write_json(root/(candidate+'-'+c['id']+'.json'),r);result.append(r)
        print(json.dumps(dict(candidate=candidate,task_id=c['id'],success=r['success'],calls=r['calls'],protocol_errors=r['protocol_errors'])),flush=True)
    return result


def train(plan_root,anchor,target,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh physics run required')
    data,p=load_plan(plan_root,anchor);parent=p['initial']
    actor=load_policy(parent['checkpoint'],parent['manifest_sha256'],parent['plan'],parent['plan_manifest_sha256'],target,TARGET)
    from power_grid_env.qwen35_training import supervised_forward,adapter_state,restore_adapters,frozen_fingerprint
    from power_grid_env.evidence import package_fingerprint
    from benchmarks.engineering_train import save_optimizer
    from safetensors.torch import save_file,load_file
    import torch
    torch.manual_seed(p['seed']);processor,model=actor.processor,actor.model;admission=[]
    for i in p['indices']+p['transfer_indices']:
        _,_,meta=data.batch(processor,i);admission.append(dict(index=i,**meta))
    if admission!=p['native_admission']['rows']:raise ValueError('actual physics native admission differs')
    root.mkdir(parents=True);core=package_fingerprint();frozen=frozen_fingerprint(model);initial_state=adapter_state(model)
    write_json(root/'state.json',dict(phase='before_evaluation',complete=False))
    before=skills(actor,data,p,'adapter40');before_workflows=workflows(actor,data,p,'adapter40',root/'episodes')
    write_json(root/'before.json',dict(complete=True,skills=before,workflow_successes=sum(r['success'] for r in before_workflows)))
    params=[v for v in model.parameters() if v.requires_grad];optimizer=torch.optim.AdamW(params,lr=.0001,weight_decay=0.)
    history=[];visual=[0]
    def count(*args):visual[0]+=1
    hook=model.model.visual.register_forward_hook(count)
    for step,i in enumerate(p['schedule']):
        model.train();batch,mask,meta=data.batch(processor,i);optimizer.zero_grad(set_to_none=True)
        ordinary,logits=supervised_forward(model,batch);labels=batch['labels'][batch['labels']!=-100]
        loss,metrics=objective(logits,labels,mask,4.)
        if abs(float(ordinary.detach())-metrics['mean_ce'])>1e-5:raise ValueError('physics causal loss alignment differs')
        loss.backward()
        if any(v.grad is None or not bool(torch.isfinite(v.grad).all()) for v in params):raise ValueError('nonfinite/missing physics gradients')
        grad=torch.nn.utils.clip_grad_norm_(params,1.,error_if_nonfinite=True)
        if float(grad)<=0:raise ValueError('zero physics gradient')
        optimizer.step()
        if any(not bool(torch.isfinite(v).all()) for v in params):raise ValueError('nonfinite physics weights')
        row=dict(step=step+1,**meta,metrics=metrics,weighted_loss=float(loss.detach()),gradient_norm=float(grad));history.append(row)
        write_json(root/'state.json',dict(phase='training',complete=False,completed_steps=step+1,total_steps=78));print(json.dumps(row),flush=True)
        del loss,ordinary,logits
    model.eval()
    if frozen_fingerprint(model)!=frozen:raise ValueError('physics phase changed frozen base/vision')
    state=adapter_state(model)
    if all(torch.equal(v,initial_state[k]) for k,v in state.items()):raise ValueError('physics phase made no update')
    save_file(state,str(root/'adapter.safetensors'));optimizer_names=save_optimizer(model,optimizer,root/'optimizer.safetensors')
    with torch.inference_mode():expected_loss,expected_logits=supervised_forward(model,batch)
    restore_adapters(model,{k:torch.zeros_like(v) for k,v in state.items()});restore_adapters(model,load_file(str(root/'adapter.safetensors')))
    with torch.inference_mode():actual_loss,actual_logits=supervised_forward(model,batch)
    if not torch.equal(expected_loss,actual_loss) or not torch.equal(expected_logits,actual_logits):raise ValueError('physics adapter roundtrip differs')
    write_json(root/'state.json',dict(phase='after_evaluation',complete=False,completed_steps=78))
    after=skills(actor,data,p,'physics78');after_workflows=workflows(actor,data,p,'physics78',root/'episodes');hook.remove()
    if visual[0]<18 or package_fingerprint()!=core:raise ValueError('physics vision/core qualification failed')
    for f,sha in p['source_sha256'].items():
        if identity(Path(__file__).with_name(f))!=sha:raise ValueError('physics source changed during phase')
    result=dict(schema='power-grid-env.physics-curriculum-result/v1',complete=True,steps=78,plan_manifest_sha256=anchor,core_sha256=core,
        initial_manifest_sha256=parent['manifest_sha256'],adapter_names=read_json(Path(parent['checkpoint'])/'summary.json')['adapter_names'],
        optimizer_parameter_names=optimizer_names,frozen_base_unchanged=True,adapter_roundtrip_exact=True,visual_encoder_forward_calls=visual[0],history=history,
        before=before,after=after,workflow_results=dict(adapter40=[dict(task_id=r['task_id'],success=r['success'],calls=r['calls']) for r in before_workflows],
            physics78=[dict(task_id=r['task_id'],success=r['success'],calls=r['calls']) for r in after_workflows]),
        score=dict(before_skills=sum(r['exact'] for r in before),after_skills=sum(r['exact'] for r in after),skill_targets=15,
            before_workflows=sum(r['success'] for r in before_workflows),after_workflows=sum(r['success'] for r in after_workflows),workflow_tasks=3),
        gate_passed=all(r['exact'] for r in after) and all(r['success'] for r in after_workflows),full_heldout_or_industrial_claim=False,
        scope='One finite train calibration phase;3 full workflows share one base network;new staged interface,no teacher fallback,no extra epochs or heldout tuning')
    write_json(root/'summary.json',result);write_json(root/'state.json',dict(phase='complete',complete=True,gate_passed=result['gate_passed']));seal(root)
    return {k:v for k,v in result.items() if k not in ('before','after','history')}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['plan','run']);parser.add_argument('--target',required=True);parser.add_argument('--out',required=True)
    parser.add_argument('--plan');parser.add_argument('--plan-manifest-sha256');a=parser.parse_args()
    if a.command=='plan':r=plan(a.target,a.out)
    else:
        if not a.plan or not a.plan_manifest_sha256:parser.error('run requires plan and owner manifest')
        r=train(a.plan,a.plan_manifest_sha256,a.target,a.out)
    print(json.dumps(r))
