"""Fail-closed answer-class coverage for selected focused SFT lessons.

Full-corpus admission does not imply a selected training schedule has contrast.
Coverage here is relative to observed task heads/classes, not all engineering.
"""
import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_focused_curriculum import FocusedDataset
from benchmarks.engineering_focused_phase import parent_receipt
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.engineering_staged_data import pack
from power_grid_env.study_artifacts import encoded,read_json,seal,verify


def head(row):
    problem=json.loads(row['messages'][1]['content'])['problem']
    return (row['skill'],row['field'],problem['direction'] if row['skill']=='coarse_threshold' else None)


def coverage(data,indices):
    if not indices or len(set(indices))!=len(indices):raise ValueError('distinct nonempty focused selection required')
    selected=[data.example(i) for i in indices]
    heads={head(r) for r in selected}
    result=[]
    for group in sorted(heads,key=str):
        available={encoded(r['answer']) for r in data.rows if head(r)==group}
        present=Counter(encoded(r['answer']) for r in selected if head(r)==group)
        result.append(dict(head=list(group),available=[json.loads(a) for a in sorted(available)],
                           selected=[dict(answer=json.loads(a),count=n) for a,n in sorted(present.items())],
                           missing=[json.loads(a) for a in sorted(available-set(present))]))
    return dict(passed=all(not r['missing'] for r in result),heads=result,
                deficient_heads=sum(bool(r['missing']) for r in result),
                scope='Typed answer classes observed in this admitted corpus and selected task heads only.')


def balanced_selection(data):
    original,_,_,_=data.selection()
    selected=list(original)
    base=data.example(original[0])['base_project_id']
    while True:
        result=coverage(data,selected)
        if result['passed']:return selected,result
        added=False
        for group in result['heads']:
            for answer in group['missing']:
                candidates=[i for i,r in enumerate(data.rows) if list(head(r))==group['head'] and
                    r['base_project_id']==base and r['nuisance_variant']=='original' and sha(r['answer'])==sha(answer)]
                if not candidates:raise ValueError('required contrast unavailable within selected TRAIN base')
                preferred='fresh_mutation' if group['head'][1]=='fresh_after' else 'initial_validate' if group['head'][1]=='simulations_spent' else 'equal'
                candidates.sort(key=lambda i:(data.rows[i]['member']!=preferred,i))
                index=candidates[0]
                if index not in selected:selected.append(index);added=True
        if not added:raise ValueError('cannot close focused class coverage')


def require_coverage(data,indices):
    result=coverage(data,indices)
    if not result['passed']:raise ValueError('selected focused lessons lack typed answer contrasts')
    return result


def make_schedule(data,selected):
    require_coverage(data,selected)
    retention=data.parent.pilot_indices()[0]
    rng=random.Random(20261119)
    buckets={}
    for source,indices in (('focused',selected),('retention',retention)):
        values=[]
        while len(values)<30:
            part=list(indices);rng.shuffle(part)
            values.extend(dict(source=source,index=i,row_id=(data.rows if source=='focused' else data.parent.rows)[i]['id']) for i in part)
        buckets[source]=values[:30]
    values=buckets['focused']+buckets['retention'];rng.shuffle(values)
    if not set(selected)<={x['index'] for x in values if x['source']=='focused'}:
        raise ValueError('every admitted contrast must be exposed')
    return values


FILES=('engineering_focused_balance.py','engineering_focused_curriculum.py','engineering_focused_phase.py')
def sources():return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in FILES}


def plan(focused,anchor,parent,parent_anchor,target,target_sha,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh balanced plan required')
    data=FocusedDataset(focused,anchor,target_sha)
    parent_receipt(parent,parent_anchor,data,target_sha)
    old=data.selection()[0];selected,cover=balanced_selection(data)
    transfer=[]
    for i in selected:
        r=data.rows[i]
        transfer.append(next(j for j,v in enumerate(data.rows) if v['pair_group']==r['pair_group'] and v['member']==r['member'] and v['nuisance_variant']=='reordered_keys'))
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    import torch
    torch.set_num_threads(4)
    spec=local_target(target,target_sha)
    processor=local_qwen35_processor(spec['local_checkpoint'],spec['config_sha256'])
    checks=[]
    for i in selected+transfer:
        _,meta=pack(processor,data.example(i));checks.append(dict(index=i,row_id=data.rows[i]['id'],**meta))
    order=make_schedule(data,selected)
    root.mkdir(parents=True)
    value=dict(schema='power-grid-env.focused-balanced-plan/v1',focused_release=str(Path(focused).resolve()),
               focused_manifest_sha256=anchor,parent_pilot=str(Path(parent).resolve()),parent_manifest_sha256=parent_anchor,
               target_sha256=target_sha,original_selection_coverage=coverage(data,old),coverage=cover,
               selected=selected,nuisance_indices=transfer,schedule=order,steps=60,
               roles=dict(Counter(r['source'] for r in order)),native_checks=checks,
               common_focused_probe_indices=old+data.selection()[1],
               additional_focused_probe_indices=[i for i in selected+transfer if i not in old+data.selection()[1]],
               source_sha256=sources(),optimizer_run=False,training_complete=False,learning_gate_passed=False,
               scope='Coverage-corrected proposed60-update phase rebased on original primitive pilot. '
                     'No weights trained; preserve common20 focused/common20 retention denominators and report added contrasts separately.')
    write_json(root/'summary.json',value);seal(root)
    return {k:v for k,v in value.items() if k not in ('schedule','native_checks')}


def load_plan(root,anchor,data):
    if verify(root)!=anchor:raise ValueError('balanced plan manifest differs')
    p=read_json(Path(root)/'summary.json')
    if p.get('schema')!='power-grid-env.focused-balanced-plan/v1' or p.get('source_sha256')!=sources() or p.get('optimizer_run') is not False:
        raise ValueError('unexecuted matching balanced plan required')
    if (verify(p['focused_release'])!=p['focused_manifest_sha256'] or
            sha(read_json(Path(p['focused_release'])/'summary.json'))!=sha(data.plan) or
            p['target_sha256']!=data.plan['target_sha256']):
        raise ValueError('balanced data attribution differs')
    parent_receipt(p['parent_pilot'],p['parent_manifest_sha256'],data,p['target_sha256'])
    selected,_=balanced_selection(data)
    if p['selected']!=selected:raise ValueError('predeclared balanced selection differs')
    if sha(require_coverage(data,p['selected']))!=sha(p['coverage']) or sha(make_schedule(data,p['selected']))!=sha(p['schedule']):
        raise ValueError('balanced coverage or schedule differs')
    transfer=[]
    for i in selected:
        row=data.rows[i]
        transfer.append(next(j for j,r in enumerate(data.rows) if r['pair_group']==row['pair_group'] and r['member']==row['member'] and r['nuisance_variant']=='reordered_keys'))
    if p['nuisance_indices']!=transfer:raise ValueError('balanced nuisance selection differs')
    expected=p['selected']+p['nuisance_indices']
    if [(r['index'],r['row_id']) for r in p['native_checks']]!=[(i,data.rows[i]['id']) for i in expected]:
        raise ValueError('complete balanced native inventory required')
    return p


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('focused-release','manifest-sha256','parent-pilot','parent-manifest-sha256','target','target-sha256','out'):
        p.add_argument('--'+key,required=True)
    a=p.parse_args()
    print(json.dumps(plan(a.focused_release,a.manifest_sha256,a.parent_pilot,a.parent_manifest_sha256,a.target,a.target_sha256,a.out)))
