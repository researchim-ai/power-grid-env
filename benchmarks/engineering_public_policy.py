"""Deterministic public-observation control, not a neural or private-oracle agent."""
import copy
import json


def route_line(project,route):
    return next((l for l in project['lines'] if {l['from_node'],l['to_node']}=={route['from_node'],route['to_node']}),None)


def plan_from_project(task,project):
    req=task['requirements'];catalog=req['catalog'];kind=task['kind']
    plan=[];diagnoses=[];used={x['id'] for k in ('nodes','lines','loads') for x in project[k]}
    for i,route in enumerate(req['routes'],1):
        line=route_line(project,route)
        if line is None:
            diagnoses.append(dict(cause='missing_connection',location=route['id']))
            candidate='engineer_line'+str(i)
            while candidate in used:candidate+='x'
            used.add(candidate)
            plan.append(dict(name='add_line',arguments=dict(route_id=route['id'],equipment_id=candidate,catalog_id='copper-1')))
            continue
        electrical=any(all(line[k]==c[k] for k in ('r_ohm_km','x_ohm_km')) for c in catalog.values())
        rating=any(line['rating_a']==c['rating_a'] for c in catalog.values())
        if not electrical:diagnoses.append(dict(cause='impedance',location=line['id']))
        if not rating:diagnoses.append(dict(cause='ampacity',location=line['id']))
        if not any(all(line[k]==c[k] for k in ('r_ohm_km','x_ohm_km','rating_a')) for c in catalog.values()):
            plan.append(dict(name='set_line_catalog',arguments=dict(equipment_id=line['id'],catalog_id='copper-1')))
    if kind=='diagnose':
        if len(diagnoses)!=1:raise ValueError('public observations do not identify exactly one authored fault')
        return [dict(name='simulate',arguments={}),dict(name='submit',arguments=diagnoses[0])]
    if kind not in ('assemble','repair'):raise ValueError('unsupported bounded task')
    for load in req['loads']:
        if not any(l['id']==load['id'] for l in project['loads']):
            plan.append(dict(name='add_load',arguments=dict(load_id=load['id'])))
    return plan+[dict(name=name,arguments={}) for name in ('validate','simulate','submit')]


class PublicPolicy:
    """Only caller-supplied public messages; no environment/evaluator import."""
    def __init__(self, public_view='raw'):
        self.public_view=public_view
        self.identity=dict(public_view=public_view,type='deterministic_public_catalog_engineer',scope='Authored passive PF control,not a learned agent or independent physics oracle')

    def __call__(self,messages):
        if self.public_view=='compact-pf-v1':
            from benchmarks.engineering_observations import referenced_messages
            messages=referenced_messages(messages)
        initial=json.loads(messages[1]['content']);task=initial['task']
        if len(messages)==2:
            return json.dumps(dict(name='inspect_project',arguments={}))
        response=json.loads(messages[-1]['content'])
        if response.get('ok') is not True:raise ValueError('public tool failed')
        inspected=json.loads(messages[3]['content'])
        if inspected.get('ok') is not True:raise ValueError('project inspection failed')
        plan=plan_from_project(copy.deepcopy(task),copy.deepcopy(inspected['result']))
        completed=(len(messages)-2)//2-1
        if not 0<=completed<len(plan):raise ValueError('public plan exhausted')
        return json.dumps(plan[completed])


def main():
    import argparse
    import hashlib
    from pathlib import Path
    from benchmarks.engineering_rollout import evaluate
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',required=True)
    parser.add_argument('--split',choices=['train','dev'],default='dev')
    parser.add_argument('--turns',type=int,default=12)
    parser.add_argument('--public-view',choices=['raw','compact-pf-v1'],default='raw')
    parser.add_argument('--repeat-limit',type=int,default=0)
    args=parser.parse_args()
    policy=PublicPolicy(args.public_view)
    policy.identity['observation_view_sha256']=hashlib.sha256(Path(__file__).with_name('engineering_observations.py').read_bytes()).hexdigest()
    policy.identity['policy_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    print(json.dumps(evaluate(policy,args.out,args.split,args.turns,args.repeat_limit)))


if __name__=='__main__':main()
