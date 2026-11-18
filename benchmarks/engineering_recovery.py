"""Train-only scripted recovery prefixes and public-expert continuations.

Not student-collected DAgger; source attribution never promotes injected errors
into expert targets. Existing judge and physics remain unchanged.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from benchmarks import engineering_agent as env
from benchmarks.engineering_rollout import SYSTEM,parse_action,public_reply
from benchmarks.engineering_public_policy import plan_from_project
from benchmarks.engineering_protocol import RunContract,pack_action
from power_grid_env.study_artifacts import seal,verify,read_json

VARIANTS=('clean','simulate_first','unknown_equipment','invalid_arguments','validate_first','partial_work')


class RecoveryPolicy:
    def __call__(self,messages):
        task=json.loads(messages[1]['content'])['task']
        pairs=[]
        for i in range(2,len(messages),2):
            action=parse_action(messages[i]['content'],task['tools'])
            response=json.loads(messages[i+1]['content'])
            pairs.append((action,response))
        inspections=[i for i,(a,r) in enumerate(pairs) if a['name']=='inspect_project' and r.get('ok') is True]
        if not inspections:return dict(name='inspect_project',arguments={})
        index=inspections[-1];project=pairs[index][1]['result']
        plan=plan_from_project(copy.deepcopy(task),copy.deepcopy(project))
        following=pairs[index+1:]
        for i,(action,response) in enumerate(following):
            if i>=len(plan) or action!=plan[i] or response.get('ok') is not True:
                return dict(name='inspect_project',arguments={})
        if len(following)>=len(plan):raise ValueError('expert plan exhausted')
        return plan[len(following)]


def perturbation(task,variant):
    if variant=='clean':return []
    if variant=='simulate_first':return [dict(name='simulate',arguments={})]
    if variant=='unknown_equipment':return [dict(name='inspect_equipment',arguments={'equipment_id':'unknown_device'})]
    if variant=='invalid_arguments':return [dict(name='inspect_project',arguments={'unexpected':True})]
    if variant=='validate_first':return [dict(name='validate',arguments={})]
    if variant=='partial_work':
        if task['kind']=='assemble':return [dict(name='add_load',arguments={'load_id':'load1'})]
        if task['kind']=='repair':return [dict(name='set_line_catalog',arguments={'equipment_id':'line1','catalog_id':'copper-2'})]
        return [dict(name='inspect_equipment',arguments={'equipment_id':'n0'})]
    raise ValueError('unknown prefix variant')


def collect(identifier,variant,maximum_actions=12):
    task=env.task(identifier) # Deliberately rejects dev/test before any solver call.
    state=env.reset(identifier)
    messages=[dict(role='system',content=SYSTEM),dict(role='user',content=json.dumps(env.observation(state,task),ensure_ascii=False))]
    policy=RecoveryPolicy();prefix=perturbation(task,variant);events=[]
    for turn in range(maximum_actions):
        source='scripted_perturbation' if turn<len(prefix) else 'public_expert'
        action=prefix[turn] if turn<len(prefix) else policy(copy.deepcopy(messages))
        state,response=env.apply(state,action['name'],action['arguments'])
        response=public_reply(response)
        events.append(dict(turn=turn,source=source,action=action,response=response))
        messages.extend([dict(role='assistant',content=json.dumps(action,ensure_ascii=False,separators=(',',':'))),
                         dict(role='user',content=json.dumps(response,ensure_ascii=False))])
        if state['finished']:break
    terminal=state['terminal'] or {}
    return dict(episode_id=env.sha(dict(task=identifier,variant=variant,version='recovery-v1')),
        task_id=identifier,split='train',family_sha256=task['family_sha256'],variant=variant,
        initial_messages=messages[:2],events=events,success=bool(terminal.get('success')),
        scope='Scripted train-state perturbation/public-expert recovery;not autonomous model performance')


def episode_messages(episode):
    messages=copy.deepcopy(episode['initial_messages'])
    for event in episode['events']:
        yield copy.deepcopy(messages),event
        messages.extend([dict(role='assistant',content=json.dumps(event['action'],ensure_ascii=False,separators=(',',':'))),
            dict(role='user',content=json.dumps(event['response'],ensure_ascii=False))])


def replay(episode):
    task=env.task(episode['task_id'])
    if episode['split']!='train' or episode['family_sha256']!=task['family_sha256']:raise ValueError('train identity differs')
    original=collect(task['id'],episode['variant'])
    if original!=episode or not original['success']:raise ValueError('owner recovery replay differs or incomplete')


def export(out):
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    rows=[]
    for task in env.registry():
        if task['split']!='train':continue
        for variant in VARIANTS:
            episode=collect(task['id'],variant)
            if not episode['success']:raise ValueError('recovery expert failed')
            rows.append(episode)
    with (root/'episodes.jsonl').open('w') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    summary=dict(schema='power-grid-env.engineering-recovery/v1',split='train',episodes=len(rows),tasks=7,
        successes=sum(r['success'] for r in rows),expert_targets=sum(e['source']=='public_expert' for r in rows for e in r['events']),
        masked_perturbation_actions=sum(e['source']=='scripted_perturbation' for r in rows for e in r['events']),
        environment_sha256=hashlib.sha256(Path(env.__file__).read_bytes()).hexdigest(),
        public_expert_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        planner_sha256=hashlib.sha256(Path(__file__).with_name('engineering_public_policy.py').read_bytes()).hexdigest(),
        eligible_for_gold_sft=False,scope='Six paths per seven existing train tasks;not new topology diversity,student DAgger or trained-agent score')
    (root/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');seal(root)
    return summary


class RecoveryDataset:
    """Owner-pinned train-only next-expert-action batches, never prefix labels."""
    def __init__(self,corpus,manifest,contract):
        if verify(corpus)!=manifest:raise ValueError('owner recovery manifest differs')
        summary=read_json(Path(corpus)/'summary.json')
        if summary.get('schema')!='power-grid-env.engineering-recovery/v1' or summary.get('split')!='train':
            raise ValueError('train recovery corpus required')
        if summary.get('environment_sha256')!=hashlib.sha256(Path(env.__file__).read_bytes()).hexdigest():
            raise ValueError('environment revision differs')
        self.episodes=[json.loads(line) for line in (Path(corpus)/'episodes.jsonl').read_text().splitlines()]
        expected={(t['id'],v) for t in env.registry() if t['split']=='train' for v in VARIANTS}
        if len(self.episodes)!=len(expected) or {(e['task_id'],e['variant']) for e in self.episodes}!=expected:
            raise ValueError('complete train recovery coverage required')
        for episode in self.episodes:replay(episode)
        self.targets=[(i,event['turn']) for i,episode in enumerate(self.episodes) for event in episode['events'] if event['source']=='public_expert']
        masked=sum(event['source']=='scripted_perturbation' for episode in self.episodes for event in episode['events'])
        if len(self.targets)!=summary['expert_targets'] or masked!=summary['masked_perturbation_actions']:
            raise ValueError('expert/perturbation target denominator differs')
        self.contract=contract
        self.manifest_sha256=manifest

    def __len__(self):return len(self.targets)

    def batch(self,processor,index):
        if type(index) is not int or not 0<=index<len(self):raise ValueError('expert target index out of range')
        episode_index,turn=self.targets[index];episode=self.episodes[episode_index]
        messages,event=next((m,e) for m,e in episode_messages(episode) if e['turn']==turn)
        if event['source']!='public_expert':raise ValueError('perturbation cannot be supervised')
        batch,meta=pack_action(processor,messages,event['action'],self.contract)
        return batch,dict(episode_id=episode['episode_id'],task_id=episode['task_id'],turn=turn,
            source='public_expert',protocol=self.contract.protocol,contract_sha256=self.contract.fingerprint(),**meta)


def qualify(corpus,manifest,target_path,target_sha,out):
    if verify(corpus)!=manifest:raise ValueError('owner recovery manifest differs')
    summary=read_json(Path(corpus)/'summary.json')
    if summary.get('schema')!='power-grid-env.engineering-recovery/v1' or summary.get('split')!='train':raise ValueError('train recovery corpus required')
    if summary.get('environment_sha256')!=hashlib.sha256(Path(env.__file__).read_bytes()).hexdigest():raise ValueError('environment revision differs')
    episodes=[json.loads(line) for line in (Path(corpus)/'episodes.jsonl').read_text().splitlines()]
    expected={(t['id'],v) for t in env.registry() if t['split']=='train' for v in VARIANTS}
    if len(episodes)!=len(expected) or {(e['task_id'],e['variant']) for e in episodes}!=expected:raise ValueError('complete train recovery coverage required')
    for e in episodes:replay(e)
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    target=local_target(target_path,target_sha)
    processor=local_qwen35_processor(target['local_checkpoint'],target['config_sha256'])
    root=Path(out)
    if root.exists():raise ValueError('fresh qualification output required')
    rows=[]
    for protocol in ('json-action-v1','qwen-native-v1'):
        contract=RunContract(protocol=protocol)
        dataset=RecoveryDataset(corpus,manifest,contract)
        for index in range(len(dataset)):
            batch,meta=dataset.batch(processor,index)
            rows.append(meta)
    result=dict(schema='power-grid-env.engineering-recovery-qualification/v1',corpus_manifest_sha256=manifest,target_sha256=target_sha,
        episodes=len(episodes),targets_per_protocol=summary['expert_targets'],rows=rows,
        protocol_source_sha256=hashlib.sha256(Path(__file__).with_name('engineering_protocol.py').read_bytes()).hexdigest(),
        weights_loaded=False,eligible_for_gold_sft=False,scope='Real processor prefix/assistant mask and public replay qualification only;no native student accuracy or SFT run')
    root.mkdir(parents=True,exist_ok=False);(root/'summary.json').write_text(json.dumps(result,indent=2)+'\n');seal(root)
    return {k:v for k,v in result.items() if k!='rows'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--qualify',action='store_true')
    p.add_argument('--corpus');p.add_argument('--manifest-sha256');p.add_argument('--target');p.add_argument('--target-sha256')
    a=p.parse_args()
    if a.qualify:
        if not all((a.corpus,a.manifest_sha256,a.target,a.target_sha256)):p.error('corpus and target owner anchors required')
        result=qualify(a.corpus,a.manifest_sha256,a.target,a.target_sha256,a.out)
    else:result=export(a.out)
    print(json.dumps(result))

if __name__=='__main__':main()
