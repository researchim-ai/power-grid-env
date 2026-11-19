"""Matched0.8B/4B baseline on actual solver-assisted workflows.

Plans freeze cases, controls, budgets and model identities before generation.
Train SFT traces are scripted, physically replayed and never include dev gold.
"""
import argparse
import copy
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

from benchmarks import engineering_agent as judge
from benchmarks.engineering_assisted_workflow import run as episode,control,MAX_ACTIONS,MAX_PREVIEWS
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_primitive_curriculum import load as load_primitive
from benchmarks.engineering_sft_release_v2 import non_dominant,checked_study,sha
from power_grid_env.study_artifacts import read_json,verify,seal


FILES=('engineering_workflow_campaign.py','engineering_assisted_workflow.py','engineering_sft_contract_v2.py',
       'engineering_reference.py','engineering_primitive_curriculum.py','engineering_family_registry.py')
def sources():return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in FILES}


def tradeoff_case(base):
    case,evidence=non_dominant(base)
    result,_=checked_study(case['project'])
    target=max((b for b in result['branches'] if b['loading_pct'] is not None),key=lambda b:b['loading_pct'])['id']
    current=next(b['from_terminal']['current_a'] for b in result['branches'] if b['id']==target)
    best=min(case['requirements']['catalog'].values(),key=lambda c:c['r_ohm_km'])
    decoy=dict(best,r_ohm_km=best['r_ohm_km']/4,rating_a=current/4)
    case['requirements']['catalog']['part-'+sha([case['id'],'low_impedance_low_ampacity'])[:10]]=decoy
    case['id']='tradeoff-'+base['id']
    checks=[];feasible=[]
    for name,passport in case['requirements']['catalog'].items():
        project=copy.deepcopy(case['project']);next(l for l in project['lines'] if l['id']==target).update(passport)
        solved,proof=checked_study(project)
        violations=judge.structure_errors(project,case['requirements'])+judge.physical_errors(solved,case['requirements'])
        checks.append(dict(catalog_id=name,violations=violations,**proof))
        if not violations:feasible.append(name)
    largest=max(case['requirements']['catalog'],key=lambda c:case['requirements']['catalog'][c]['rating_a'])
    lowest=min(case['requirements']['catalog'],key=lambda c:case['requirements']['catalog'][c]['r_ohm_km'])
    if len(feasible)!=1 or largest in feasible or lowest in feasible:raise ValueError('tradeoff controls are not discriminating')
    return case,dict(catalogue_studies=checks,feasible_count=len(feasible),maximum_rating_fails=True,minimum_resistance_fails=True)


def heuristic(query,ranking):
    p=json.loads(query['messages'][1]['content'])
    if not p['has_fresh_measurements']:a=dict(name='simulate',arguments={})
    elif p['current_result']['measurement_feasible'] and p['current_result']['structure_matches']:a=dict(name='submit',arguments={})
    elif p['current_result']['overloaded_branches'] and p['catalog_edits_used']==0:
        line=max(p['current_result']['overloaded_branches'],key=lambda x:x['loading_pct'])['id']
        catalog=(max(p['scene']['catalog'],key=lambda c:c['rating_a']) if ranking=='max_rating' else min(p['scene']['catalog'],key=lambda c:c['r_ohm_km']))['id']
        a=dict(name='set_line_catalog',arguments=dict(equipment_id=line,catalog_id=catalog))
    else:a=dict(name='submit',arguments={})
    return json.dumps(a)


def plan(source,source_anchor,targets,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh workflow campaign plan required')
    _,_,summary=load_primitive(source,source_anchor)
    cases=[json.loads(line) for line in (Path(source)/'cases.jsonl').read_text().splitlines()]
    selected=[];checks=[]
    for split in ('train','dev'):
        chosen=[];families=set()
        for base in sorted((c for c in cases if c['split']==split),key=lambda c:(len(c['project']['nodes']),c['base_project_id'])):
            if base['family_sha256'] in families:continue
            families.add(base['family_sha256']);chosen.append(base)
            if len(chosen)==2:break
        if len(chosen)!=2:raise ValueError('two distinct families per evaluated split required')
        healthy=copy.deepcopy(chosen[0]);healthy.update(id='healthy-'+healthy['id'],project=copy.deepcopy(healthy['healthy_project']))
        hard,proof=tradeoff_case(chosen[1]);selected.extend([healthy,hard]);checks.append(dict(task_id=hard['id'],**proof))
    controls=[]
    for case in selected:
        good=episode(case,control)
        bad_max=episode(case,lambda q:heuristic(q,'max_rating'))
        bad_r=episode(case,lambda q:heuristic(q,'min_resistance'))
        if not good['success'] or (case['id'].startswith('tradeoff-') and (bad_max['success'] or bad_r['success'])):
            raise ValueError('physical workflow controls failed')
        controls.append(dict(task_id=case['id'],expert=good,max_rating=bad_max,min_resistance=bad_r))
    from power_grid_env.qwen35_training import local_target
    for spec in targets.values():local_target(spec['path'],spec['sha256'])
    root.mkdir(parents=True)
    write_json(root/'cases.json',selected);write_json(root/'controls.json',controls);write_json(root/'physics.json',checks)
    # Generate TRAIN-only engineer action SFT candidates on all eight TRAIN
    # bases; no output labels or teacher plans for dev are exported to training.
    train_rows=[];training_episodes=[]
    for base in cases:
        if base['split']!='train':continue
        healthy=copy.deepcopy(base);healthy.update(id='healthy-'+base['id'],project=copy.deepcopy(base['healthy_project']))
        hard,_=tradeoff_case(base)
        for case in (healthy,hard):
            trace=episode(case,control)
            if not trace['success']:raise ValueError('positive SFT requires completed physical workflow')
            training_episodes.append(dict(case=case,trace=trace))
            for turn,event in enumerate(trace['events']):
                row=dict(split='train',family_sha256=case['family_sha256'],base_project_id=case['base_project_id'],task_id=case['id'],
                         turn=turn,messages=event['query']['messages'],answer=event['action'],source='scripted_public_tool_controller',
                         terminal_physics_replayed=True)
                row['id']=sha(row);train_rows.append(row)
    (root/'train-sft.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in train_rows))
    write_json(root/'train-episodes.json',training_episodes)
    result=dict(schema='power-grid-env.workflow-capacity-plan/v1',source=str(Path(source).resolve()),source_manifest_sha256=source_anchor,
                targets=targets,case_ids=[c['id'] for c in selected],splits=dict(Counter(c['split'] for c in selected)),
                families=len({c['family_sha256'] for c in selected}),base_networks=len({c['base_project_id'] for c in selected}),
                maximum_actions=MAX_ACTIONS,maximum_preview_studies=MAX_PREVIEWS,maximum_completion_tokens=128,
                nonprogress_repeat_limit=2,expert_successes=sum(r['expert']['success'] for r in controls),
                train_sft_records=len(train_rows),train_sft_episodes=len(training_episodes),train_sft_bases=len({r['base_project_id'] for r in train_rows}),
                train_sft_native_admission_passed=False,source_sha256=sources(),optimizer_run=False,
                scope='Four fixed solver-assisted synthetic constant-Z workflows,2 TRAIN calibration/2 new-family dev; test closed. '
                      'Scripted control is not model skill; same tools/budgets for both base models. Different profile from primitive or old exact/v2 scores.')
    write_json(root/'summary.json',result);seal(root)
    return result


def load_plan(root,anchor):
    if verify(root)!=anchor:raise ValueError('workflow plan manifest differs')
    p=read_json(Path(root)/'summary.json')
    if p.get('schema')!='power-grid-env.workflow-capacity-plan/v1' or p.get('source_sha256')!=sources():raise ValueError('workflow source/schema differs')
    cases=read_json(Path(root)/'cases.json')
    if [c['id'] for c in cases]!=p['case_ids']:raise ValueError('workflow case inventory differs')
    controls=read_json(Path(root)/'controls.json')
    if len(controls)!=len(cases) or p['expert_successes']!=len(cases):raise ValueError('complete successful control inventory required')
    for case,record in zip(cases,controls):
        if record['task_id']!=case['id']:raise ValueError('control task attribution differs')
        if sha(episode(case,control))!=sha(record['expert']):raise ValueError('public scripted control does not replay')
    return p,cases


def benchmark(plan_root,anchor,candidate,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh model workflow evaluation required')
    p,cases=load_plan(plan_root,anchor)
    if candidate not in p['targets']:raise ValueError('predeclared model target required')
    from power_grid_env.qwen35_training import local_target,load_native
    from benchmarks.engineering_sft_runtime_v2 import NativePolicy
    target=p['targets'][candidate]
    processor,model=load_native(local_target(target['path'],target['sha256']),4)
    native=NativePolicy(processor,model);root.mkdir(parents=True)
    observations=[];episodes=[]
    def policy(query):
        start=time.monotonic();raw=native(query)
        observations.append(dict(query_sha256=sha(query),completion=raw,seconds=time.monotonic()-start))
        return raw
    for case in cases:
        result=episode(case,policy);episodes.append(result)
        write_json(root/'partial.json',dict(complete=False,candidate=candidate,episodes=episodes))
        print(json.dumps(dict(candidate=candidate,task=case['id'],split=case['split'],success=result['success'],calls=result['calls'])),flush=True)
    counts={split:dict(successes=sum(r['success'] for r in episodes if r['split']==split),total=sum(r['split']==split for r in episodes)) for split in ('train','dev')}
    result=dict(schema='power-grid-env.workflow-capacity-evaluation/v1',complete=True,candidate=candidate,
                plan_manifest_sha256=anchor,target_sha256=target['sha256'],episodes=episodes,observations=observations,counts=counts,
                source_sha256=sources(),optimizer_run=False,test_evaluated=False,
                scope='Actual zero-shot base-model engineering episodes; no teacher fallback/format repair, physics terminal required. '
                      'Small scoped solver-assisted benchmark, not general superiority or a learned-agent result.')
    write_json(root/'summary.json',result);seal(root)
    return {k:v for k,v in result.items() if k not in ('episodes','observations')}


if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('plan')
    for key in ('source','manifest-sha256','targets','out'):a.add_argument('--'+key,required=True)
    a=sub.add_parser('run')
    for key in ('plan','manifest-sha256','candidate','out'):a.add_argument('--'+key,required=True)
    a=p.parse_args()
    print(json.dumps(plan(a.source,a.manifest_sha256,read_json(a.targets),a.out) if a.command=='plan' else benchmark(a.plan,a.manifest_sha256,a.candidate,a.out)))
