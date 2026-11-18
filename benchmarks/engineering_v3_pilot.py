"""Finite real-model lifecycle pilot after complete student-data admission.

Two mixture targets,one per source,with at least one parameterized call.
Seed selection uses labels only,never model outcomes. This is not policy eval.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import sys
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_learning_campaign import wait_sealed
from benchmarks.engineering_learning_mix import plan
from benchmarks.engineering_trainer_v3 import load_plan,train,expected_action,state_digest
from power_grid_env.study_artifacts import read_json,verify,seal


def compare_tensors(full,resumed):
    import torch
    from safetensors.torch import load_file
    for name in ('adapter.safetensors','optimizer.safetensors'):
        a=load_file(str(Path(full)/name));b=load_file(str(Path(resumed)/name))
        if a.keys()!=b.keys() or any(not torch.equal(a[k],b[k]) for k in a):raise ValueError('native uninterrupted/resumed tensors differ:'+name)


def finish(admission,target,target_sha,initial,initial_manifest,out,wait_seconds=7200):
    if type(wait_seconds) is not int or not 1<=wait_seconds<=14400:raise ValueError('finite pilot wait required')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);phase='wait_for_student_admission'
    job=dict(schema='power-grid-env.v3-native-lifecycle-pilot/v1',admission=str(admission),target_sha256=target_sha,initial_v2_manifest_sha256=initial_manifest,
        selection='first_seed42..141_with_one_parameterized_call;2steps_one_per_source;no_model_outcome_selection',
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),full_policy_quality_claim=False)
    write_json(root/'job.json',job)
    def progress(value):
        write_json(root/'state.json',dict(phase=value));print(json.dumps(dict(phase=value)),flush=True)
    try:
        progress(phase);admission_anchor=wait_sealed(admission,time.monotonic()+wait_seconds);receipt=read_json(Path(admission)/'summary.json')
        if receipt.get('schema')!='power-grid-env.student-data-campaign/v1' or receipt.get('complete') is not True or receipt.get('admission_passed') is not True:
            raise ValueError('actual-student admission did not pass')
        if receipt.get('target_sha256')!=target_sha:raise ValueError('admission target differs')
        full_plan=Path(admission)/'mixture-plan';mix,old=load_plan(full_plan,verify(full_plan),target_sha)
        phase='select_two_source_pilot';progress(phase);seed=None
        for candidate in range(42,142):
            if any(expected_action(mix,e)['arguments'] for e in mix.schedule(2,candidate)):seed=candidate;break
        if seed is None:raise ValueError('no parameterized pilot pair within bounded seed selection')
        spec=Path(admission)/'mixture-sources.json';spec_sha=hashlib.sha256(spec.read_bytes()).hexdigest()
        p=plan(spec,spec_sha,root/'plan',steps=2,seed=seed);anchor=verify(root/'plan')
        args=(target,target_sha,root/'plan',anchor)
        phase='uninterrupted_warm_start';progress(phase)
        full=train(*args,root/'full',initial=initial,initial_manifest=initial_manifest)
        phase='first_chunk_new_phase';progress(phase)
        head=train(*args,root/'head',initial=initial,initial_manifest=initial_manifest,max_new_steps=1)
        phase='restore_same_phase';progress(phase)
        tail=train(*args,root/'resumed',initial=initial,initial_manifest=initial_manifest,resume=root/'head',resume_manifest=verify(root/'head'))
        phase='compare_native_adamw_and_adapters';progress(phase);compare_tensors(root/'full',root/'resumed')
        from safetensors.torch import load_file
        expected_initial=state_digest(load_file(str(Path(initial)/'adapter.safetensors')))
        if full['phase_initial_adapter_sha256']!=expected_initial or tail['phase_initial_adapter_sha256']!=expected_initial:raise ValueError('native warm-start identity differs')
        phase='native_proxy_loading_probe';progress(phase)
        from benchmarks.engineering_rollout import ProcessPolicy,parse_action
        from benchmarks.engineering_recovery import episode_messages
        from benchmarks.engineering_student_dataset import source_sha
        entry=p['schedule'][0];data=mix.datasets[entry['source']]
        if entry['source']=='student_correction':messages,expected_call,episode,_=data.example(entry['index'])
        else:
            e,t=data.targets[entry['index']];episode=data.episodes[e]
            messages,event=next((m,event) for m,event in episode_messages(episode) if event['turn']==t);expected_call=event['action']
        command=[sys.executable,'-m','benchmarks.engineering_v3_policy','--target',str(target),'--target-sha256',target_sha,
            '--adapter',str(root/'resumed'),'--adapter-manifest-sha256',verify(root/'resumed')]
        policy=ProcessPolicy(command,timeout=180)
        try:generated=policy(messages)
        finally:policy.close()
        try:action=parse_action(generated['text'],json.loads(messages[1]['content'])['task']['tools']);wire_error=None
        except (ValueError,TypeError) as exc:action=None;wire_error=str(exc)
        write_json(root/'proxy-probe.json',dict(source=entry['source'],task_id=episode['task_id'],expected=expected_call,generated=generated,action=action,
            wire_error=wire_error,exact_call_match=action==expected_call,policy_source_sha256=source_sha('engineering_v3_policy.py'),full_policy_quality_claim=False))
        phase='complete';progress(phase)
        r=dict(**job,complete=True,admission_manifest_sha256=admission_anchor,plan_manifest_sha256=anchor,seed=seed,optimizer_updates=4,
            phase_steps=2,sources=p['source_counts'],full_manifest_sha256=verify(root/'full'),resumed_manifest_sha256=verify(root/'resumed'),
            warm_start_verified=True,adapter_optimizer_resume_exact=True,frozen_base_unchanged=full['frozen_base_unchanged'] and tail['frozen_base_unchanged'],
            adapter_roundtrip_exact=full['adapter_roundtrip_exact'] and tail['adapter_roundtrip_exact'],proxy_loaded_and_generated=True,
            proxy_wire_valid=wire_error is None,proxy_exact_conditional_call=action==expected_call,closed_loop_quality_gate_status='not_run',
            scope='Native CPU numerical/lifecycle qualification of a two-source2-step phase;not learned recovery,full policy,VLM or GRPO qualification')
        write_json(root/'summary.json',r);seal(root);return r
    except Exception as exc:
        write_json(root/'failure.json',dict(**job,complete=False,failed_phase=phase,error=type(exc).__name__,detail=str(exc)[:1500]));seal(root);raise


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('admission','target','target-sha256','initial','initial-manifest-sha256','out'):p.add_argument('--'+name,required=True)
    p.add_argument('--wait-seconds',type=int,default=7200)
    a=p.parse_args();print(json.dumps(finish(a.admission,a.target,a.target_sha256,a.initial,a.initial_manifest_sha256,a.out,a.wait_seconds)))
