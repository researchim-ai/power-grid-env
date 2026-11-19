"""One-shot fixed-checkpoint evaluation on the complete frozen family inventory.

No optimizer, checkpoint choice or development-based filtering. Test outcomes
are consumed only after the final 96-update checkpoints have been sealed.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from benchmarks.engineering_assisted_workflow import control
from benchmarks.engineering_compact_workflow import run
from benchmarks.engineering_cpu_workflow import load
from benchmarks.engineering_gpu_workflow import PLAN,ANCHOR,evaluate
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_workflow_campaign import tradeoff_case,heuristic
from power_grid_env.study_artifacts import read_json,verify,seal


def prepare(out):
    p,_=load(PLAN,ANCHOR)
    parent=read_json(Path(p['parent_plan'])/'summary.json')
    if verify(parent['source'])!=parent['source_manifest_sha256']:raise ValueError('physical corpus differs')
    cases=[];controls=[]
    for line in (Path(parent['source'])/'cases.jsonl').read_text().splitlines():
        base=json.loads(line)
        healthy=copy.deepcopy(base);healthy.update(id='healthy-'+base['id'],project=copy.deepcopy(base['healthy_project']))
        repair,_=tradeoff_case(base)
        for case in (healthy,repair):
            proof=run(case,control)
            if not proof['success']:raise ValueError('all declared controls must complete')
            negative={rank:run(case,lambda q:heuristic(q,rank))['success'] for rank in ('max_rating','min_resistance')}
            if case is repair and any(negative.values()):raise ValueError('repair shortcut control unexpectedly passed')
            cases.append(case);controls.append(dict(case_id=case['id'],success=True,negative=negative))
    # Keep numerical cases and hard catalogue alternatives unfiltered.
    result=dict(schema='power-grid-env.workflow-holdout-plan/v1',parent_plan=PLAN,parent_manifest_sha256=ANCHOR,
        corpus_manifest_sha256=parent['source_manifest_sha256'],targets=p['targets'],
        cases=len(cases),splits={s:sum(c['split']==s for c in cases) for s in ('train','dev','test')},
        families={s:len({c['family_sha256'] for c in cases if c['split']==s}) for s in ('train','dev','test')},
        bases={s:len({c['base_project_id'] for c in cases if c['split']==s}) for s in ('train','dev','test')},
        checkpoint_rule='Exactly the final 96-update adapter of each predeclared GPU experiment; no checkpoint selection.',
        scope='Test topology families excluded from SFT; synthetic bounded 400V radial constant-Z catalogue tasks only.',
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    root=Path(out)
    if root.exists():raise ValueError('fresh holdout plan required')
    write_json(root/'summary.json',result);write_json(root/'cases.json',cases);write_json(root/'controls.json',controls)
    seal(root);return result


def benchmark(plan,anchor,training,training_anchor,out,device):
    import torch,transformers
    from transformers import Qwen3_5ForConditionalGeneration
    from safetensors.torch import load_file
    from benchmarks.engineering_trainer_v2 import LoraSpec,attach_language_lora
    from power_grid_env.qwen35_training import local_target,restore_adapters
    from power_grid_env.vlm_batches import local_qwen35_processor
    if verify(plan)!=anchor or verify(training)!=training_anchor:raise ValueError('sealed input manifest differs')
    p=read_json(Path(plan)/'summary.json');r=read_json(Path(training)/'summary.json');recipe=read_json(Path(training)/'recipe.json')
    if p['source_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('holdout code differs')
    if not r['complete'] or r['optimizer_steps']!=96 or not r['optimizer_run']:raise ValueError('fixed completed checkpoint required')
    spec=p['targets'][r['candidate']]
    if recipe['target_sha256']!=spec['sha256'] or recipe['plan_manifest_sha256']!=p['parent_manifest_sha256']:
        raise ValueError('checkpoint target or physical ancestry differs')
    target=local_target(spec['path'],spec['sha256']);torch.set_num_threads(4)
    processor=local_qwen35_processor(target['local_checkpoint'],target['config_sha256'])
    model=Qwen3_5ForConditionalGeneration.from_pretrained(target['local_checkpoint'],local_files_only=True,trust_remote_code=False,
        use_safetensors=True,dtype=torch.bfloat16,attn_implementation='sdpa').to(device).eval();model.requires_grad_(False)
    cases=read_json(Path(plan)/'cases.json');root=Path(out)
    if root.exists():raise ValueError('fresh heldout evaluation required')
    root.mkdir(parents=True)
    write_json(root/'inputs.json',dict(plan_manifest_sha256=anchor,training_manifest_sha256=training_anchor,
        candidate=r['candidate'],device=device,gpu=torch.cuda.get_device_name(device),torch=torch.__version__,transformers=transformers.__version__))
    try:
        before=evaluate(model,processor,cases,root,device,'base')
        attach_language_lora(model,LoraSpec(last_blocks=8))
        for param in model.parameters():
            if param.requires_grad:param.data=param.data.float()
        restore_adapters(model,load_file(str(Path(training)/'adapter.safetensors')))
        after=evaluate(model,processor,cases,root,device,'sft')
        def metrics(result):
            return {split:dict(successes=sum(e['success'] for e in result['episodes'] if e['split']==split),
                total=sum(e['split']==split for e in result['episodes'])) for split in ('train','dev','test')}
        result=dict(schema='power-grid-env.workflow-holdout-evaluation/v1',complete=True,candidate=r['candidate'],
            base=metrics(before),sft=metrics(after),optimizer_run=False,test_evaluated=True,checkpoints_compared=1,
            scope=p['scope'],format_repair=False,scripted_fallback=False)
        write_json(root/'summary.json',result);seal(root);return result
    except BaseException as exc:
        write_json(root/'failure.json',dict(complete=False,error=type(exc).__name__,detail=str(exc)[:1000]));seal(root);raise


if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('plan');a.add_argument('--out',required=True)
    a=sub.add_parser('run')
    for key in ('plan','manifest-sha256','training','training-sha256','out','device'):a.add_argument('--'+key,required=True)
    a=p.parse_args();print(json.dumps(prepare(a.out) if a.command=='plan' else benchmark(a.plan,a.manifest_sha256,a.training,a.training_sha256,a.out,a.device)))
