"""Finish an explicitly started local tiny run, then evaluate and audit it.

No scheduler, network, external account, test selection or implicit new training.
A finite watcher pins the existing recipe before waiting for sealed completion.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_learning_gate import strict_contract,load_suite,epoch_schedule
from benchmarks.engineering_learning_compare import compare
from benchmarks.engineering_tiny_run import run_eval
from power_grid_env.study_artifacts import verify,read_json,seal


def wait_sealed(root,deadline,interval=5):
    root=Path(root)
    while True:
        if (root/'manifest.json').exists():return verify(root)
        if time.monotonic()>=deadline:raise TimeoutError('local job did not publish a sealed result: '+str(root))
        time.sleep(min(interval,max(0,deadline-time.monotonic())))


def validate_training(root,recipe_sha,suite,train_manifest,suite_manifest,target_sha):
    verify(root)
    recipe=read_json(Path(root)/'recipe.json')
    if hashlib.sha256((Path(root)/'recipe.json').read_bytes()).hexdigest()!=recipe_sha:raise ValueError('started training recipe changed')
    if (recipe.get('schema')!='power-grid-env.tiny-train-run/v1' or recipe.get('train_manifest_sha256')!=train_manifest or
        recipe.get('suite_manifest_sha256')!=suite_manifest or recipe.get('target_sha256')!=target_sha or
        recipe.get('contract')!=strict_contract().model_dump() or recipe.get('suite_task_ids')!=suite['task_ids'] or recipe.get('target_indices')!=suite['target_indices']):
        raise ValueError('training run source/suite differs')
    checkpoint=Path(root)/'checkpoint';anchor=verify(checkpoint)
    from benchmarks.engineering_v2_adapter import receipt
    r=receipt(checkpoint,anchor,target_sha,strict_contract())
    expected=epoch_schedule(suite,recipe['epochs'],recipe['seed'])
    if r['recipe']['schedule']!=expected or r['recipe']['steps']!=len(expected) or r['recipe']['lora']!=recipe['lora']:
        raise ValueError('checkpoint did not train the complete selected schedule')
    outer=read_json(Path(root)/'summary.json')
    if outer.get('checkpoint_manifest_sha256')!=anchor or outer.get('completed_steps')!=len(expected) or outer.get('schedule_complete') is not True:
        raise ValueError('training run is incomplete')
    return checkpoint,anchor


def finish(train_run,recipe_sha,base_eval,train,train_manifest,suite,suite_manifest,out,python,target,target_sha,wait_seconds=10800):
    if type(wait_seconds) is not int or not 1<=wait_seconds<=14400:raise ValueError('finite wait1..14400 seconds required')
    if hashlib.sha256((Path(train_run)/'recipe.json').read_bytes()).hexdigest()!=recipe_sha:raise ValueError('owner started recipe differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    job=dict(schema='power-grid-env.tiny-learning-campaign/v1',train_run=str(train_run),started_recipe_sha256=recipe_sha,base_eval=str(base_eval),
        train_manifest_sha256=train_manifest,suite_manifest_sha256=suite_manifest,target_sha256=target_sha,wait_seconds=wait_seconds,
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),generalization_claim=False)
    write_json(root/'job.json',job);phase='wait_for_training';tick=time.monotonic()
    def progress(value):
        write_json(root/'state.json',dict(phase=value,elapsed_s=time.monotonic()-tick))
        print(json.dumps(dict(phase=value)),flush=True)
    try:
        deadline=time.monotonic()+wait_seconds;progress(phase)
        training_anchor=wait_sealed(train_run,deadline)
        data=ProceduralDataset(train,train_manifest,strict_contract());selected=load_suite(data,suite,suite_manifest)
        checkpoint,checkpoint_anchor=validate_training(train_run,recipe_sha,selected,train_manifest,suite_manifest,target_sha)
        phase='wait_for_base';progress(phase);base_anchor=wait_sealed(base_eval,deadline)
        phase='standalone_adapter_evaluation';progress(phase)
        evaluation=run_eval(python,target,target_sha,train,train_manifest,root/'adapter-evaluation',suite,suite_manifest,'train',str(checkpoint),checkpoint_anchor)
        phase='paired_physical_replay';progress(phase)
        cases=[data.scenarios[t] for t in selected['task_ids']]
        paired=compare(base_eval,base_anchor,root/'adapter-evaluation',verify(root/'adapter-evaluation'),cases,strict_contract(),root/'paired')
        phase='complete';progress(phase)
        result=dict(**job,complete=True,training_manifest_sha256=training_anchor,checkpoint_manifest_sha256=checkpoint_anchor,
            base_manifest_sha256=base_anchor,adapter_evaluation_manifest_sha256=verify(root/'adapter-evaluation'),paired_manifest_sha256=verify(root/'paired'),
            tasks=paired['tasks'],base_successes=paired['base_successes'],adapter_successes=paired['adapter_successes'],
            tiny_learning_gate_passed=paired['tiny_learning_gate_passed'],next_gate='matched_dev' if paired['tiny_learning_gate_passed'] else 'inspect_train_rollouts_and_revise_recipe',
            scope='One tiny train memorization experiment;no autonomous industrial or heldout qualification')
        write_json(root/'summary.json',result);seal(root);return result
    except Exception as exc:
        result=dict(**job,complete=False,failed_phase=phase,error=type(exc).__name__,detail=str(exc)[:1500],tiny_learning_gate_passed=False)
        write_json(root/'failure.json',result);seal(root);raise


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for flag in ('train-run','recipe-sha256','base-eval','train','manifest-sha256','suite','suite-manifest-sha256','out'):p.add_argument('--'+flag,required=True)
    p.add_argument('--python',default='.venv-qwen35-08b/bin/python');p.add_argument('--target',default='requirements/qwen35-08b-local-target.json')
    p.add_argument('--target-sha256',default='e763c82b7f4f1d5acea273b43674e45bcdb949fc91d000cb7537ea54b5fa0262');p.add_argument('--wait-seconds',type=int,default=10800)
    a=p.parse_args();print(json.dumps(finish(a.train_run,a.recipe_sha256,a.base_eval,a.train,a.manifest_sha256,a.suite,a.suite_manifest_sha256,a.out,a.python,a.target,a.target_sha256,a.wait_seconds)))
