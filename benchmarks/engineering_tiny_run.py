"""Owner-pinned tiny-suite training and standalone evaluation CLI.

Deliberately separate stages: no teacher enters the model evaluator, and no
validation trajectories enter training. Every invocation needs a fresh output.
"""
import argparse
import hashlib
import json
from pathlib import Path
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_learning_gate import (strict_contract,load_suite,epoch_schedule,load_evaluation,evaluate_cases,model_command)
from benchmarks.engineering_trainer_v2 import train
from benchmarks.engineering_rollout import ProcessPolicy
from power_grid_env.study_artifacts import verify,seal


def run_train(target,target_sha,corpus,corpus_manifest,preflight,preflight_manifest,suite,suite_manifest,out,epochs=1,last_blocks=2,seed=42,max_new_steps=None,resume=None,resume_manifest=None):
    root=Path(out)
    if root.exists():raise ValueError('fresh learning run required')
    data=ProceduralDataset(corpus,corpus_manifest,strict_contract());selected=load_suite(data,suite,suite_manifest)
    order=epoch_schedule(selected,epochs,seed)
    recipe=dict(schema='power-grid-env.tiny-train-run/v1',suite_manifest_sha256=suite_manifest,train_manifest_sha256=corpus_manifest,
        preflight_manifest_sha256=preflight_manifest,target_sha256=target_sha,epochs=epochs,seed=seed,steps=len(order),
        suite_task_ids=selected['task_ids'],target_indices=selected['target_indices'],contract=strict_contract().model_dump(),
        lora=dict(rank=16,alpha=32.,last_blocks=last_blocks),runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        generalization_claim=False,closed_loop_quality_gate_status='not_run')
    root.mkdir(parents=True,exist_ok=False);write_json(root/'recipe.json',recipe)
    try:
        result=train(target,target_sha,corpus,corpus_manifest,preflight,preflight_manifest,root/'checkpoint',steps=len(order),indices=order,seed=seed,
            protocol='qwen-native-strict-v1',lora=recipe['lora'],context_tokens=24576,max_new_steps=max_new_steps,resume=resume,resume_manifest=resume_manifest)
    except Exception as exc:
        write_json(root/'failure.json',dict(error=type(exc).__name__,detail=str(exc)[:1000],complete=False));seal(root);raise
    write_json(root/'summary.json',dict(**recipe,completed_steps=result['completed_steps'],schedule_complete=result['schedule_complete'],checkpoint_manifest_sha256=verify(root/'checkpoint')))
    seal(root);return result


def run_eval(python,target,target_sha,corpus,corpus_manifest,out,suite=None,suite_manifest=None,split='train',adapter=None,adapter_manifest=None,control=False,timeout=180):
    contract=strict_contract()
    if split=='train':
        if not suite or not suite_manifest:raise ValueError('train evaluation requires a frozen tiny suite')
        data=ProceduralDataset(corpus,corpus_manifest,contract);selected=load_suite(data,suite,suite_manifest)
        cases=[data.scenarios[t] for t in selected['task_ids']]
        source=dict(role='tiny_train',corpus_manifest_sha256=corpus_manifest,suite_manifest_sha256=suite_manifest)
    else:
        if suite or suite_manifest:raise ValueError('heldout evaluation cannot select a tiny train suite')
        cases=load_evaluation(corpus,corpus_manifest,split);source=dict(role=split,corpus_manifest_sha256=corpus_manifest)
    if control:
        if adapter or adapter_manifest:raise ValueError('public control cannot load model adapter')
        from benchmarks.engineering_recovery import RecoveryPolicy
        class Control:
            def __init__(self):self.policy=RecoveryPolicy()
            def __call__(self,messages):return json.dumps(self.policy(messages))
        policy=Control();candidate=dict(kind='public_deterministic_control',neural=False)
    else:
        command=model_command(python,target,target_sha,contract,adapter,adapter_manifest)
        policy=ProcessPolicy(command,timeout=timeout)
        candidate=dict(kind='native_model',target_sha256=target_sha,adapter_manifest_sha256=adapter_manifest,transport=policy.identity,
            adapter_loader_sha256=hashlib.sha256(Path(__file__).with_name('engineering_v2_adapter.py').read_bytes()).hexdigest())
    try:return evaluate_cases(cases,policy,out,contract,source,candidate)
    finally:
        if hasattr(policy,'close'):policy.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['train','evaluate'])
    p.add_argument('--corpus',required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--out',required=True)
    p.add_argument('--suite');p.add_argument('--suite-manifest-sha256');p.add_argument('--split',choices=['train','dev','test'],default='train')
    p.add_argument('--target',default='requirements/qwen35-08b-local-target.json');p.add_argument('--target-sha256',default='e763c82b7f4f1d5acea273b43674e45bcdb949fc91d000cb7537ea54b5fa0262')
    p.add_argument('--python',default='.venv-qwen35-08b/bin/python');p.add_argument('--adapter');p.add_argument('--adapter-manifest-sha256');p.add_argument('--control',action='store_true')
    p.add_argument('--preflight');p.add_argument('--preflight-manifest-sha256');p.add_argument('--epochs',type=int,default=1);p.add_argument('--last-blocks',type=int,default=2)
    p.add_argument('--max-new-steps',type=int);p.add_argument('--resume');p.add_argument('--resume-manifest-sha256');p.add_argument('--timeout',type=int,default=180)
    a=p.parse_args()
    if a.stage=='train':
        if a.split!='train' or not all((a.preflight,a.preflight_manifest_sha256,a.suite,a.suite_manifest_sha256)):p.error('training requires train corpus,preflight and frozen suite anchors')
        if a.control or a.adapter or a.adapter_manifest_sha256:p.error('training uses --resume for optimizer continuation')
        result=run_train(a.target,a.target_sha256,a.corpus,a.manifest_sha256,a.preflight,a.preflight_manifest_sha256,a.suite,a.suite_manifest_sha256,a.out,a.epochs,a.last_blocks,
            max_new_steps=a.max_new_steps,resume=a.resume,resume_manifest=a.resume_manifest_sha256)
    else:
        if a.preflight or a.preflight_manifest_sha256 or a.resume or a.resume_manifest_sha256 or a.max_new_steps:p.error('training arguments are not evaluation arguments')
        result=run_eval(a.python,a.target,a.target_sha256,a.corpus,a.manifest_sha256,a.out,a.suite,a.suite_manifest_sha256,a.split,a.adapter,a.adapter_manifest_sha256,a.control,a.timeout)
    print(json.dumps(result))

if __name__=='__main__':main()
