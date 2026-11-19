"""Sealed cross-arm comparison; does not turn TRAIN constraints into OOD evidence."""
import argparse
import hashlib
import json
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_balanced_training import validate_result,sources as balanced_sources
from benchmarks.engineering_retention_guard import SCALES,sources as guard_sources
from benchmarks.engineering_focused_balance import load_plan
from benchmarks.engineering_focused_curriculum import FocusedDataset
from benchmarks.engineering_focused_phase import parent_receipt
from benchmarks.engineering_sft_release_v2 import sha
from power_grid_env.study_artifacts import read_json,verify,seal


def audit(plan,anchor,arms,out):
    root=Path(out)
    if root.exists() or verify(plan)!=anchor:raise ValueError('fresh comparison and matching plan required')
    header=read_json(Path(plan)/'summary.json')
    data=FocusedDataset(header['focused_release'],header['focused_manifest_sha256'],header['target_sha256'])
    p=load_plan(plan,anchor,data)
    prior=parent_receipt(p['parent_pilot'],p['parent_manifest_sha256'],data,p['target_sha256'])
    if set(arms)!={'balanced','anchored','guarded'}:raise ValueError('all three executed arms required')
    reports={};results={}
    for name,spec in arms.items():
        if verify(spec['path'])!=spec['manifest_sha256']:raise ValueError('arm manifest differs')
        r=read_json(Path(spec['path'])/'summary.json')
        expected='power-grid-env.retention-guard-native-training/v1' if name=='guarded' else 'power-grid-env.balanced-native-training/v1'
        if (r.get('schema')!=expected or r.get('complete') is not True or r['identity']['plan_manifest_sha256']!=anchor or
                r['identity']['parent_manifest_sha256']!=p['parent_manifest_sha256'] or
                r['identity']['source_sha256']!=(guard_sources() if name=='guarded' else balanced_sources())):
            raise ValueError('arm source/parent/profile differs')
        validate_result(r,data,p,prior)
        if sha(r['recipe']['schedule'])!=sha(p['schedule']) or len(r['history'])!=60:
            raise ValueError('same60 SFT schedule required')
        for step,(record,entry) in enumerate(zip(r['history'],p['schedule'])):
            if record['step']!=step or any(record[k]!=v for k,v in entry.items()):raise ValueError('arm optimization targets differ')
        if not r['parent_generation_replay_exact'] or not r['frozen_base_unchanged'] or not r['adapter_roundtrip_exact']:
            raise ValueError('native integrity checks required')
        if name=='guarded':
            for key in ('prefix_cache_initial_proofs','prefix_cache_final_proofs'):
                if len(r[key])!=12 or any(v.get('bit_exact') is not True or v.get('maximum_logit_difference')!=0. for v in r[key]):
                    raise ValueError('bit-exact native prefix-cache qualification required')
            guard_indices=r['recipe']['guard_train_indices']
            expected_indices=[x['index'] for x in prior['after'] if x['correct']]
            if guard_indices!=expected_indices or not r['recipe']['guard_uses_key_order_training_views']:
                raise ValueError('guard TRAIN conditioning attribution differs')
            for record in r['history']:
                attempts=record['attempts']
                if not attempts or [a['scale'] for a in attempts]!=list(SCALES[:len(attempts)]):raise ValueError('guard trial scales differ')
                if record['accepted']:
                    if not attempts[-1]['passed'] or any(a['passed'] for a in attempts[:-1]):raise ValueError('guard accepted an invalid proposal')
                    if record['accepted_scale']!=attempts[-1]['scale']:raise ValueError('guard accepted scale differs')
                elif len(attempts)!=len(SCALES) or any(a['passed'] for a in attempts):raise ValueError('guard rejected passing/incomplete trials')
            if r['accepted_optimizer_updates']!=sum(x['accepted'] for x in r['history']):raise ValueError('accepted update count differs')
        elif r['recipe']['arm']!=name:raise ValueError('arm objective differs')
        reports[name]=r
        results[name]=dict(before=r['before_metrics'],after=r['after_metrics'],
                           lost_previous_retention_answers=sum(x['source']=='retention' for x in r['changes']['regressed']),
                           no_retention_regressions=r['no_retention_regressions'],all_probe_gate_passed=r['all_probe_gate_passed'],
                           accepted_updates=r.get('accepted_optimizer_updates',60))
    if any(sha(r['before'])!=sha(reports['balanced']['before']) for r in reports.values()):
        raise ValueError('native initial generation differs between arms')
    fixed=results['guarded']['no_retention_regressions'] and results['guarded']['after']['common_focused']['correct']>results['guarded']['before']['common_focused']['correct']
    output=dict(schema='power-grid-env.regression-repair-comparison/v1',passed=True,plan_manifest_sha256=anchor,
                arms=arms,results=results,regression_fix_verified_on_declared_train_probes=fixed,
                independent_heldout_evaluated=False,full_workflow_quality_gate_passed=False,inference_release=False,
                guarded_key_order_views_are_training_constraints=True,
                source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                scope='Recorded native three-arm TRAIN comparison with identical initial generation/SFT schedule. '
                      'KL adds extra original-anchor forwards; hard guard additionally conditions on12 known TRAIN answers including key-order views. '
                      'Not an equal-compute or independent-transfer benchmark and not attestation of kernel execution.')
    write_json(root/'summary.json',output);seal(root)
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('plan','manifest-sha256','arms','out'):p.add_argument('--'+key,required=True)
    a=p.parse_args();print(json.dumps(audit(a.plan,a.manifest_sha256,read_json(a.arms),a.out)))
