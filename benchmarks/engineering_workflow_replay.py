"""Replay actual model actions and evaluate decision-coverage adapters.

The disclosed 30-case inventory is a regression suite, not a fresh test for
later recipes. Replay never substitutes a controller for an invalid action.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from benchmarks.engineering_compact_workflow import run
from benchmarks.engineering_gpu_workflow import evaluate
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_sft_release_v2 import sha
from power_grid_env.study_artifacts import read_json,verify,seal


def replay(case,trace):
    turns=iter(trace['events']);used=[]
    def actor(query):
        event=next(turns)
        if sha(query)!=sha(event['query']):raise ValueError('recorded action did not observe this physical state')
        used.append(event);return event['completion']
    actual=run(case,actor)
    if len(used)!=len(trace['events']) or sha(actual)!=sha(trace):raise ValueError('model trace does not physically replay')
    return actual


def metrics(episodes):
    result={}
    for split in ('train','dev','test'):
        group=[e for e in episodes if e['split']==split]
        result[split]=dict(successes=sum(e['success'] for e in group),total=len(group),
            healthy=dict(successes=sum(e['success'] for e in group if e['task_id'].startswith('healthy-')),
                         total=sum(e['task_id'].startswith('healthy-') for e in group)),
            repair=dict(successes=sum(e['success'] for e in group if e['task_id'].startswith('tradeoff-')),
                        total=sum(e['task_id'].startswith('tradeoff-') for e in group)))
    result['errors']=dict(decode_errors=sum(bool(t['decode_error']) for e in episodes for t in e['events']),
        violations=dict(Counter(v for e in episodes for v in e['terminal'].get('violations',[]))),
        calls=sum(e['calls'] for e in episodes),independent_physics_checks=sum(len(e['physics_checks']) for e in episodes))
    return result


def regression(plan,anchor,training,training_anchor,out,device):
    import torch
    from transformers import Qwen3_5ForConditionalGeneration
    from safetensors.torch import load_file
    from benchmarks.engineering_trainer_v2 import attach_language_lora,LoraSpec
    from power_grid_env.qwen35_training import local_target,restore_adapters
    from power_grid_env.vlm_batches import local_qwen35_processor
    if verify(plan)!=anchor or verify(training)!=training_anchor:raise ValueError('input manifest differs')
    p=read_json(Path(plan)/'summary.json');s=read_json(Path(training)/'summary.json');r=read_json(Path(training)/'recipe.json')
    if not s['complete'] or s['schema']!='power-grid-env.workflow-decision-sft/v1' or s['optimizer_steps']!=240:
        raise ValueError('fixed final decision-coverage checkpoint required')
    candidate=s['candidate'];spec=p['targets'][candidate]
    if r['targets'][candidate]!=spec or r['checkpoint_selection'] is not False:raise ValueError('checkpoint identity differs')
    target=local_target(spec['path'],spec['sha256']);torch.set_num_threads(4)
    processor=local_qwen35_processor(target['local_checkpoint'],target['config_sha256'])
    model=Qwen3_5ForConditionalGeneration.from_pretrained(target['local_checkpoint'],local_files_only=True,trust_remote_code=False,
        use_safetensors=True,dtype=torch.bfloat16,attn_implementation='sdpa').to(device).eval()
    attach_language_lora(model,LoraSpec(**r['lora']))
    for param in model.parameters():
        if param.requires_grad:param.data=param.data.float()
    restore_adapters(model,load_file(str(Path(training)/'adapter.safetensors')))
    root=Path(out)
    if root.exists():raise ValueError('fresh regression report required')
    root.mkdir(parents=True)
    inputs=dict(plan_manifest_sha256=anchor,training_manifest_sha256=training_anchor,device=device,
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),previously_disclosed_test=True)
    write_json(root/'inputs.json',inputs)
    try:
        cases=read_json(Path(plan)/'cases.json');result=evaluate(model,processor,cases,root,device,'sft')
        for case,trace in zip(cases,result['episodes']):replay(case,trace)
        summary=dict(schema='power-grid-env.workflow-decision-regression/v1',complete=True,candidate=candidate,
            metrics=metrics(result['episodes']),all_actions_replayed=True,optimizer_run=False,
            unbiased_test=False,inference_release=False,format_repair=False,scripted_fallback=False,
            scope='Previously disclosed synthetic 30-case regression suite. No new heldout qualification.')
        write_json(root/'summary.json',summary);seal(root);return summary
    except BaseException as exc:
        write_json(root/'failure.json',dict(complete=False,error=type(exc).__name__,detail=str(exc)[:1000]));seal(root);raise


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('plan','manifest-sha256','training','training-sha256','out','device'):p.add_argument('--'+key,required=True)
    a=p.parse_args();print(json.dumps(regression(a.plan,a.manifest_sha256,a.training,a.training_sha256,a.out,a.device)))
