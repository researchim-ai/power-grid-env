"""Versioned editor-card action,argument-binding and readiness supervision.

Equipment is explicitly public on every editor frame. This differs from the
legacy inspect-only interface and must not be called a training-only gain.
"""
import copy
import json
import hashlib
from pathlib import Path
from benchmarks.engineering_scenarios import ScenarioEngine
from benchmarks.engineering_public_policy import plan_from_project
from benchmarks.engineering_grounding import scene_from_messages,resolve_action
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_learning_gate import strict_contract
from power_grid_env.study_artifacts import seal
from benchmarks import engineering_agent as legacy


class CardEpisode:
    def __init__(self,scenario):
        self.engine=ScenarioEngine(scenario);self.validated_revision=None

    def observe(self):
        e=self.engine;public=e.observation();task=e.public_task();revision=public['revision']
        # Pure projection adapter;these helper structures are NOT executed or
        # recorded tool calls. Public editor contract exposes current equipment.
        projection=[dict(role='system',content='public projection'),dict(role='user',content=json.dumps(public)),
            dict(role='assistant',content=json.dumps(dict(name='inspect_project',arguments={}))),
            dict(role='user',content=json.dumps(dict(ok=True,result=e.state['project'],observation=dict(revision=revision))))]
        scene,aliases=scene_from_messages(projection)
        schemas=copy.deepcopy(e.schemas())
        for item in schemas:
            name=item['function']['name'];p=item['function']['parameters']['properties']
            if name=='add_line':
                p['route_id']['enum']=[r['id'] for r in scene['routes']];p.pop('equipment_id',None)
                item['function']['parameters']['required']=[k for k in item['function']['parameters']['required'] if k!='equipment_id']
            if name=='add_load':p['load_id']['enum']=[l['id'] for l in scene['loads']]
        study=None
        if public['has_fresh_measurements']:
            raw=e.state['last_result'];study=dict(converged=raw.get('converged'),nodes=[],branches=[])
            for n in raw['nodes']:
                values=[v['voltage_pu'] for v in n['phases'].values()]
                study['nodes'].append(dict(id=aliases['nodes'][n['id']],voltage_pu_min=min(values),energized=n['energized']))
            for l in raw['branches']:
                if l['id'] in aliases['lines']:study['branches'].append(dict(id=aliases['lines'][l['id']],loading_pct=l['loading_pct']))
        return dict(profile='editor-card/v1',kind=task['kind'],revision=revision,calls_remaining=public['calls_remaining'],simulations_remaining=public['simulations_remaining'],
            goal=dict(minimum_voltage_pu=task['requirements']['minimum_voltage_pu'],maximum_branch_loading_pct=task['requirements']['maximum_branch_loading_pct'],all_loads_energized=True),
            validated=self.validated_revision==revision,has_fresh_measurements=public['has_fresh_measurements'],scene=scene,study=study,tools=schemas),aliases

    def apply(self,action):
        card,aliases=self.observe()
        try:raw=resolve_action(card['scene'],aliases,action,self.engine.state['revision'])
        except ValueError as exc:
            response=self.engine.apply('__invalid_action__',{});response['error']=str(exc);return response,None
        response=self.engine.apply(raw['name'],raw['arguments'])
        if raw['name']=='validate' and response.get('ok') is True:self.validated_revision=self.engine.state['revision']
        return response,raw

    def teacher(self):
        card,aliases=self.observe();e=self.engine
        plan=plan_from_project(e.public_task(),copy.deepcopy(e.state['project']))
        if e.scenario['kind']=='diagnose':raw=plan[-1] if card['has_fresh_measurements'] else dict(name='simulate',arguments={})
        else:
            edits=[a for a in plan if a['name'] in ('add_line','add_load','set_line_catalog','remove_line')]
            raw=edits[0] if edits else dict(name='validate',arguments={}) if not card['validated'] else dict(name='simulate',arguments={}) if not card['has_fresh_measurements'] else dict(name='submit',arguments={})
        args=copy.deepcopy(raw['arguments']);mapping={k:v for group in aliases.values() for k,v in group.items()}
        for key in ('equipment_id','load_id','route_id','location'):
            if key in args:
                if key=='equipment_id' and raw['name']=='add_line':args.pop(key)
                else:args[key]=mapping[args[key]]
        return dict(name=raw['name'],arguments=args)

    def readiness_label(self):
        card,_=self.observe();e=self.engine
        if e.scenario['kind']=='diagnose':return dict(study_ready=card['has_fresh_measurements'] and e.state['last_result'].get('converged') is True)
        ready=card['has_fresh_measurements'] and e.state['last_result'].get('converged') is True
        if ready:ready=not (legacy.structure_errors(e.state['project'],e.requirements)+legacy.physical_errors(e.state['last_result'],e.requirements))
        return dict(solution_ready=bool(ready))


def collect(scenario,variant='clean'):
    if scenario['split']!='train':raise ValueError('card training requires train scenario')
    if variant not in ('clean','measure_first'):raise ValueError('unknown card variant')
    e=CardEpisode(scenario);events=[];prefix=[]
    if variant=='measure_first':
        reply,raw=e.apply(dict(name='simulate',arguments={}));prefix.append(dict(source='scripted_probe',action=raw,response=reply))
    while not e.engine.state['finished'] and e.engine.state['calls']<24:
        card,_=e.observe();action=e.teacher();ready=e.readiness_label();response,raw=e.apply(action)
        events.append(dict(source='public_expert',card=card,action=action,resolved_public_action=raw,readiness=ready,response=response))
        if not response.get('ok'):break
    success=(e.engine.state['terminal'] or {}).get('success') is True
    return dict(task_id=scenario['id'],base_project_id=scenario['base_project_id'],family_sha256=scenario['family_sha256'],split='train',variant=variant,
        scripted_prefix=prefix,events=events,success=success,scope='Full-public-editor alias interface;different observability/action encoding from raw benchmark')


def binding_directive(action):
    a=action['arguments'];name=action['name']
    if name=='add_line':return f"Connect required route {a['route_id']} using catalog {a['catalog_id']}."
    if name=='add_load':return f"Place required load {a['load_id']}."
    if name=='set_line_catalog':return f"Use catalog {a['catalog_id']} for installed line {a['equipment_id']}."
    if name=='submit':return f"Report cause {a['cause']} at {a['location']}."
    raise ValueError('unsupported binding directive')


def export(train,manifest,out):
    data=ProceduralDataset(train,manifest,strict_contract());root=Path(out)
    if root.exists():raise ValueError('fresh card curriculum required')
    root.mkdir(parents=True)
    rows=[];episodes=[]
    for case in data.scenarios.values():
        for variant in ('clean','measure_first'):
            episode=collect(case,variant)
            if not episode['success']:raise ValueError('card expert cannot complete declared scenario')
            episodes.append(episode)
            for turn,event in enumerate(episode['events']):
                base=dict(task_id=case['id'],family_sha256=case['family_sha256'],base_project_id=case['base_project_id'],split='train',variant=variant,turn=turn,card=event['card'])
                rows.append(dict(**base,task='full_action',answer=event['action']))
                rows.append(dict(**base,task='readiness',answer=event['readiness']))
                if event['action']['arguments']:rows.append(dict(**base,task='bind_arguments',given_tool=event['action']['name'],directive=binding_directive(event['action']),answer=event['action']['arguments']))
    for filename,values in [('episodes.jsonl',episodes),('records.jsonl',rows)]:
        with (root/filename).open('w') as f:
            for row in values:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    r=dict(schema='power-grid-env.card-curriculum/v1',train_manifest_sha256=manifest,tasks=len(data.scenarios),episodes=len(episodes),records=len(rows),
        tasks_by_objective={k:sum(r['task']==k for r in rows) for k in ('full_action','bind_arguments','readiness')},
        readiness_false_after_fresh_study=sum(r['task']=='readiness' and r['card']['has_fresh_measurements'] and r['answer'].get('solution_ready') is False for r in rows),
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),eligible_for_gold_sft=False,
        scope='Solver-completed editor-card teaching candidates;new interface;requires new matched processor/learner/evaluation profile')
    write_json(root/'summary.json',r);seal(root);return r


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser()
    for name in ('train','manifest-sha256','out'):p.add_argument('--'+name,required=True)
    a=p.parse_args();print(json.dumps(export(a.train,a.manifest_sha256,a.out)))
