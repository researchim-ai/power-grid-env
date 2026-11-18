"""Frozen train memorization suites and standalone procedural rollouts.

Candidate gets public messages, never the expert, private scenario or target.
A tiny train success is a wiring/learnability gate, not generalization evidence.
"""
import argparse
import copy
from collections import Counter
import hashlib
import json
import random
import time
from pathlib import Path
from benchmarks.engineering_factory import ProceduralDataset,public_family,write_json
from benchmarks.engineering_protocol import RunContract
from benchmarks.engineering_rollout import SYSTEM,parse_action,public_reply
from benchmarks.engineering_scenarios import ScenarioEngine
from power_grid_env.study_artifacts import verify,read_json,seal


def strict_contract():
    return RunContract(protocol='qwen-native-strict-v1',maximum_actions=24,context_tokens=24576)


def select_suite(data,size=8):
    if type(size) is not int or not 8<=size<=16:raise ValueError('tiny suite requires8..16 episodes')
    candidates=[e for e in data.episodes if e['variant']=='clean']
    chosen=[];families=Counter();bases=set();kinds=Counter()
    for index in range(size):
        kind=('assemble','diagnose','repair')[index%3]
        options=[e for e in candidates if data.scenarios[e['task_id']]['kind']==kind and e['base_project_id'] not in bases]
        if not options:raise ValueError('insufficient independent train base networks for tiny suite')
        episode=min(options,key=lambda e:(families[e['family_sha256']],e['family_sha256'],e['task_id']))
        chosen.append(episode);bases.add(episode['base_project_id']);families[episode['family_sha256']]+=1;kinds[kind]+=1
    ids={e['episode_id'] for e in chosen}
    targets=[i for i,(e,t) in enumerate(data.targets) if data.episodes[e]['episode_id'] in ids]
    return dict(episode_ids=[e['episode_id'] for e in chosen],task_ids=[e['task_id'] for e in chosen],target_indices=targets,
        kinds=dict(kinds),families=dict(families),base_networks=len(bases))


def create_suite(train,manifest,out,size=8):
    root=Path(out)
    if root.exists():raise ValueError('fresh suite output required')
    data=ProceduralDataset(train,manifest,strict_contract());selected=select_suite(data,size)
    record=dict(schema='power-grid-env.tiny-learning-suite/v1',train_manifest_sha256=manifest,
        contract=strict_contract().model_dump(),selection='clean;kind round-robin;unused base;least represented family;lexical ties',
        size=size,**selected,generalization_claim=False)
    write_json(root/'summary.json',record);seal(root);return record


def load_suite(data,root,anchor):
    if verify(root)!=anchor:raise ValueError('owner tiny suite manifest differs')
    s=read_json(Path(root)/'summary.json')
    if s.get('schema')!='power-grid-env.tiny-learning-suite/v1' or s.get('train_manifest_sha256')!=data.manifest_sha256 or s.get('contract')!=data.contract.model_dump():
        raise ValueError('tiny suite source or contract differs')
    selected=select_suite(data,s.get('size'))
    if any(s.get(k)!=v for k,v in selected.items()):raise ValueError('tiny suite selection/target coverage differs')
    return s


def epoch_schedule(suite,epochs,seed=42):
    if type(epochs) is not int or not 1<=epochs<=100:raise ValueError('epochs must be1..100')
    rng=random.Random(seed);order=[]
    for _ in range(epochs):
        indices=list(suite['target_indices']);rng.shuffle(indices);order.extend(indices)
    return order


def rollout(scenario,policy,contract):
    if contract.maximum_actions>24:raise ValueError('scenario engine supports at most24 actions')
    engine=ScenarioEngine(scenario)
    public=engine.observation();messages=[dict(role='system',content=SYSTEM),dict(role='user',content=json.dumps(public,ensure_ascii=False))]
    turns=[];reason='turn_budget';last_key=None;repeated=0
    for turn in range(contract.maximum_actions):
        tick=time.monotonic()
        try:generated=policy(copy.deepcopy(messages))
        except Exception as exc:
            turns.append(dict(turn=turn,error=type(exc).__name__,error_code=getattr(exc,'code','transport_or_policy_failure'),detail=str(exc)[:500],elapsed_s=time.monotonic()-tick));reason='policy_failure';break
        raw=generated.get('text') if isinstance(generated,dict) else generated
        row=dict(turn=turn,completion=raw,generation={k:v for k,v in generated.items() if k!='text'} if isinstance(generated,dict) else {},elapsed_s=time.monotonic()-tick)
        try:action=parse_action(raw,public['task']['tools'])
        except (ValueError,TypeError) as exc:
            row['protocol_error']=str(exc);response=engine.apply('__invalid_action__',{})
        else:
            response=engine.apply(action['name'],action['arguments']);row['action']=action
        row['response']=public_reply(response);turns.append(row)
        messages.extend([dict(role='assistant',content=raw if isinstance(raw,str) else ''),dict(role='user',content=json.dumps(row['response'],ensure_ascii=False))])
        if engine.state['finished']:
            reason='submitted' if engine.state['terminal'] and 'call_budget' not in engine.state['terminal'].get('violations',[]) else 'call_budget';break
        if contract.repeat_limit and row.get('action'):
            payload={k:v for k,v in response.items() if k!='observation'}
            key=json.dumps(dict(action=row['action'],payload=payload,revision=engine.state['revision']),sort_keys=True,separators=(',',':'))
            repeated=repeated+1 if last_key==key else 1;last_key=key
            if repeated>=contract.repeat_limit:reason='repeated_public_observation';break
    terminal=engine.state['terminal'] or {}
    return dict(task_id=scenario['id'],split=scenario['split'],family_sha256=scenario['family_sha256'],base_project_id=scenario['base_project_id'],kind=scenario['kind'],
        success=terminal.get('success') is True,reward=terminal.get('reward',0.),stop_reason=reason,calls=engine.state['calls'],simulations=engine.state['simulations'],
        final_revision=engine.state['revision'],terminal=terminal,turns=turns,eligible_for_gold_sft=False)


def load_evaluation(root,anchor,split):
    if split not in ('dev','test'):raise ValueError('evaluation role requires dev/test')
    if verify(root)!=anchor:raise ValueError('owner evaluation manifest differs')
    r=read_json(Path(root)/'summary.json')
    if r.get('schema')!='power-grid-env.procedural-data/v1' or r.get('role')!='evaluation' or r.get('admission_passed') is not True:raise ValueError('admitted evaluation package required')
    for field,file in [('reference_sha256','engineering_reference.py'),('environment_sha256','engineering_scenarios.py')]:
        if r.get(field)!=hashlib.sha256(Path(__file__).with_name(file).read_bytes()).hexdigest():raise ValueError('evaluation source differs')
    spec=read_json(Path(root)/'split_manifest.json');cases=[json.loads(x) for x in (Path(root)/'scenarios.jsonl').read_text().splitlines()]
    if len(cases)!=r['scenarios'] or len({c['id'] for c in cases})!=len(cases):raise ValueError('evaluation inventory differs')
    for c in cases:
        family=public_family(c['requirements'])
        if c['split'] not in ('dev','test') or family!=c['family_sha256'] or spec['families'].get(family)!=c['split'] or family in spec['excluded_legacy_families']:raise ValueError('evaluation family/split differs')
    selected=[c for c in cases if c['split']==split]
    if not selected:raise ValueError('empty evaluation split')
    return sorted(selected,key=lambda c:c['id'])


def evaluate_cases(cases,policy,out,contract,source,candidate):
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    identifiers=[c['id'] for c in cases]
    if not cases or len(set(identifiers))!=len(cases):raise ValueError('nonempty unique evaluation inventory required')
    rows=[]
    for case in cases:
        if hasattr(policy,'begin_episode'):policy.begin_episode()
        row=rollout(case,policy,contract);rows.append(row);write_json(root/(case['id']+'.json'),row)
        print(json.dumps(dict(evaluated=len(rows),total=len(cases),kind=case['kind'],success=row['success'],stop=row['stop_reason'])),flush=True)
    def grouped(key):
        return {value:dict(tasks=sum(r[key]==value for r in rows),successes=sum(r['success'] for r in rows if r[key]==value)) for value in sorted({r[key] for r in rows})}
    report=dict(schema='power-grid-env.procedural-standalone-eval/v1',source=source,candidate=candidate,contract=contract.model_dump(),contract_sha256=contract.fingerprint(),
        tasks=len(rows),task_ids=identifiers,successes=sum(r['success'] for r in rows),accuracy=sum(r['success'] for r in rows)/len(rows),
        kinds=grouped('kind'),families=grouped('family_sha256'),base_networks=len({r['base_project_id'] for r in rows}),
        protocol_errors=sum('protocol_error' in t for r in rows for t in r['turns']),policy_failures=sum(r['stop_reason']=='policy_failure' for r in rows),
        edits=sum(t.get('action',{}).get('name') in ('add_line','add_load','set_line_catalog','remove_line') and t.get('response',{}).get('ok') is True for r in rows for t in r['turns']),
        submits=sum(t.get('action',{}).get('name')=='submit' for r in rows for t in r['turns']),
        evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),complete=True,
        scope='Standalone bounded procedural episodes;all failures count;train tiny suite is not heldout generalization')
    report['tiny_learning_gate_passed']=(source.get('role')=='tiny_train' and candidate.get('kind')=='native_model' and report['successes']==len(rows))
    write_json(root/'summary.json',report);seal(root);return report


def model_command(python,target,target_sha,contract,adapter=None,adapter_manifest=None):
    if bool(adapter)!=bool(adapter_manifest):raise ValueError('adapter requires owner anchor')
    command=[python,'-m','benchmarks.engineering_protocol_policy','--target',str(target),'--target-sha256',target_sha,'--protocol',contract.protocol,
        '--public-view',contract.public_view,'--action-tokens',str(contract.action_tokens),'--context-tokens',str(contract.context_tokens),
        '--maximum-actions',str(contract.maximum_actions),'--repeat-limit',str(contract.repeat_limit)]
    if adapter:command.extend(['--experimental-v2-adapter',str(adapter),'--adapter-manifest-sha256',adapter_manifest])
    return command


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--train',required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--out',required=True);p.add_argument('--size',type=int,default=8)
    a=p.parse_args();print(json.dumps(create_suite(a.train,a.manifest_sha256,a.out,a.size)))
