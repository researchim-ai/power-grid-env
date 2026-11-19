"""Explicit CPU-BF16 inference profile on compact engineering tool tasks.

Separate from CPU-FP32 strict-card scores. Native IDs/timings stream per call.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

from benchmarks.engineering_assisted_workflow import control
from benchmarks.engineering_compact_workflow import run,CompactEpisode,request
from benchmarks.engineering_workflow_campaign import load_plan
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_sft_release_v2 import sha
from power_grid_env.study_artifacts import read_json,verify,seal


FILES=('engineering_cpu_workflow.py','engineering_compact_workflow.py','engineering_assisted_workflow.py','engineering_workflow_campaign.py')
def sources():return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in FILES}


def plan(parent,anchor,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh compact CPU plan required')
    p,cases=load_plan(parent,anchor)
    controls=[run(c,control) for c in cases]
    if not all(r['success'] for r in controls):raise ValueError('same public-tool control must complete every case')
    root.mkdir(parents=True);write_json(root/'controls.json',controls)
    result=dict(schema='power-grid-env.compact-cpu-workflow-plan/v1',parent_plan=str(Path(parent).resolve()),parent_manifest_sha256=anchor,
                targets=p['targets'],case_ids=p['case_ids'],source_sha256=sources(),dtype='bfloat16',threads=12,
                maximum_actions=10,maximum_completion_tokens=128,thinking=False,optimizer_run=False,
                scope='New compact optional-empty API/CPU-BF16 development profile, not comparable to strict FP32 scores. '
                      'Cases frozen before model runs; old0.8B dev syntax failure informed protocol development, so dev is not a final unbiased test. Test closed.')
    write_json(root/'summary.json',result);seal(root);return result


def load(root,anchor):
    if verify(root)!=anchor:raise ValueError('compact plan manifest differs')
    p=read_json(Path(root)/'summary.json')
    if p.get('schema')!='power-grid-env.compact-cpu-workflow-plan/v1' or p.get('source_sha256')!=sources():raise ValueError('compact source/profile differs')
    _,cases=load_plan(p['parent_plan'],p['parent_manifest_sha256'])
    if [c['id'] for c in cases]!=p['case_ids']:raise ValueError('compact case inventory differs')
    controls=read_json(Path(root)/'controls.json')
    if len(controls)!=len(cases) or any(sha(run(c,control))!=sha(r) for c,r in zip(cases,controls)):
        raise ValueError('compact numerical controls do not replay')
    return p,cases


def benchmark(plan_root,anchor,candidate,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh compact evaluation required')
    p,cases=load(plan_root,anchor)
    if candidate not in p['targets']:raise ValueError('predeclared compact candidate required')
    if 'avx512_bf16' not in Path('/proc/cpuinfo').read_text():raise ValueError('qualified CPU BF16 hardware flag absent')
    import torch,transformers
    from transformers import Qwen3_5ForConditionalGeneration
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    spec=p['targets'][candidate];target=local_target(spec['path'],spec['sha256'])
    torch.set_num_threads(p['threads'])
    processor=local_qwen35_processor(target['local_checkpoint'],target['config_sha256'])
    model=Qwen3_5ForConditionalGeneration.from_pretrained(target['local_checkpoint'],local_files_only=True,trust_remote_code=False,
        use_safetensors=True,dtype=torch.bfloat16,attn_implementation='sdpa').eval()
    model.requires_grad_(False)
    root.mkdir(parents=True);calls=[];episodes=[]
    def actor(query):
        start=time.monotonic()
        inputs=processor.apply_chat_template(query['messages'],tokenize=True,return_dict=True,return_tensors='pt',
            add_generation_prompt=True,enable_thinking=False)
        n=inputs['input_ids'].shape[1]
        if n+query['maximum_completion_tokens']>8192:raise ValueError('compact context cap exceeded')
        with torch.inference_mode():
            ids=model.generate(**inputs,do_sample=False,num_beams=1,use_cache=True,logits_to_keep=1,
                max_new_tokens=query['maximum_completion_tokens'],pad_token_id=processor.tokenizer.pad_token_id,
                eos_token_id=processor.tokenizer.eos_token_id)
        raw=processor.tokenizer.decode(ids[0,n:],skip_special_tokens=True)
        record=dict(query_sha256=sha(query),completion=raw,prompt_tokens=n,generated_tokens=ids.shape[1]-n,seconds=time.monotonic()-start)
        calls.append(record)
        with (root/'model-calls.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
        print(json.dumps(dict(candidate=candidate,call=len(calls),prompt_tokens=n,generated_tokens=record['generated_tokens'],seconds=record['seconds'])),flush=True)
        return raw
    try:
        # Real native prefill qualification before reporting episode failures as
        # model behavior. Backend/kernel errors abort the evaluation separately.
        q=request(CompactEpisode(cases[0]).public())
        inputs=processor.apply_chat_template(q['messages'],tokenize=True,return_dict=True,return_tensors='pt',add_generation_prompt=True,enable_thinking=False)
        with torch.inference_mode():smoke=model(**inputs,use_cache=False,logits_to_keep=1).logits
        if not bool(torch.isfinite(smoke).all()):raise ValueError('nonfinite native BF16 prefill')
        for case in cases:
            result=run(case,actor);episodes.append(result)
            write_json(root/'partial.json',dict(complete=False,candidate=candidate,episodes=episodes))
            print(json.dumps(dict(candidate=candidate,case=case['id'],split=case['split'],success=result['success'],calls=result['calls'])),flush=True)
        counts={s:dict(successes=sum(r['success'] for r in episodes if r['split']==s),total=sum(r['split']==s for r in episodes)) for s in ('train','dev')}
        by_kind={kind:dict(successes=sum(r['success'] for r in episodes if r['task_id'].startswith(prefix)),
                           total=sum(r['task_id'].startswith(prefix) for r in episodes)) for kind,prefix in (('healthy','healthy-'),('tradeoff_repair','tradeoff-'))}
        result=dict(schema='power-grid-env.compact-cpu-workflow-evaluation/v1',complete=True,candidate=candidate,plan_manifest_sha256=anchor,
                    target_sha256=spec['sha256'],episodes=episodes,model_calls=calls,counts=counts,by_kind=by_kind,
                    dtype='bfloat16',threads=p['threads'],torch=torch.__version__,transformers=transformers.__version__,
                    native_bf16_prefill_finite=True,source_sha256=sources(),optimizer_run=False,test_evaluated=False,
                    scope='Actual compact solver-assisted model episodes; required choices not repaired. API allows explicit empty-argument defaults. '
                          'BF16 development results are separate from strict FP32; four cases do not establish broad superiority.')
        write_json(root/'summary.json',result);seal(root);return {k:v for k,v in result.items() if k not in ('episodes','model_calls')}
    except Exception as exc:
        write_json(root/'failure.json',dict(complete=False,candidate=candidate,error=type(exc).__name__,detail=str(exc)[:1000],model_quality_score_available=False))
        seal(root);raise


if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('plan')
    for key in ('parent-plan','manifest-sha256','out'):a.add_argument('--'+key,required=True)
    a=sub.add_parser('run')
    for key in ('plan','manifest-sha256','candidate','out'):a.add_argument('--'+key,required=True)
    a=p.parse_args();print(json.dumps(plan(a.parent_plan,a.manifest_sha256,a.out) if a.command=='plan' else benchmark(a.plan,a.manifest_sha256,a.candidate,a.out)))
