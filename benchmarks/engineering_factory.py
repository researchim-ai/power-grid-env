"""Admitted procedural data: grouped splits, independent physics, public recovery."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from benchmarks import engineering_agent as legacy
from benchmarks.grid_editor import solve
from benchmarks.engineering_scenarios import ScenarioEngine,generate_scenarios,family_pool
from benchmarks.engineering_reference import compare_reference
from benchmarks.engineering_recovery import RecoveryPolicy,VARIANTS,episode_messages
from benchmarks.engineering_public_policy import plan_from_project
from benchmarks.engineering_rollout import SYSTEM,public_reply
from benchmarks.engineering_protocol import RunContract,pack_action
from power_grid_env.study_artifacts import seal,verify,read_json


FACTORY_VARIANTS=VARIANTS+('alternate_plan',)


class DiversePublicPolicy(RecoveryPolicy):
    def __call__(self,messages):
        task=json.loads(messages[1]['content'])['task'];pairs=[]
        for i in range(2,len(messages),2):
            pairs.append((json.loads(messages[i]['content']),json.loads(messages[i+1]['content'])))
        inspections=[i for i,(a,r) in enumerate(pairs) if a['name']=='inspect_project' and r.get('ok') is True]
        if not inspections:return dict(name='inspect_project',arguments={})
        index=inspections[-1];plan=plan_from_project(task,pairs[index][1]['result'])
        edits=[copy.deepcopy(a) for a in plan if a['name'] in ('add_line','add_load','set_line_catalog')][::-1]
        for action in edits:
            if 'catalog_id' in action['arguments']:action['arguments']['catalog_id']='copper-2'
        plan=edits+[a for a in plan if a['name'] not in ('add_line','add_load','set_line_catalog')]
        following=pairs[index+1:]
        if any(i>=len(plan) or action!=plan[i] or response.get('ok') is not True for i,(action,response) in enumerate(following)):
            return dict(name='inspect_project',arguments={})
        if len(following)>=len(plan):raise ValueError('expert plan exhausted')
        return plan[len(following)]


def public_family(requirements):
    root=requirements['source_node'];edges=requirements['routes'];adj={root:set()}
    for edge in edges:
        a,b=edge['from_node'],edge['to_node'];adj.setdefault(a,set()).add(b);adj.setdefault(b,set()).add(a)
    seen=set()
    def tree(node,parent=None):
        if node in seen:raise ValueError('cycle or duplicate topology')
        seen.add(node);return '('+''.join(sorted(tree(n,node) for n in adj[node] if n!=parent))+')'
    signature=tree(root)
    if len(seen)!=len(adj) or len(edges)!=len(adj)-1:raise ValueError('connected tree required')
    return legacy.sha(signature)


def initial_prefix(engine,variant):
    req=engine.requirements
    if variant in ('clean','alternate_plan'):return []
    if variant=='simulate_first':return [dict(name='simulate',arguments={})]
    if variant=='unknown_equipment':return [dict(name='inspect_equipment',arguments=dict(equipment_id='unknown_device'))]
    if variant=='invalid_arguments':return [dict(name='inspect_project',arguments=dict(unexpected=True))]
    if variant=='validate_first':return [dict(name='validate',arguments={})]
    if variant=='partial_work':
        if engine.scenario['kind']=='assemble':return [dict(name='add_load',arguments=dict(load_id=req['loads'][0]['id']))]
        if engine.scenario['kind']=='repair':return [dict(name='set_line_catalog',arguments=dict(equipment_id=engine.state['project']['lines'][0]['id'],catalog_id='copper-2'))]
        return [dict(name='inspect_equipment',arguments=dict(equipment_id=req['source_node']))]
    raise ValueError('unknown prefix')


def collect_scenario(scenario,variant,training=True):
    if training and scenario['split']!='train':raise ValueError('heldout scenario cannot become training data')
    engine=ScenarioEngine(scenario);policy=DiversePublicPolicy() if variant=='alternate_plan' else RecoveryPolicy();prefix=initial_prefix(engine,variant)
    messages=[dict(role='system',content=SYSTEM),dict(role='user',content=json.dumps(engine.observation(),ensure_ascii=False))]
    events=[]
    for turn in range(24):
        source='scripted_perturbation' if turn<len(prefix) else 'public_expert'
        action=prefix[turn] if turn<len(prefix) else policy(copy.deepcopy(messages))
        response=public_reply(engine.apply(action['name'],action['arguments']))
        events.append(dict(turn=turn,source=source,action=action,response=response))
        messages.extend([dict(role='assistant',content=json.dumps(action,ensure_ascii=False,separators=(',',':'))),dict(role='user',content=json.dumps(response,ensure_ascii=False))])
        if engine.state['finished']:break
    terminal=engine.state['terminal'] or {}
    return dict(episode_id=legacy.sha(dict(scenario=scenario['id'],variant=variant,version='procedural-recovery-v1')),
        task_id=scenario['id'],base_project_id=scenario['base_project_id'],split=scenario['split'],family_sha256=scenario['family_sha256'],variant=variant,
        initial_messages=messages[:2],events=events,success=bool(terminal.get('success')),eligible_for_gold_sft=False)


def admission(scenario):
    if public_family(scenario['requirements'])!=scenario['family_sha256']:raise ValueError('public topology differs from declared family')
    healthy=solve(scenario['healthy_project']);healthy_audit=compare_reference(scenario['healthy_project'],healthy)
    if legacy.structure_errors(scenario['healthy_project'],scenario['requirements']) or legacy.physical_errors(healthy,scenario['requirements']):
        raise ValueError('healthy reference is not feasible')
    evidence=dict(healthy=healthy_audit,minimum_healthy_voltage_pu=min(p['voltage_pu'] for n in healthy['nodes'] for p in n['phases'].values()))
    if scenario['kind']!='assemble':
        initial=solve(scenario['project']);evidence['initial']=compare_reference(scenario['project'],initial)
        if not legacy.physical_errors(initial,scenario['requirements']):raise ValueError('injected fault has no observable physical violation')
    # Independent public policy proves there is an executable admissible continuation.
    episode=collect_scenario(scenario,'clean',training=False)
    if not episode['success']:raise ValueError('public expert cannot solve scenario')
    fresh=ScenarioEngine(scenario);early=fresh.apply('submit',dict(cause=scenario['truth']['cause'],location=scenario['truth']['location']) if scenario['kind']=='diagnose' else {})
    if not early['ok'] or early['result']['success']:raise ValueError('submit without fresh measurement accepted')
    if scenario['kind']=='diagnose':
        wrong=ScenarioEngine(scenario);wrong.apply('simulate',{})
        cause=next(c for c in legacy.BUGS if c!=scenario['truth']['cause'])
        response=wrong.apply('submit',dict(cause=cause,location=scenario['truth']['location']))
        if not response['ok'] or response['result']['success']:raise ValueError('wrong diagnosis accepted')
    else:
        fixed=copy.deepcopy(scenario);fixed['project']=fixed['healthy_project'];check=ScenarioEngine(fixed);check.apply('simulate',{})
        line=check.state['project']['lines'][0]
        check.apply('set_line_catalog',dict(equipment_id=line['id'],catalog_id='copper-1'))
        response=check.apply('submit',{})
        if response['result']['success']:raise ValueError('stale simulation accepted')
    evidence['negative_controls_passed']=True
    return evidence


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def build(out,seed=420,variants=2,counts=(8,3,3)):
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    scenarios=generate_scenarios(seed,variants,counts);groups={}
    for scenario in scenarios:groups.setdefault(scenario['base_project_id'],[]).append(scenario)
    split_map={f['family_sha256']:f['split'] for f in family_pool(seed,counts)}
    excluded=sorted(legacy.family_key(v[1]) for v in legacy.FAMILIES.values())
    split_spec=dict(schema='power-grid-env.family-split/v1',seed=seed,families=split_map,excluded_legacy_families=excluded)
    admitted=[];quarantine=[];records=[];episode_count=0;target_count=0
    for base_id,cases in groups.items():
        try:
            checks=[dict(task_id=c['id'],**admission(c)) for c in cases]
            episodes=[]
            if cases[0]['split']=='train':
                for case in cases:
                    for variant in FACTORY_VARIANTS:
                        episode=collect_scenario(case,variant)
                        if not episode['success']:raise ValueError('recovery continuation failed')
                        episodes.append(episode)
        except (ValueError,RuntimeError,ArithmeticError) as exc:
            quarantine.append(dict(base_project_id=base_id,split=cases[0]['split'],scenarios=len(cases),reason=str(exc)[:2000]))
            continue
        admitted.extend(cases);records.extend(checks)
        for episode in episodes:
            write_json(root/'train'/'episodes'/(episode['episode_id']+'.json'),episode)
            episode_count+=1;target_count+=sum(e['source']=='public_expert' for e in episode['events'])
    for folder,selected in [('train',[c for c in admitted if c['split']=='train']),('evaluation',[c for c in admitted if c['split']!='train'])]:
        write_json(root/folder/'split_manifest.json',split_spec)
        with (root/folder/'scenarios.jsonl').open('w') as f:
            for case in selected:f.write(json.dumps(case,ensure_ascii=False)+'\n')
        write_json(root/folder/'summary.json',dict(schema='power-grid-env.procedural-data/v1',role=folder,scenarios=len(selected),
            episodes=episode_count if folder=='train' else 0,expert_targets=target_count if folder=='train' else 0,
            variants=list(FACTORY_VARIANTS),admission_passed=not quarantine,
            reference_sha256=hashlib.sha256(Path(__file__).with_name('engineering_reference.py').read_bytes()).hexdigest(),
            family_count=len({c['family_sha256'] for c in selected}),base_projects=len({c['base_project_id'] for c in selected}),
            eligible_for_gold_sft=False,parameter_provenance='owner_synthetic_not_OEM',
            environment_sha256=hashlib.sha256(Path(__file__).with_name('engineering_scenarios.py').read_bytes()).hexdigest()))
        seal(root/folder)
    result=dict(schema='power-grid-env.data-admission/v1',seed=seed,parameter_variants=variants,requested_family_counts=dict(zip(('train','dev','test'),counts)),
        requested_scenarios=len(scenarios),admitted_scenarios=len(admitted),quarantined_base_projects=len(quarantine),
        release_passed=not quarantine,train_episodes=episode_count,expert_targets=target_count,
        train_manifest_sha256=verify(root/'train'),evaluation_manifest_sha256=verify(root/'evaluation'),
        reference_sha256=hashlib.sha256(Path(__file__).with_name('engineering_reference.py').read_bytes()).hexdigest(),
        generator_sha256=hashlib.sha256(Path(__file__).with_name('engineering_scenarios.py').read_bytes()).hexdigest(),
        producer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        quarantine=quarantine,checks=records,
        scope='Synthetic balanced constant-Z radial pilot,three fault classes;no VLM,field/OEM validation or trained-policy score')
    write_json(root/'summary.json',result);seal(root)
    return {k:v for k,v in result.items() if k not in ('checks','quarantine')}


class ProceduralDataset:
    def __init__(self,root,manifest,contract):
        self.root=Path(root)
        if verify(root)!=manifest:raise ValueError('owner train manifest differs')
        self.summary=read_json(self.root/'summary.json')
        if self.summary.get('schema')!='power-grid-env.procedural-data/v1' or self.summary.get('role')!='train' or self.summary.get('admission_passed') is not True:raise ValueError('admitted train package required')
        if self.summary.get('reference_sha256')!=hashlib.sha256(Path(__file__).with_name('engineering_reference.py').read_bytes()).hexdigest():raise ValueError('independent reference revision differs')
        if self.summary['environment_sha256']!=hashlib.sha256(Path(__file__).with_name('engineering_scenarios.py').read_bytes()).hexdigest():raise ValueError('environment revision differs')
        split=read_json(self.root/'split_manifest.json')
        cases=[json.loads(line) for line in (self.root/'scenarios.jsonl').read_text().splitlines()]
        self.scenarios={c['id']:c for c in cases}
        if len(self.scenarios)!=len(cases) or len(cases)!=self.summary['scenarios']:raise ValueError('scenario inventory differs')
        for case in cases:
            family=public_family(case['requirements'])
            if case['split']!='train' or family!=case['family_sha256'] or split['families'].get(family)!='train' or family in split['excluded_legacy_families']:
                raise ValueError('heldout or mismatched family in training')
            admission(case)
        self.episodes=[];self.targets=[];self.contract=contract;self.manifest_sha256=manifest
        for path in sorted((self.root/'episodes').glob('*.json')):
            episode=read_json(path);case=self.scenarios.get(episode['task_id'])
            if case is None or episode!=collect_scenario(case,episode['variant']):raise ValueError('public source/replay differs')
            if not episode['success']:raise ValueError('unfinished trajectory cannot train')
            index=len(self.episodes);self.episodes.append(episode)
            self.targets.extend((index,e['turn']) for e in episode['events'] if e['source']=='public_expert')
        variants=self.summary.get('variants')
        if not isinstance(variants,list) or set(variants)!=set(FACTORY_VARIANTS) or len(variants)!=len(FACTORY_VARIANTS):raise ValueError('recipe variants differ')
        expected={(c['id'],v) for c in cases for v in variants}
        actual={(e['task_id'],e['variant']) for e in self.episodes}
        if actual!=expected or len(self.episodes)!=len(expected) or len(self.episodes)!=self.summary['episodes'] or len(self.targets)!=self.summary['expert_targets']:
            raise ValueError('trajectory/target coverage differs')

    def __len__(self):return len(self.targets)

    def batch(self,processor,index):
        if type(index) is not int or not 0<=index<len(self):raise ValueError('target index outside train package')
        episode,turn=self.targets[index];episode=self.episodes[episode]
        messages,event=next((m,e) for m,e in episode_messages(episode) if e['turn']==turn)
        batch,meta=pack_action(processor,messages,event['action'],self.contract)
        return batch,dict(index=index,task_id=episode['task_id'],episode_id=episode['episode_id'],family_sha256=episode['family_sha256'],
            source='public_expert',turn=turn,**meta)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--seed',type=int,default=420);p.add_argument('--variants',type=int,default=2)
    a=p.parse_args();print(json.dumps(build(a.out,a.seed,a.variants)))
