"""One bounded128-step admitted-mixture phase,checkpoints and standalone eval.

No automatic recipe search,extra epochs,GRPO,heldout tuning or remote services.
The lifecycle pilot and both source admissions must pass before optimization.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
from collections import Counter
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_learning_gate import strict_contract,load_suite
from benchmarks.engineering_learning_campaign import wait_sealed
from benchmarks.engineering_learning_compare import audit
from benchmarks.engineering_action_quality import quality
from benchmarks.engineering_trainer_v3 import train,load_plan
from benchmarks.engineering_v3_pilot import compare_tensors
from benchmarks.engineering_v3_policy import receipt as checkpoint_receipt
from benchmarks.engineering_v3_eval import evaluate
from power_grid_env.study_artifacts import read_json,verify,seal


def compare_revisions(reference,reference_manifest,candidate,candidate_manifest,cases,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh revision comparison required')
    a,ar=audit(reference,reference_manifest,cases,strict_contract());b,br=audit(candidate,candidate_manifest,cases,strict_contract())
    if a['source']!=b['source'] or a['candidate'].get('kind')!='native_model' or b['candidate'].get('kind')!='native_model' or a['candidate'].get('target_sha256')!=b['candidate'].get('target_sha256'):
        raise ValueError('reference and candidate source/model profile differ')
    if not a['candidate'].get('adapter_manifest_sha256') or not b['candidate'].get('adapter_manifest_sha256') or a['candidate']['adapter_manifest_sha256']==b['candidate']['adapter_manifest_sha256']:
        raise ValueError('distinct checkpoint revisions required')
    details=[];qa=Counter();qb=Counter()
    for c,x,y in zip(cases,ar,br):
        ac=quality(c,x)['counts'];bc=quality(c,y)['counts'];qa.update(ac);qb.update(bc)
        details.append(dict(task_id=c['id'],kind=c['kind'],family_sha256=c['family_sha256'],reference_success=x['success'],candidate_success=y['success'],reference_counts=ac,candidate_counts=bc))
    r=dict(schema='power-grid-env.checkpoint-workflow-comparison/v1',reference_manifest_sha256=reference_manifest,candidate_manifest_sha256=candidate_manifest,
        source=a['source'],tasks=len(cases),reference_successes=a['successes'],candidate_successes=b['successes'],
        improved=sum(not r['reference_success'] and r['candidate_success'] for r in details),regressed=sum(r['reference_success'] and not r['candidate_success'] for r in details),
        reference_action_counts=dict(qa),candidate_action_counts=dict(qb),rows=details,
        tiny_train_gate_passed=a['source'].get('role')=='tiny_train' and b['successes']==len(cases),physical_replay_passed=True,
        scope='Matched checkpoint revisions on the declared suite;train success is not heldout generalization or a resource benchmark')
    write_json(root/'summary.json',r);seal(root);return r


def finish(pilot,admission,target,target_sha,initial,initial_manifest,reference_eval,out,chunk_size=8,wait_seconds=7200,resume=None,resume_manifest=None):
    if type(chunk_size) is not int or not 1<=chunk_size<=16:raise ValueError('chunk size1..16 required')
    if type(wait_seconds) is not int or not 1<=wait_seconds<=14400:raise ValueError('finite campaign wait required')
    if bool(resume)!=bool(resume_manifest):raise ValueError('phase continuation needs an owner manifest')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);phase='wait_for_native_lifecycle_pilot'
    job=dict(schema='power-grid-env.v3-mixture-campaign/v1',pilot=str(pilot),admission=str(admission),target_sha256=target_sha,initial_v2_manifest_sha256=initial_manifest,
        reference_evaluation=str(reference_eval),steps=128,chunk_size=chunk_size,resumed_from_phase_manifest=resume_manifest,
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),no_heldout_tuning=True)
    write_json(root/'job.json',job)
    def progress(value,**meta):
        write_json(root/'state.json',dict(phase=value,**meta));print(json.dumps(dict(phase=value,**meta)),flush=True)
    try:
        progress(phase);pilot_anchor=wait_sealed(pilot,time.monotonic()+wait_seconds);p=read_json(Path(pilot)/'summary.json')
        if (p.get('schema')!='power-grid-env.v3-native-lifecycle-pilot/v1' or p.get('complete') is not True or p.get('adapter_optimizer_resume_exact') is not True or
            p.get('warm_start_verified') is not True or p.get('proxy_loaded_and_generated') is not True or p.get('frozen_base_unchanged') is not True or p.get('adapter_roundtrip_exact') is not True or
            p.get('target_sha256')!=target_sha or p.get('initial_v2_manifest_sha256')!=initial_manifest):raise ValueError('native lifecycle pilot did not qualify this phase')
        if verify(admission)!=p['admission_manifest_sha256']:raise ValueError('student admission changed after native pilot')
        compare_tensors(Path(pilot)/'full',Path(pilot)/'resumed')
        checkpoint_receipt(Path(pilot)/'resumed',verify(Path(pilot)/'resumed'),target_sha)
        plan=Path(admission)/'mixture-plan';plan_anchor=verify(plan);mix,planned=load_plan(plan,plan_anchor,target_sha)
        if planned['steps']!=128:raise ValueError('this campaign authorizes exactly128 mixture steps')
        previous=resume;previous_anchor=resume_manifest;cursor=0
        if previous:
            if verify(previous)!=previous_anchor:raise ValueError('owner partial phase differs')
            prior=read_json(Path(previous)/'summary.json');cursor=prior['completed_steps']
            if type(cursor) is not int or not 0<cursor<128:raise ValueError('partial phase cursor required')
        while cursor<128:
            phase='train_checkpoint_chunk';progress(phase,completed_steps=cursor,total_steps=128)
            stop=min(cursor+chunk_size,128);checkpoint=root/('chunk-'+str(stop).zfill(4))
            r=train(target,target_sha,plan,plan_anchor,checkpoint,initial=initial,initial_manifest=initial_manifest,resume=previous,resume_manifest=previous_anchor,max_new_steps=chunk_size)
            if r['completed_steps']!=stop:raise ValueError('phase chunk cursor differs')
            previous=checkpoint;previous_anchor=verify(checkpoint);cursor=stop
            progress(phase,completed_steps=cursor,total_steps=128,checkpoint=str(checkpoint),checkpoint_manifest_sha256=previous_anchor)
        spec=planned['sources'];phase='standalone_eight_case_evaluation';progress(phase,completed_steps=128)
        import sys
        evaluation=evaluate(sys.executable,target,target_sha,previous,previous_anchor,spec['train'],spec['train_manifest'],root/'evaluation',
            suite=spec['suite'],suite_manifest=spec['suite_manifest'])
        phase='wait_for_reference_then_replay';progress(phase)
        reference_anchor=wait_sealed(reference_eval,time.monotonic()+wait_seconds)
        reference_meta=read_json(Path(reference_eval)/'summary.json')
        if reference_meta.get('candidate',{}).get('adapter_manifest_sha256')!=initial_manifest:raise ValueError('reference evaluation is not the starting65-step adapter')
        data=ProceduralDataset(spec['train'],spec['train_manifest'],strict_contract());selected=load_suite(data,spec['suite'],spec['suite_manifest']);cases=[data.scenarios[t] for t in selected['task_ids']]
        paired=compare_revisions(reference_eval,reference_anchor,root/'evaluation',verify(root/'evaluation'),cases,root/'comparison')
        phase='complete';progress(phase,completed_steps=128)
        result=dict(**job,complete=True,pilot_manifest_sha256=pilot_anchor,plan_manifest_sha256=plan_anchor,checkpoint=str(previous),checkpoint_manifest_sha256=previous_anchor,
            evaluation_manifest_sha256=verify(root/'evaluation'),comparison_manifest_sha256=verify(root/'comparison'),tasks=paired['tasks'],
            reference_successes=paired['reference_successes'],candidate_successes=paired['candidate_successes'],tiny_train_gate_passed=paired['tiny_train_gate_passed'],
            next_gate='matched_dev' if paired['tiny_train_gate_passed'] else 'inspect_required_arguments_recovery_and_capacity',
            scope='One admitted128-step text SFT phase plus standalone train-suite comparison;no VLM,GRPO or heldout qualification')
        write_json(root/'summary.json',result);seal(root);return result
    except Exception as exc:
        write_json(root/'failure.json',dict(**job,complete=False,failed_phase=phase,error=type(exc).__name__,detail=str(exc)[:1500]));seal(root);raise


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('pilot','admission','target','target-sha256','initial','initial-manifest-sha256','reference-evaluation','out'):p.add_argument('--'+name,required=True)
    p.add_argument('--chunk-size',type=int,default=8);p.add_argument('--wait-seconds',type=int,default=7200);p.add_argument('--resume');p.add_argument('--resume-manifest-sha256')
    a=p.parse_args();print(json.dumps(finish(a.pilot,a.admission,a.target,a.target_sha256,a.initial,a.initial_manifest_sha256,a.reference_evaluation,a.out,a.chunk_size,a.wait_seconds,a.resume,a.resume_manifest_sha256)))
