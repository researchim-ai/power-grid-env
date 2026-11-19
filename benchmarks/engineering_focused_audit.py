"""Recompute corrective-phase scores and individual retention regressions.

An audit of sealed records/lineage, not independent attestation of model execution.
"""
import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_focused_curriculum import FocusedDataset
from benchmarks.engineering_focused_phase import parent_receipt,probes,schedule,sources,totals
from benchmarks.engineering_primitive_pilot_audit import grade
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.engineering_staged_agent import bounded_json
from power_grid_env.study_artifacts import read_json,seal,verify


def regressions(before,after):
    keys=lambda rows:{(r['source'],r['row_id']):r['correct'] for r in rows}
    old,new=keys(before),keys(after)
    if len(old)!=len(before) or len(new)!=len(after) or old.keys()!=new.keys():
        raise ValueError('matching unique before/after probes required')
    return dict(regressed=[dict(source=k[0],row_id=k[1]) for k in old if old[k] and not new[k]],
                gained=[dict(source=k[0],row_id=k[1]) for k in old if not old[k] and new[k]])


def audit(phase,anchor,focused,focused_anchor,parent,parent_anchor,target_sha,out):
    root=Path(out)
    if root.exists() or verify(phase)!=anchor:raise ValueError('fresh audit and matching phase manifest required')
    report=read_json(Path(phase)/'summary.json')
    if report.get('schema')!='power-grid-env.focused-native-phase/v1' or report.get('complete') is not True:
        raise ValueError('complete focused phase required')
    identity=report['identity']
    if (identity.get('focused_manifest_sha256')!=focused_anchor or identity.get('parent_manifest_sha256')!=parent_anchor or
            identity.get('target_sha256')!=target_sha or identity.get('source_sha256')!=sources()):
        raise ValueError('phase lineage/source attribution differs')
    data=FocusedDataset(focused,focused_anchor,target_sha)
    prior=parent_receipt(parent,parent_anchor,data,target_sha)
    recipe=report['recipe']
    if (sha(recipe['schedule'])!=sha(schedule(data)) or recipe['steps']!=60 or recipe['seed']!=20261119 or
            recipe['learning_rate']!=.0001 or recipe['objective']!='assistant_only_uniform_cross_entropy'):
        raise ValueError('predeclared focused recipe differs')
    if len(report['history'])!=60:raise ValueError('complete focused history required')
    for step,(value,entry) in enumerate(zip(report['history'],schedule(data))):
        if value['step']!=step or any(value[k]!=v for k,v in entry.items()):
            raise ValueError('optimizer target attribution differs')
        if (not math.isfinite(value['loss']) or value['loss']<0 or not math.isfinite(value['gradient_norm']) or value['gradient_norm']<=0):
            raise ValueError('invalid optimization metrics')
    diagnostics={}
    for phase_name in ('before','after'):
        values=report[phase_name]
        if len(values)!=40:raise ValueError('complete40-probe generation required')
        counts={source:Counter() for source in ('retention','focused')}
        groups={}
        fields={}
        for result,entry in zip(values,probes(data)):
            dataset=data.parent if entry['source']=='retention' else data
            row=dataset.example(entry['index'])
            if (result['source']!=entry['source'] or result['index']!=entry['index'] or result['row_id']!=row['id'] or
                    result['phase']!=phase_name or result['presentation']!=row['nuisance_variant'] or sha(result['expected'])!=sha(row['answer'])):
                raise ValueError('generated focused attribution differs')
            category=grade(result['completion'],row['answer'])
            if result['correct'] is not (category=='correct'):raise ValueError('generated score differs')
            counts[entry['source']][category]+=1
            group=groups.setdefault(entry['source']+':'+row['nuisance_variant'],dict(correct=0,total=0))
            group['correct']+=int(category=='correct');group['total']+=1
            try:actual=bounded_json(result['completion'])
            except (ValueError,TypeError):actual={}
            for field,expected in row['answer'].items():
                field_group=fields.setdefault(entry['source']+':'+field,dict(correct=0,total=0))
                field_group['correct']+=int(field in actual and sha(actual[field])==sha(expected));field_group['total']+=1
        if report[phase_name+'_correct']!=totals(values):raise ValueError('aggregate focused score differs')
        diagnostics[phase_name]=dict(classifications={k:dict(v) for k,v in counts.items()},groups=groups,fields=fields)
    before_retention=[r for r in report['before'] if r['source']=='retention']
    if [r['completion'] for r in before_retention]!=[r['completion'] for r in prior['after']] or report['parent_generation_replay_exact'] is not True:
        raise ValueError('parent generation replay was not exact')
    changes=regressions(report['before'],report['after'])
    gate=all(r['correct'] for r in report['after'])
    if (report['focused_and_retention_gate_passed'] is not gate or report['full_workflow_quality_gate_passed'] is not False or
            report['independent_heldout_evaluated'] is not False or report['inference_release'] is not False or
            report['frozen_base_unchanged'] is not True or report['adapter_roundtrip_exact'] is not True):
        raise ValueError('quality/frozen/heldout promotion differs')
    output=dict(schema='power-grid-env.focused-phase-audit/v1',passed=True,phase_manifest_sha256=anchor,
                focused_manifest_sha256=focused_anchor,parent_manifest_sha256=parent_anchor,
                before_correct=report['before_correct'],after_correct=report['after_correct'],diagnostics=diagnostics,
                changes=changes,focused_and_retention_gate_passed=gate,
                no_retention_regressions=not any(r['source']=='retention' for r in changes['regressed']),
                independent_heldout_evaluated=False,full_workflow_quality_gate_passed=False,
                source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                scope='Recorded TRAIN generation/schedule/source audit with individual regression accounting; '
                      'not attestation of model execution, causal isolation, unseen-family or workflow quality.')
    write_json(root/'summary.json',output);seal(root)
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('phase','manifest-sha256','focused-release','focused-manifest-sha256','parent-pilot',
                'parent-manifest-sha256','target-sha256','out'):p.add_argument('--'+key,required=True)
    a=p.parse_args()
    print(json.dumps(audit(a.phase,a.manifest_sha256,a.focused_release,a.focused_manifest_sha256,
        a.parent_pilot,a.parent_manifest_sha256,a.target_sha256,a.out)))
