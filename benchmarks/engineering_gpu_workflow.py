"""Local GPU experiment: matched public observations, full-action TRAIN SFT.

Fixed schedule, no development checkpoint selection, no test labels. Scores
come from actual tool episodes and independently checked physical studies.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import time

from benchmarks.engineering_assisted_workflow import control
from benchmarks.engineering_compact_workflow import run
from benchmarks.engineering_cpu_workflow import load
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.engineering_workflow_sft import pack
from power_grid_env.study_artifacts import read_json, seal, verify


PLAN='reports/engineering-compact-cpu-plan-2026-11-19'
ANCHOR='44d0d80131afe1e47058792374cdf7980baa8b0786b5431824b31a7a77867cd5'


def training_rows(plan):
    """Rebuild positive compact traces from sealed TRAIN cases only."""
    rows=[]
    for item in read_json(Path(plan['parent_plan'])/'train-episodes.json'):
        case=item['case']
        if case['split']!='train':raise ValueError('heldout training case forbidden')
        trace=run(case,control)
        if not trace['success']:raise ValueError('positive compact control must replay')
        for turn,event in enumerate(trace['events']):
            row=dict(split='train',family_sha256=case['family_sha256'],base_project_id=case['base_project_id'],
                     task_id=case['id'],turn=turn,messages=event['query']['messages'],answer=event['action'])
            row['id']=sha(row);rows.append(row)
    return rows


def schedule(rows,steps,seed):
    """Equal tool-class mass per update, cyclic shuffled coverage of each class."""
    if type(steps) is not int or not 1<=steps<=512:raise ValueError('bounded optimizer schedule required')
    groups=defaultdict(list)
    for i,row in enumerate(rows):
        if row['split']!='train':raise ValueError('heldout schedule forbidden')
        groups[row['answer']['name']].append(i)
    expected={'simulate','preview_line_catalog','set_line_catalog','submit'}
    if set(groups)!=expected:raise ValueError('all four workflow action classes required')
    rng=random.Random(seed);queues={};result=[]
    for _ in range(steps):
        batch=[]
        for name in sorted(groups):
            if not queues.get(name):
                queues[name]=groups[name].copy();rng.shuffle(queues[name])
            batch.append(queues[name].pop())
        rng.shuffle(batch);result.append(batch)
    return result


def frozen_hash(model):
    """BF16-safe hash without materializing the full model on the host."""
    import torch
    h=hashlib.sha256()
    for name,p in model.named_parameters():
        if p.requires_grad:continue
        h.update(sha(dict(name=name,shape=list(p.shape),dtype=str(p.dtype))).encode())
        raw=p.detach().contiguous().view(-1)
        for offset in range(0,raw.numel(),1_000_000):
            h.update(raw[offset:offset+1_000_000].cpu().view(torch.uint8).numpy().tobytes())
    return h.hexdigest()


def evaluate(model,processor,cases,root,device,phase):
    import torch
    episodes=[];calls=[]
    def actor(query):
        start=time.monotonic()
        inputs=processor.apply_chat_template(query['messages'],tokenize=True,return_dict=True,return_tensors='pt',
            add_generation_prompt=True,enable_thinking=False).to(device)
        n=inputs['input_ids'].shape[1]
        if n+128>8192:raise ValueError('native context cap exceeded')
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):
            output=model.generate(**inputs,do_sample=False,num_beams=1,use_cache=True,logits_to_keep=1,
                max_new_tokens=128,pad_token_id=processor.tokenizer.pad_token_id,eos_token_id=processor.tokenizer.eos_token_id)
        raw=processor.tokenizer.decode(output[0,n:],skip_special_tokens=True)
        record=dict(query_sha256=sha(query),completion=raw,prompt_tokens=n,generated_tokens=output.shape[1]-n,seconds=time.monotonic()-start)
        calls.append(record)
        with (root/(phase+'-calls.jsonl')).open('a') as f:f.write(json.dumps(record)+'\n')
        return raw
    model.eval()
    for case in cases:
        result=run(case,actor);episodes.append(result)
        write_json(root/(phase+'-partial.json'),dict(complete=False,episodes=episodes))
        print(json.dumps(dict(phase=phase,case=case['id'],success=result['success'],calls=result['calls'])),flush=True)
    result=dict(complete=True,episodes=episodes,model_calls=calls,
        counts={split:dict(successes=sum(r['success'] for r in episodes if r['split']==split),total=sum(r['split']==split for r in episodes)) for split in ('train','dev')})
    write_json(root/(phase+'.json'),result);return result


def experiment(candidate,out,device='cuda:1',steps=96,seed=20261119):
    import torch,transformers
    from transformers import Qwen3_5ForConditionalGeneration
    from safetensors.torch import save_file,load_file
    from benchmarks.engineering_trainer_v2 import attach_language_lora,LoraSpec
    from power_grid_env.qwen35_training import local_target,supervised_forward,adapter_state,restore_adapters
    from power_grid_env.vlm_batches import local_qwen35_processor
    root=Path(out)
    if root.exists():raise ValueError('fresh experiment directory required')
    p,cases=load(PLAN,ANCHOR)
    if candidate not in p['targets']:raise ValueError('pinned candidate required')
    if not device.startswith('cuda:') or not torch.cuda.is_available():raise ValueError('explicit available CUDA device required')
    spec=p['targets'][candidate];target=local_target(spec['path'],spec['sha256'])
    torch.set_num_threads(4);torch.manual_seed(seed);torch.cuda.manual_seed_all(seed)
    processor=local_qwen35_processor(target['local_checkpoint'],target['config_sha256'])
    rows=training_rows(p);order=schedule(rows,steps,seed) if steps else []
    batches=[];checks=[]
    for row in rows:
        batch,meta=pack(processor,row);batches.append(batch);checks.append(dict(row_id=row['id'],**meta))
    # Freeze complete recipe and dataset inventory before model evaluation/training.
    root.mkdir(parents=True)
    recipe=dict(schema='power-grid-env.gpu-workflow-sft/v1',candidate=candidate,target_sha256=spec['sha256'],
        plan_manifest_sha256=ANCHOR,device=device,gpu=torch.cuda.get_device_name(device),torch=torch.__version__,transformers=transformers.__version__,
        dtype='bfloat16',seed=seed,optimizer_steps=steps,gradient_accumulation=4,learning_rate=1e-4,
        optimizer='AdamW',weight_decay=0.,gradient_clip=1.,lora=dict(rank=16,alpha=32.,last_blocks=8),
        loss='assistant-only mean token CE; equal action-class mass per optimizer update',schedule=order,
        dataset_sha256=sha(rows),native_checks=checks,records=len(rows),bases=len({r['base_project_id'] for r in rows}),
        source_sha256={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in
            ('engineering_gpu_workflow.py','engineering_workflow_sft.py','engineering_trainer_v2.py')},
        test_evaluated=False,visual_input=False,dev_use='protocol development only; no checkpoint selection',
        scope='Synthetic balanced 400V constant-Z radial networks; compact fixed-structure catalogue workflow. Not general engineering qualification.')
    write_json(root/'recipe.json',recipe)
    with (root/'train.jsonl').open('w') as f:
        for row in rows:f.write(json.dumps(row)+'\n')
    try:
        model=Qwen3_5ForConditionalGeneration.from_pretrained(target['local_checkpoint'],local_files_only=True,trust_remote_code=False,
            use_safetensors=True,dtype=torch.bfloat16,attn_implementation='sdpa').to(device).eval()
        model.requires_grad_(False)
        before=evaluate(model,processor,cases,root,device,'base')
        if steps:
            names=attach_language_lora(model,LoraSpec(last_blocks=8))
            for param in model.parameters():
                if param.requires_grad:param.data=param.data.float()
            frozen_before=frozen_hash(model)
            params=[param for param in model.parameters() if param.requires_grad]
            optimizer=torch.optim.AdamW(params,lr=1e-4,weight_decay=0.)
            history=[];start=time.monotonic()
            for step,indices in enumerate(order,1):
                optimizer.zero_grad(set_to_none=True);losses=[]
                for index in indices:
                    batch={k:v.to(device) for k,v in batches[index].items()}
                    with torch.autocast('cuda',dtype=torch.bfloat16):loss,_=supervised_forward(model,batch)
                    (loss/4).backward();losses.append(float(loss.detach()))
                norm=torch.nn.utils.clip_grad_norm_(params,1.,error_if_nonfinite=True);optimizer.step()
                row=dict(step=step,indices=indices,mean_loss=sum(losses)/4,gradient_norm=float(norm),elapsed_seconds=time.monotonic()-start)
                history.append(row)
                with (root/'training.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
                if step%8==0:print(json.dumps(row),flush=True)
            optimizer.zero_grad(set_to_none=True)
            frozen_after=frozen_hash(model)
            if frozen_after!=frozen_before:raise ValueError('frozen model tensors changed')
            state=adapter_state(model);save_file(state,str(root/'adapter.safetensors'))
            restore_adapters(model,load_file(str(root/'adapter.safetensors')))
            if any(not torch.equal(v,adapter_state(model)[k]) for k,v in state.items()):raise ValueError('adapter roundtrip differs')
            write_json(root/'training-summary.json',dict(completed_steps=len(history),trainable_parameters=sum(x.numel() for x in params),
                lora_modules=names,frozen_before=frozen_before,frozen_after=frozen_after,adapter_roundtrip_exact=True,
                maximum_cuda_memory_bytes=torch.cuda.max_memory_allocated(device),seconds=time.monotonic()-start))
            after=evaluate(model,processor,cases,root,device,'sft')
        else:after=None
        result=dict(schema=recipe['schema'],complete=True,candidate=candidate,base=before['counts'],sft=after['counts'] if after else None,
            optimizer_run=bool(steps),optimizer_steps=steps,recipe_sha256=sha(recipe),test_evaluated=False,
            scope=recipe['scope'],format_repair=False,scripted_fallback=False)
        write_json(root/'summary.json',result);seal(root);return result
    except BaseException as exc:
        write_json(root/'failure.json',dict(complete=False,error=type(exc).__name__,detail=str(exc)[:1500],model_quality_score_available=False))
        seal(root);raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--candidate',required=True);p.add_argument('--out',required=True)
    p.add_argument('--device',default='cuda:1');p.add_argument('--steps',type=int,default=96)
    a=p.parse_args();print(json.dumps(experiment(a.candidate,a.out,a.device,a.steps)))
