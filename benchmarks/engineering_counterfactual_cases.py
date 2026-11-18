"""Physically replayed matched-stage feasible/overloaded train observations.

Revision,budgets,freshness,goal and previous feedback match. Only physical
passports/measurements differ;the model cannot label both states by stage count.
"""
import argparse,copy,json
from pathlib import Path
from benchmarks.engineering_staged_agent import run,catalog_control,operation_request
from benchmarks.engineering_constraint_tasks import assess,messages as assessment_messages
from benchmarks.engineering_replacement_challenges import ReplacementEpisode
from benchmarks.engineering_reference import compare_reference
from benchmarks.grid_editor import solve
from benchmarks.engineering_staged_data import pack
from benchmarks.engineering_skill_tasks import identity
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,read_json,seal


def pair(case):
    healthy=copy.deepcopy(case);healthy['id']='initially-feasible-'+case['id'];healthy['project']=copy.deepcopy(case['healthy_project'])
    rows=[];controls=[];snapshots=[]
    for variant,c in [('overloaded',case),('feasible',healthy)]:
        control=run(c,catalog_control)
        if not control['success']:raise ValueError('paired public control failed')
        controls.append(control);e=ReplacementEpisode(c)
        before=compare_reference(c['project'],solve(c['project']))
        e.apply(dict(name='validate',arguments={}));reply,_=e.apply(dict(name='simulate',arguments={}))
        if not reply.get('ok'):raise ValueError('paired completed study required')
        card,_=e.observe();label=assess(card)
        if not label['study_ready'] or label['measurement_feasible'] is not (variant=='feasible'):raise ValueError('paired physical label differs')
        if not label['equipment_budget_ok']:raise ValueError('counterfactual pair must have equal valid equipment budgets')
        feedback=dict(ok=True,error=None,operation='simulate');op='submit' if variant=='feasible' else 'set_line_catalog'
        base=dict(split='train',task_id=c['id'],base_project_id=c['base_project_id'],family_sha256=c['family_sha256'],variant=variant)
        rows.append(dict(**base,skill='operation',messages=operation_request(card,feedback)['messages'],answer=dict(operation=op)))
        rows.append(dict(**base,skill='measurement',messages=assessment_messages(card),answer=label))
        # Replay the public control and independently check its actual final project.
        replay=ReplacementEpisode(c)
        for event in control['events']:
            response,_=replay.apply(event['decision']['action'])
            if response!=event['response']:raise ValueError('paired control does not replay')
        after=compare_reference(replay.engine.state['project'],solve(replay.engine.state['project']))
        snapshots.append(dict(variant=variant,card=card,independent_before=before,independent_after=after))
    a,b=[{k:v for k,v in s['card'].items() if k not in ('scene','study')} for s in snapshots]
    if a!=b:raise ValueError('counterfactual metadata or public goals differ')
    return dict(base_project_id=case['base_project_id'],family_sha256=case['family_sha256'],snapshots=snapshots,controls=controls),rows


def export(source,anchor,target,target_sha,out):
    if verify(source)!=anchor:raise ValueError('owner counterfactual source differs')
    r=read_json(Path(source)/'summary.json')
    if r.get('schema')!='power-grid-env.replacement-challenge-audit/v2':raise ValueError('qualified v2 physics cases required')
    root=Path(out)
    if root.exists():raise ValueError('fresh paired physical corpus required')
    pairs=[];rows=[]
    for path in sorted((Path(source)/'cases').glob('*.json')):
        record=read_json(path);p,targets=pair(record['case']);pairs.append(p);rows.extend(targets)
    if len(pairs)!=r['cases'] or not pairs:raise ValueError('complete source pair inventory required')
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    import torch
    torch.set_num_threads(4);t=local_target(target,target_sha);processor=local_qwen35_processor(t['local_checkpoint'],t['config_sha256'])
    native=[]
    for i,row in enumerate(rows):
        _,meta=pack(processor,row);native.append(dict(index=i,**meta))
    root.mkdir(parents=True)
    with (root/'records.jsonl').open('w') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    write_json(root/'pairs.json',pairs)
    result=dict(schema='power-grid-env.matched-stage-physics-data/v1',source_manifest_sha256=anchor,target_sha256=target_sha,pairs=len(pairs),
        observations=2*len(pairs),records=len(rows),base_networks=len({p['base_project_id'] for p in pairs}),
        public_control_successes=sum(c['success'] for p in pairs for c in p['controls']),same_metadata_and_goals=True,
        independent_before_after_checks=4*len(pairs),native_admission_passed=True,checked_targets=len(native),native_rows=native,
        maximum_prompt_tokens=max(x['prompt_tokens'] for x in native),optimizer_run=False,eligible_for_gold_sft=False,
        source_sha256={f:identity(Path(__file__).with_name(f)) for f in ('engineering_counterfactual_cases.py','engineering_staged_agent.py','engineering_constraint_tasks.py','engineering_staged_data.py')},
        scope='Same-stage balanced400V TRAIN feasible/overloaded pairs,physically replayed;not independent networks,industrial qualification or learned performance')
    write_json(root/'summary.json',result);seal(root);return {k:v for k,v in result.items() if k!='native_rows'}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for f in ('source','manifest-sha256','target','target-sha256','out'):p.add_argument('--'+f,required=True)
    a=p.parse_args();print(json.dumps(export(a.source,a.manifest_sha256,a.target,a.target_sha256,a.out)))
