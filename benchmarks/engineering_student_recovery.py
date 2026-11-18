"""Train-only corrections in states actually visited by a recorded candidate.

No rollout is assumed successful. Every prefix is replayed, public expert
continuations execute in the remaining original budget, and failures stay in
the denominator. This exports candidate corrections, not an automatic SFT run.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_learning_gate import strict_contract
from benchmarks.engineering_learning_compare import audit
from benchmarks.engineering_rollout import SYSTEM,parse_action,public_reply
from benchmarks.engineering_public_policy import plan_from_project
from benchmarks.engineering_scenarios import ScenarioEngine
from power_grid_env.study_artifacts import read_json,verify,seal


class PublicCorrector:
    """Replan from the latest successful public inspection, ignoring older calls."""
    def __call__(self,messages):
        task=json.loads(messages[1]['content'])['task'];pairs=[];last=None
        for i in range(2,len(messages),2):
            try:action=parse_action(messages[i]['content'],task['tools'])
            except (ValueError,TypeError):action=None
            response=json.loads(messages[i+1]['content']);pairs.append((action,response))
            if action and action['name']=='inspect_project' and response.get('ok') is True:last=len(pairs)-1
        inspect=dict(name='inspect_project',arguments={})
        if last is None:return inspect
        plan=plan_from_project(copy.deepcopy(task),copy.deepcopy(pairs[last][1]['result']))
        following=pairs[last+1:]
        if any(i>=len(plan) or a!=plan[i] or r.get('ok') is not True for i,(a,r) in enumerate(following)):return inspect
        if len(following)>=len(plan):raise ValueError('public correction plan exhausted')
        return plan[len(following)]


def correction(scenario,recorded_turns,prefix_calls):
    if scenario['split']!='train':raise ValueError('student correction requires train scenario')
    if type(prefix_calls) is not int or not 1<=prefix_calls<=len(recorded_turns):raise ValueError('nonempty visited prefix required')
    engine=ScenarioEngine(scenario);messages=[dict(role='system',content=SYSTEM),dict(role='user',content=json.dumps(engine.observation(),ensure_ascii=False))];events=[]
    for t in recorded_turns[:prefix_calls]:
        if engine.state['finished'] or 'completion' not in t:raise ValueError('terminal/transport failure is not an actionable visited prefix')
        raw=t['completion']
        try:action=parse_action(raw,engine.public_task()['tools'])
        except (ValueError,TypeError):action=None
        response=public_reply(engine.apply(action['name'],action['arguments']) if action else engine.apply('__invalid_action__',{}))
        if response!=t['response'] or action!=t.get('action'):raise ValueError('candidate prefix replay differs')
        events.append(dict(turn=len(events),source='student',completion=raw,action=action,response=response))
        messages.extend([dict(role='assistant',content=raw if isinstance(raw,str) else ''),dict(role='user',content=json.dumps(response,ensure_ascii=False))])
    if engine.state['finished'] or engine.state['calls']>=24:return None
    teacher=PublicCorrector();failure=None
    while engine.state['calls']<24 and not engine.state['finished']:
        try:action=teacher(copy.deepcopy(messages))
        except (ValueError,RuntimeError) as exc:failure=str(exc)[:500];break
        raw=json.dumps(action,ensure_ascii=False,separators=(',',':'));response=public_reply(engine.apply(action['name'],action['arguments']))
        events.append(dict(turn=len(events),source='public_expert',completion=raw,action=action,response=response))
        messages.extend([dict(role='assistant',content=raw),dict(role='user',content=json.dumps(response,ensure_ascii=False))])
    success=(engine.state['terminal'] or {}).get('success') is True
    return dict(schema='power-grid-env.student-correction-episode/v1',task_id=scenario['id'],split='train',family_sha256=scenario['family_sha256'],base_project_id=scenario['base_project_id'],
        prefix_calls=prefix_calls,initial_messages=messages[:2],events=events,success=success,teacher_failure=failure,
        expert_targets=sum(e['source']=='public_expert' for e in events) if success else 0,eligible_for_gold_sft=False)


def export(train,train_manifest,suite,suite_manifest,student_eval,student_manifest,out):
    from benchmarks.engineering_learning_gate import load_suite
    root=Path(out)
    if root.exists():raise ValueError('fresh student correction output required')
    data=ProceduralDataset(train,train_manifest,strict_contract());selected=load_suite(data,suite,suite_manifest)
    cases=[data.scenarios[t] for t in selected['task_ids']]
    report,rows=audit(student_eval,student_manifest,cases,strict_contract())
    expected=dict(role='tiny_train',corpus_manifest_sha256=train_manifest,suite_manifest_sha256=suite_manifest)
    if report['source']!=expected or report['candidate'].get('kind')!='native_model':raise ValueError('actual train model rollout required;no control or heldout labels')
    root.mkdir(parents=True,exist_ok=False);attempts=[];episodes=[]
    for case,row in zip(cases,rows):
        for n,t in enumerate(row['turns'],1):
            if 'completion' not in t:
                attempts.append(dict(task_id=case['id'],prefix_calls=n-1,status='transport_failure_no_action',success=False));continue
            result=correction(case,row['turns'],n)
            if result is None:
                attempts.append(dict(task_id=case['id'],prefix_calls=n,status='terminal_or_budget_exhausted',success=False));continue
            identity=hashlib.sha256(json.dumps(dict(student=student_manifest,task=case['id'],prefix=n),sort_keys=True).encode()).hexdigest()
            result['episode_id']=identity;write_json(root/'attempts'/(identity+'.json'),result)
            attempts.append(dict(task_id=case['id'],prefix_calls=n,episode_id=identity,status='corrected' if result['success'] else 'continuation_failed',success=result['success']))
            if result['success']:episodes.append(result)
    with (root/'episodes.jsonl').open('w') as f:
        for e in episodes:f.write(json.dumps(e,ensure_ascii=False)+'\n')
    result=dict(schema='power-grid-env.student-corrections/v1',train_manifest_sha256=train_manifest,suite_manifest_sha256=suite_manifest,student_evaluation_manifest_sha256=student_manifest,
        source_candidate=report['candidate'],tasks=len(cases),source_turns=sum(len(r['turns']) for r in rows),
        visited_prefixes=sum(a['status']!='transport_failure_no_action' for a in attempts),successful_corrections=len(episodes),
        expert_targets=sum(e['expert_targets'] for e in episodes),attempts=attempts,teacher_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        eligible_for_gold_sft=False,scope='Actual train candidate visited states;public-expert continuations only;not learned recovery or an admitted trainer dataset')
    write_json(root/'summary.json',result);seal(root);return result


def expert_messages(episode):
    if episode.get('split')!='train' or episode.get('success') is not True:raise ValueError('successful train correction required')
    messages=copy.deepcopy(episode['initial_messages'])
    for event in episode['events']:
        if event['source']=='public_expert':yield copy.deepcopy(messages),event['action']
        elif event['source']!='student':raise ValueError('unknown correction source')
        messages.extend([dict(role='assistant',content=event['completion'] if isinstance(event['completion'],str) else ''),dict(role='user',content=json.dumps(event['response'],ensure_ascii=False))])


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for flag in ('train','train-manifest-sha256','suite','suite-manifest-sha256','student-evaluation','student-manifest-sha256','out'):p.add_argument('--'+flag,required=True)
    a=p.parse_args();print(json.dumps(export(a.train,a.train_manifest_sha256,a.suite,a.suite_manifest_sha256,a.student_evaluation,a.student_manifest_sha256,a.out)))
