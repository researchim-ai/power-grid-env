"""Offline integrity and retention audit of the completed GPU comparison.

Loads no model and performs no optimizer update. A valid report is not a
learning quality pass; gains may coexist with lost previously solved cases.
"""
import argparse
import hashlib
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_workflow_replay import replay,metrics
from power_grid_env.study_artifacts import read_json,verify,seal


def changes(before,after):
    a={e['task_id']:e for e in before};b={e['task_id']:e for e in after}
    if len(a)!=len(before) or len(b)!=len(after) or a.keys()!=b.keys():raise ValueError('complete identical episode inventories required')
    if any(a[k]['split']!=b[k]['split'] or a[k]['base_project_id']!=b[k]['base_project_id'] for k in a):
        raise ValueError('case ancestry differs')
    return {split:dict(lost=[k for k in a if a[k]['split']==split and a[k]['success'] and not b[k]['success']],
                       gained=[k for k in a if a[k]['split']==split and not a[k]['success'] and b[k]['success']]) for split in ('train','dev','test')}


def audit(plan,out):
    plan=Path(plan);plan_anchor=verify(plan);cases=read_json(plan/'cases.json');ids=[c['id'] for c in cases]
    result={};replayed=0
    for candidate in ('qwen08b','qwen4b'):
        previous=Path('reports/engineering-workflow-holdout-'+candidate+'-2026-11-19')
        current=Path('reports/engineering-decision-regression-'+candidate+'-2026-11-19')
        anchors=dict(fixed96=verify(previous),decision240=verify(current));phases={};traces={}
        for label,root,phase in (('base',previous,'base'),('fixed96',previous,'sft'),('decision240',current,'sft')):
            data=read_json(root/(phase+'.json'))
            if not data['complete'] or [e['task_id'] for e in data['episodes']]!=ids:raise ValueError('full frozen numerical inventory required')
            for case,trace in zip(cases,data['episodes']):
                replay(case,trace);replayed+=1
                for kind,name in (('actual','simulate'),('preview','preview_line_catalog')):
                    count=sum(e['action']['name']==name and e['reply'].get('ok') is True for e in trace['events'])
                    if count!=sum(p['kind']==kind for p in trace['physics_checks']):raise ValueError('accepted study lacks independent proof')
                if any('reference residual' in str(e['reply'].get('error')) for e in trace['events']):raise ValueError('independent physics backend failure')
            phases[label]=metrics(data['episodes']);traces[label]=data['episodes']
        delta=changes(traces['fixed96'],traces['decision240'])
        result[candidate]=dict(manifests=anchors,metrics=phases,changes=delta,
            no_previous_success_losses=not any(v['lost'] for v in delta.values()),
            disclosed_suite_all_solved=all(e['success'] for e in traces['decision240']))
    root=Path(out)
    if root.exists():raise ValueError('fresh campaign audit required')
    summary=dict(schema='power-grid-env.workflow-campaign-audit/v1',integrity_passed=True,episodes_replayed=replayed,
        plan_manifest_sha256=plan_anchor,candidates=result,model_weights_loaded=False,optimizer_run=False,
        inference_release=False,new_unbiased_test=False,
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='Fixed96 initial test disclosed once; decision240 evaluated on the same now-disclosed regression suite. Data, seed and updates differ.')
    write_json(root/'summary.json',summary);seal(root);return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--plan',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=audit(a.plan,a.out);print(dict(integrity_passed=r['integrity_passed'],episodes_replayed=r['episodes_replayed']))
