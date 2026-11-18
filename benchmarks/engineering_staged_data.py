"""Replayed staged-operation,conditional-argument and measurement targets.

Teacher values are outside prompts. Failed controllers contribute assessments,
never positive action targets. No optimizer updates are performed here.
"""
import argparse,json
from pathlib import Path
from collections import Counter
from benchmarks.engineering_staged_agent import operation_request,argument_request,public_feedback
from benchmarks.engineering_constraint_tasks import assess,messages as assessment_messages
from benchmarks.engineering_replacement_challenges import ReplacementEpisode
from benchmarks.engineering_skill_tasks import identity,score
from benchmarks.engineering_value_loss import payload_mask
from benchmarks.engineering_factory import write_json
from benchmarks import engineering_agent as legacy
from power_grid_env.study_artifacts import verify,read_json,seal


def targets(record):
    case=record['case'];result=[]
    if case['split']!='train':raise ValueError('staged teaching is train-only')
    for source in ('expert','rebuild_all'):
        e=ReplacementEpisode(case);feedback=None;trace=record[source]
        for turn,event in enumerate(trace['events']):
            card,_=e.observe()
            if card!=event['card']:raise ValueError('staged source card does not replay')
            label=assess(card)
            if label['study_ready'] and label['measurement_feasible']!=(not legacy.physical_errors(e.engine.state['last_result'],e.engine.requirements)):
                raise ValueError('public measurement labels disagree with physical judge')
            if label['equipment_budget_ok']!=(e.edits<=1 and len(e.changed_routes)<=1):raise ValueError('public equipment budget label disagrees')
            base=dict(split='train',task_id=case['id'],base_project_id=case['base_project_id'],family_sha256=case['family_sha256'],source=source,turn=turn)
            result.append(dict(**base,skill='measurement',messages=assessment_messages(card),answer=label))
            if source=='expert':
                a=event['action'];result.append(dict(**base,skill='operation',messages=operation_request(card,feedback)['messages'],answer=dict(operation=a['name'])))
                if a['arguments']:
                    result.append(dict(**base,skill='conditional_arguments',messages=argument_request(card,a['name'],feedback)['messages'],answer=a['arguments']))
            reply,_=e.apply(event['action'])
            if reply!=event['response']:raise ValueError('staged source response does not replay')
            feedback=public_feedback(reply,event['action'])
        if e.engine.state['terminal']!=trace['terminal']:raise ValueError('staged source terminal differs')
    return result


def pack(processor,row):
    import torch
    m=row['messages'];answer=row['answer'];settings=dict(tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)
    prompt=processor.apply_chat_template(m,add_generation_prompt=True,**settings)
    full=processor.apply_chat_template(m+[dict(role='assistant',content=json.dumps(answer,separators=(',',':')))],add_generation_prompt=False,**settings)
    n,total=prompt['input_ids'].shape[1],full['input_ids'].shape[1]
    if not n<total<=8192 or total-n>128 or not torch.equal(prompt['input_ids'],full['input_ids'][:,:n]):raise ValueError('staged prefix/budget differs')
    if 'pixel_values' in full:raise ValueError('this staged profile is explicitly card-text')
    full['labels']=full['input_ids'].clone();full['labels'][:,:n]=-100;ids=full['input_ids'][full['labels']!=-100]
    if not score(processor.tokenizer.decode(ids,skip_special_tokens=True),answer)['exact']:raise ValueError('staged actual labels differ')
    mask=payload_mask(processor.tokenizer,ids,answer)
    return full,dict(skill=row['skill'],prompt_tokens=n,label_tokens=total-n,payload_tokens=sum(mask))


def export(source,anchor,target,target_sha,out):
    if verify(source)!=anchor:raise ValueError('owner v2 audit differs')
    receipt=read_json(Path(source)/'summary.json')
    if receipt.get('schema')!='power-grid-env.replacement-challenge-audit/v2':raise ValueError('v2 replay evidence required')
    for f,sha in receipt['source_sha256'].items():
        if identity(Path(__file__).with_name(f))!=sha:raise ValueError('v2 replay implementation changed')
    root=Path(out)
    if root.exists():raise ValueError('fresh staged corpus required')
    rows=[]
    for p in sorted((Path(source)/'cases').glob('*.json')):rows.extend(targets(read_json(p)))
    if not rows or any(r['source']!='expert' and r['skill']!='measurement' for r in rows):raise ValueError('nonempty expert-action-only corpus required')
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    import torch
    torch.set_num_threads(4);t=local_target(target,target_sha);processor=local_qwen35_processor(t['local_checkpoint'],t['config_sha256'])
    checks=[];failures=[]
    for i,row in enumerate(rows):
        try:_,meta=pack(processor,row);checks.append(dict(index=i,**meta))
        except (ValueError,RuntimeError) as exc:failures.append(dict(index=i,error=str(exc)[:500]))
        if (i+1)%128==0:print(json.dumps(dict(checked=i+1,total=len(rows),failures=len(failures))),flush=True)
    root.mkdir(parents=True)
    with (root/'records.jsonl').open('w') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    summary=dict(schema='power-grid-env.staged-teaching-data/v1',source_manifest_sha256=anchor,target_sha256=target_sha,records=len(rows),
        skills=dict(Counter(r['skill'] for r in rows)),cases=len({r['task_id'] for r in rows}),base_networks=len({r['base_project_id'] for r in rows}),
        admission_passed=not failures,checked_targets=len(checks),failures=failures,native_rows=checks,
        maximum_prompt_tokens=max((x['prompt_tokens'] for x in checks),default=0),failed_actions_supervised=0,
        source_sha256={f:identity(Path(__file__).with_name(f)) for f in ('engineering_staged_data.py','engineering_staged_agent.py','engineering_constraint_tasks.py','engineering_value_loss.py')},
        eligible_for_gold_sft=False,optimizer_run=False,scope='Fully replayed public text staged/measurement targets with native labels and payload masks;not visual topology training,learned workflow or industrial qualification')
    write_json(root/'summary.json',summary);seal(root);return {k:v for k,v in summary.items() if k!='native_rows'}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for f in ('source','manifest-sha256','target','target-sha256','out'):p.add_argument('--'+f,required=True)
    a=p.parse_args();print(json.dumps(export(a.source,a.manifest_sha256,a.target,a.target_sha256,a.out)))
