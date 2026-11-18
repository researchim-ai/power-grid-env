"""Finite local actual-student export -> all-target preflight -> mixture plan.

No optimizer, external services, partial-rollout admission or dev/test labels.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_learning_campaign import wait_sealed
from benchmarks.engineering_student_recovery import export
from benchmarks.engineering_student_preflight import qualify
from benchmarks.engineering_learning_mix import plan
from power_grid_env.study_artifacts import verify,seal


def finish(train,train_manifest,suite,suite_manifest,student_eval,train_preflight,train_preflight_manifest,target,target_sha,out,wait_seconds=7200):
    if type(wait_seconds) is not int or not 1<=wait_seconds<=14400:raise ValueError('finite wait1..14400 seconds required')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);phase='wait_for_complete_student_evaluation'
    job=dict(schema='power-grid-env.student-data-campaign/v1',train=str(train),train_manifest_sha256=train_manifest,suite=str(suite),suite_manifest_sha256=suite_manifest,
        student_evaluation=str(student_eval),target_sha256=target_sha,wait_seconds=wait_seconds,source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),optimizer_run=False)
    write_json(root/'job.json',job)
    def progress(value):
        write_json(root/'state.json',dict(phase=value));print(json.dumps(dict(phase=value)),flush=True)
    try:
        progress(phase);student_manifest=wait_sealed(student_eval,time.monotonic()+wait_seconds)
        phase='export_all_visited_prefixes';progress(phase)
        result=export(train,train_manifest,suite,suite_manifest,student_eval,student_manifest,root/'corrections')
        corpus_anchor=verify(root/'corrections');phase='native_all_target_preflight';progress(phase)
        p=qualify(root/'corrections',corpus_anchor,train,train_manifest,suite,suite_manifest,student_eval,student_manifest,target,target_sha,root/'preflight')
        preflight_anchor=verify(root/'preflight');mixture_anchor=None
        if p['passed']:
            phase='admitted_mixture_plan';progress(phase)
            spec=dict(train=str(train),train_manifest=train_manifest,suite=str(suite),suite_manifest=suite_manifest,student_eval=str(student_eval),student_eval_manifest=student_manifest,
                corrections=str(root/'corrections'),corrections_manifest=corpus_anchor,train_preflight=str(train_preflight),train_preflight_manifest=train_preflight_manifest,
                student_preflight=str(root/'preflight'),student_preflight_manifest=preflight_anchor,target_sha256=target_sha)
            write_json(root/'mixture-sources.json',spec);sha=hashlib.sha256((root/'mixture-sources.json').read_bytes()).hexdigest()
            plan(root/'mixture-sources.json',sha,root/'mixture-plan',steps=128);mixture_anchor=verify(root/'mixture-plan')
        phase='complete' if p['passed'] else 'preflight_rejected';progress(phase)
        report=dict(**job,complete=True,admission_passed=p['passed'],student_evaluation_manifest_sha256=student_manifest,correction_manifest_sha256=corpus_anchor,
            preflight_manifest_sha256=preflight_anchor,mixture_manifest_sha256=mixture_anchor,tasks=result['tasks'],source_turns=result['source_turns'],
            visited_prefixes=result['visited_prefixes'],successful_corrections=result['successful_corrections'],expert_targets=result['expert_targets'],checked_targets=p['checked_targets'],
            preflight_failures=len(p['failures']),learned_recovery_claim=False,scope='Actual model train-state correction data admission only;no new model update or closed-loop quality score')
        write_json(root/'summary.json',report);seal(root);return report
    except Exception as exc:
        write_json(root/'failure.json',dict(**job,complete=False,failed_phase=phase,error=type(exc).__name__,detail=str(exc)[:1500],admission_passed=False));seal(root);raise


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for flag in ('train','train-manifest-sha256','suite','suite-manifest-sha256','student-evaluation','train-preflight','train-preflight-manifest-sha256','target','target-sha256','out'):p.add_argument('--'+flag,required=True)
    p.add_argument('--wait-seconds',type=int,default=7200)
    a=p.parse_args();print(json.dumps(finish(a.train,a.train_manifest_sha256,a.suite,a.suite_manifest_sha256,a.student_evaluation,a.train_preflight,a.train_preflight_manifest_sha256,a.target,a.target_sha256,a.out,a.wait_seconds)))
