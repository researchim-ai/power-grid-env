"""Recompute primitive pilot scores, source attribution and TRAIN-only selection."""
import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_primitive_admission import PrimitiveDataset
from benchmarks.engineering_primitive_pilot import order
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.engineering_staged_agent import bounded_json
from power_grid_env.study_artifacts import read_json, seal, verify


def grade(raw, expected):
    try:
        actual=bounded_json(raw)
    except (ValueError,TypeError):
        return 'protocol_error'
    if set(actual)!=set(expected):
        return 'field_schema_error'
    return 'correct' if sha(actual)==sha(expected) else 'wrong_values_or_types'


def audit(pilot,anchor,admission,admission_anchor,target_sha,out):
    root=Path(out)
    if root.exists() or verify(pilot)!=anchor:
        raise ValueError('fresh audit and matching pilot manifest required')
    report=read_json(Path(pilot)/'summary.json')
    if report.get('schema')!='power-grid-env.primitive-native-pilot/v1' or report.get('complete') is not True:
        raise ValueError('complete primitive pilot required')
    identity=report['identity']
    if identity.get('admission_manifest_sha256')!=admission_anchor or identity.get('target_sha256')!=target_sha:
        raise ValueError('pilot admission/target attribution differs')
    for name,expected in identity['source_sha256'].items():
        if hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()!=expected:
            raise ValueError('pilot implementation changed:'+name)
    data=PrimitiveDataset(admission,admission_anchor,target_sha)
    indices,nuisance=data.pilot_indices()
    recipe=report['recipe']
    if (recipe['indices']!=indices or recipe['nuisance_indices']!=nuisance or
            recipe['schedule']!=order(indices) or recipe['steps']!=30):
        raise ValueError('fixed pilot selection/schedule differs')
    history=report['history']
    if len(history)!=30:
        raise ValueError('complete optimizer history required')
    for step,(entry,index) in enumerate(zip(history,order(indices))):
        if entry['step']!=step or entry['index']!=index or entry['row_id']!=data.rows[index]['id']:
            raise ValueError('optimizer target attribution differs')
        if not math.isfinite(entry['loss']) or not math.isfinite(entry['gradient_norm']) or entry['gradient_norm']<=0:
            raise ValueError('invalid recorded optimization metrics')
    diagnostics={}
    for phase in ('before','after'):
        values=report[phase]
        if len(values)!=20:
            raise ValueError('complete original+nuisance generation required')
        classes=Counter()
        groups={}
        correct_by_skill=Counter()
        for result,index in zip(values,indices+nuisance):
            row=data.example(index)
            expected_group='train_original' if index in indices else 'untrained_key_order'
            if (result['index']!=index or result['row_id']!=row['id'] or result['skill']!=row['skill'] or
                    result['group']!=expected_group or result['phase']!=phase or sha(result['expected'])!=sha(row['answer'])):
                raise ValueError('generated target attribution differs')
            classification=grade(result['completion'],row['answer'])
            correct=classification=='correct'
            if result['correct'] is not correct:
                raise ValueError('recorded generated score differs')
            classes[classification]+=1
            group=groups.setdefault(expected_group,dict(correct=0,total=0))
            group['correct']+=int(correct);group['total']+=1
            if correct: correct_by_skill[row['skill']]+=1
        if report[phase+'_correct']!=classes['correct'] or report[phase+'_by_skill']!=dict(correct_by_skill):
            raise ValueError('aggregate generated score differs')
        diagnostics[phase]=dict(classification=dict(classes),groups=groups,correct_by_skill=dict(correct_by_skill))
    gate=all(r['correct'] for r in report['after'])
    if report['primitive_learning_gate_passed'] is not gate or report['full_workflow_quality_gate_passed'] is not False or report['independent_heldout_evaluated'] is not False:
        raise ValueError('pilot gate/heldout promotion differs')
    if report['frozen_base_unchanged'] is not True or report['adapter_roundtrip_exact'] is not True or report['inference_release'] is not False:
        raise ValueError('frozen/roundtrip/release scope differs')
    output=dict(schema='power-grid-env.primitive-pilot-audit/v1',passed=True,pilot_manifest_sha256=anchor,
                admission_manifest_sha256=admission_anchor,diagnostics=diagnostics,primitive_learning_gate_passed=gate,
                full_workflow_quality_gate_passed=False,independent_heldout_evaluated=False,
                source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                scope='Recomputed scores and lineage on10 TRAIN originals/10 key-order variants from one base; '
                      'does not independently attest model execution or qualify unseen-family/workflow success.')
    write_json(root/'summary.json',output);seal(root)
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('pilot','manifest-sha256','admission','admission-manifest-sha256','target-sha256','out'):
        p.add_argument('--'+key,required=True)
    a=p.parse_args()
    print(json.dumps(audit(a.pilot,a.manifest_sha256,a.admission,a.admission_manifest_sha256,a.target_sha256,a.out)))
