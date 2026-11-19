"""TRAIN-only decision-state coverage beyond a single successful teacher path.

Each reachable preview subset has a solver-certified successful continuation.
Old test outcomes are regression evidence after first disclosure, never labels.
"""
import argparse
import copy
import hashlib
import itertools
import json
from pathlib import Path
import time

from benchmarks.engineering_assisted_workflow import control
from benchmarks.engineering_compact_workflow import CompactEpisode,request,decode
from benchmarks.engineering_cpu_workflow import load
from benchmarks.engineering_gpu_workflow import PLAN,ANCHOR,schedule,frozen_hash,evaluate
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.engineering_workflow_sft import pack
from power_grid_env.study_artifacts import read_json,verify,seal


def corpus(p):
    rows={};proofs=[]
    def continue_from(owner,case,previous=None):
        path=[];collected=[]
        while not owner.episode.engine.state['finished'] and owner.calls<10:
            public=owner.public();query=request(public,previous)
            action,_=decode(control(query),public)
            row=dict(split='train',family_sha256=case['family_sha256'],base_project_id=case['base_project_id'],
                task_id=case['id'],messages=query['messages'],answer=action)
            row['id']=sha(row);collected.append(row)
            previous=owner.apply(action);path.append(action)
        terminal=owner.episode.engine.state['terminal']
        if not terminal or terminal['success'] is not True:raise ValueError('every teaching continuation must physically complete')
        for row in collected:rows[row['id']]=row
        proofs.append(dict(task_id=case['id'],continuation=path,success=True,physics_checks=owner.physics_checks))
    for item in read_json(Path(p['parent_plan'])/'train-episodes.json'):
        case=item['case']
        if case['split']!='train':raise ValueError('heldout decision coverage forbidden')
        continue_from(CompactEpisode(case),case)
        # Recovery from repeated actual studies: completion must be explicit.
        if case['id'].startswith('healthy-'):
            for repeats in (2,3,4):
                owner=CompactEpisode(case)
                for _ in range(repeats):previous=owner.apply(dict(name='simulate',arguments={}))
                continue_from(owner,case,previous)
            continue
        base=CompactEpisode(case);base.apply(dict(name='simulate',arguments={}))
        public=base.public();line=public['current_result']['overloaded_branches'][0]['id']
        catalogue=[c['id'] for c in public['scene']['catalog']]
        # Exhaustive 2^4 preview subsets include good-before-bad and all-bad.
        for size in range(1,len(catalogue)+1):
            for subset in itertools.combinations(catalogue,size):
                owner=copy.deepcopy(base);previous=None
                for cid in subset:
                    previous=owner.apply(dict(name='preview_line_catalog',arguments=dict(equipment_id=line,catalog_id=cid)))
                    if not previous['ok']:raise ValueError('preview coverage transition rejected')
                continue_from(owner,case,previous)
    return sorted(rows.values(),key=lambda r:r['id']),proofs


def build(out):
    p,_=load(PLAN,ANCHOR);rows,proofs=corpus(p);root=Path(out)
    if root.exists():raise ValueError('fresh decision coverage corpus required')
    root.mkdir(parents=True)
    with (root/'train.jsonl').open('w') as f:
        for row in rows:f.write(json.dumps(row)+'\n')
    write_json(root/'proofs.json',proofs)
    result=dict(schema='power-grid-env.workflow-decision-coverage/v1',parent_plan=PLAN,parent_manifest_sha256=ANCHOR,
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),records=len(rows),continuations=len(proofs),
        bases=len({r['base_project_id'] for r in rows}),families=len({r['family_sha256'] for r in rows}),
        action_counts={name:sum(r['answer']['name']==name for r in rows) for name in ('simulate','preview_line_catalog','set_line_catalog','submit')},
        targets=p['targets'],optimizer_steps=240,seed=20261120,learning_rate=1e-4,gradient_accumulation=4,
        lora=dict(rank=16,alpha=32.,last_blocks=8),test_labels_used=False,
        evaluation_scope='Previously disclosed dev/test are regression checks only. No new unbiased heldout claim.',
        scope='Exhaustive reachable preview subsets and repeated-study recovery on the original eight TRAIN bases; no new physical/topological diversity.')
    write_json(root/'summary.json',result);seal(root);return result


def train(data,anchor,candidate,out,device):
    import torch,transformers
    from transformers import Qwen3_5ForConditionalGeneration
    from safetensors.torch import save_file,load_file
    from benchmarks.engineering_trainer_v2 import attach_language_lora,LoraSpec
    from power_grid_env.qwen35_training import local_target,supervised_forward,adapter_state,restore_adapters
    from power_grid_env.vlm_batches import local_qwen35_processor
    if verify(data)!=anchor:raise ValueError('decision coverage manifest differs')
    p=read_json(Path(data)/'summary.json')
    if p['source_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('decision coverage code differs')
    parent,cases=load(PLAN,ANCHOR);rebuilt,_=corpus(parent)
    rows=[json.loads(line) for line in (Path(data)/'train.jsonl').read_text().splitlines()]
    if sha(rows)!=sha(rebuilt):raise ValueError('decision coverage labels do not replay')
    spec=p['targets'][candidate];target=local_target(spec['path'],spec['sha256'])
    torch.set_num_threads(4);torch.manual_seed(p['seed']);torch.cuda.manual_seed_all(p['seed'])
    processor=local_qwen35_processor(target['local_checkpoint'],target['config_sha256'])
    batches=[];checks=[]
    for row in rows:
        batch,meta=pack(processor,row);batches.append(batch);checks.append(dict(row_id=row['id'],**meta))
    order=schedule(rows,p['optimizer_steps'],p['seed']);root=Path(out)
    if root.exists():raise ValueError('fresh decision coverage training required')
    recipe=dict(**{k:v for k,v in p.items() if k!='source_sha256'},candidate=candidate,data_manifest_sha256=anchor,
        device=device,gpu=torch.cuda.get_device_name(device),torch=torch.__version__,transformers=transformers.__version__,
        dtype='bfloat16',schedule=order,native_checks=checks,base='fresh pinned model; no previous adapter',
        checkpoint_selection=False,source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    write_json(root/'recipe.json',recipe)
    try:
        model=Qwen3_5ForConditionalGeneration.from_pretrained(target['local_checkpoint'],local_files_only=True,trust_remote_code=False,
            use_safetensors=True,dtype=torch.bfloat16,attn_implementation='sdpa').to(device).eval()
        attach_language_lora(model,LoraSpec(**p['lora']))
        for param in model.parameters():
            if param.requires_grad:param.data=param.data.float()
        frozen_before=frozen_hash(model);params=[param for param in model.parameters() if param.requires_grad]
        optimizer=torch.optim.AdamW(params,lr=p['learning_rate'],weight_decay=0.);start=time.monotonic()
        for step,indices in enumerate(order,1):
            optimizer.zero_grad(set_to_none=True);losses=[]
            for index in indices:
                with torch.autocast('cuda',dtype=torch.bfloat16):
                    loss,_=supervised_forward(model,{k:v.to(device) for k,v in batches[index].items()})
                (loss/4).backward();losses.append(float(loss.detach()))
            norm=torch.nn.utils.clip_grad_norm_(params,1.,error_if_nonfinite=True);optimizer.step()
            record=dict(step=step,indices=indices,mean_loss=sum(losses)/4,gradient_norm=float(norm),elapsed_seconds=time.monotonic()-start)
            with (root/'training.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
            if step%20==0:print(json.dumps(record),flush=True)
        optimizer.zero_grad(set_to_none=True);frozen_after=frozen_hash(model)
        if frozen_before!=frozen_after:raise ValueError('frozen weights changed')
        save_file(adapter_state(model),str(root/'adapter.safetensors'))
        restore_adapters(model,load_file(str(root/'adapter.safetensors')))
        result=evaluate(model,processor,cases,root,device,'sft')
        summary=dict(schema='power-grid-env.workflow-decision-sft/v1',complete=True,candidate=candidate,optimizer_steps=len(order),
            counts=result['counts'],data_manifest_sha256=anchor,frozen_before=frozen_before,frozen_after=frozen_after,
            peak_cuda_memory_bytes=torch.cuda.max_memory_allocated(device),training_seconds=time.monotonic()-start,
            test_evaluated=False,inference_release=False,scope=p['scope'],evaluation_scope=p['evaluation_scope'])
        write_json(root/'summary.json',summary);seal(root);return summary
    except BaseException as exc:
        write_json(root/'failure.json',dict(complete=False,error=type(exc).__name__,detail=str(exc)[:1000]));seal(root);raise


if __name__=='__main__':
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='command',required=True)
    a=s.add_parser('build');a.add_argument('--out',required=True)
    a=s.add_parser('train')
    for key in ('data','manifest-sha256','candidate','out','device'):a.add_argument('--'+key,required=True)
    a=p.parse_args();print(json.dumps(build(a.out) if a.command=='build' else train(a.data,a.manifest_sha256,a.candidate,a.out,a.device)))
