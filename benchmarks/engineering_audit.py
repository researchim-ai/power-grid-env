"""Read-only failure audit of a sealed paired evaluation. No private labels."""
import argparse
from collections import Counter
import json
from pathlib import Path
from power_grid_env.study_artifacts import verify, read_json, seal


def case_metrics(row):
    turns=row['turns']
    counts=Counter();repeats=0;previous=None;revision=0
    successful_mutations=0;simulations=0;submissions=0;tool_errors=0
    for turn in turns:
        action=turn.get('action')
        response=turn.get('response',{})
        if action:
            name=action['name'];counts[name]+=1
            key=(revision,name,json.dumps(action['arguments'],sort_keys=True,separators=(',',':')))
            if key==previous:repeats+=1
            previous=key
            if name=='submit':submissions+=1
            if response.get('ok') is True:
                if name=='simulate':simulations+=1
                if name in ('add_line','add_load','set_line_catalog','remove_line'):successful_mutations+=1
            elif response.get('ok') is False:tool_errors+=1
        else:previous=None
        revision=response.get('observation',{}).get('revision',revision)
    return dict(task_id=row['task_id'],kind=row['kind'],success=row['success'],stop_reason=row['stop_reason'],
        calls=row['calls'],simulations=row['simulations'],turns=len(turns),actions=dict(counts),
        successful_mutations=successful_mutations,successful_simulations=simulations,submissions=submissions,
        consecutive_unchanged_revision_repeats=repeats,tool_errors=tool_errors,
        protocol_errors=sum('protocol_error' in t for t in turns),
        context_failure=any(t.get('error_code')=='context_budget' for t in turns),
        generation_budget_exhaustions=sum(t.get('generation',{}).get('token_budget_exhausted') is True for t in turns))


def audit(root,manifest_sha256,out):
    root=Path(root)
    if verify(root)!=manifest_sha256:raise ValueError('owner paired manifest differs')
    receipt=read_json(root/'summary.json')
    if receipt.get('schema')!='power-grid-env.engineering-paired-evaluation/v1' or receipt.get('split')!='dev':
        raise ValueError('paired dev evaluation required')
    dest=Path(out)
    if dest.exists():raise ValueError('fresh audit output required')
    roles={};ids={}
    for role,anchor in [('base','base_manifest_sha256'),('adapter','adapter_evaluation_manifest_sha256')]:
        if verify(root/role)!=receipt[anchor]:raise ValueError('paired child manifest differs')
        child=read_json(root/role/'summary.json')
        if child.get('split')!='dev' or child.get('max_turns')!=receipt['maximum_turns'] or child.get('tasks')!=receipt['tasks'] or child.get('repeat_limit',0)!=receipt.get('repeat_limit',0):
            raise ValueError('child evaluation budget or denominator differs')
        rows=[read_json(p) for p in sorted((root/role).glob('task-*.json'))]
        if len(rows)!=receipt['tasks'] or len({r['task_id'] for r in rows})!=len(rows) or any(r['split']!='dev' for r in rows):
            raise ValueError('missing, duplicate or non-dev task rows')
        for row in rows:
            if (type(row.get('success')) is not bool or
                type(row.get('calls')) is not int or not 0<=row['calls']<=receipt['maximum_turns'] or
                type(row.get('simulations')) is not int or not 0<=row['simulations']<=min(4,row['calls']) or
                row.get('reward') != float(row['success']) or
                not isinstance(row.get('turns'),list) or len(row['turns'])>receipt['maximum_turns'] or
                any(t.get('turn')!=i for i,t in enumerate(row['turns']))):
                raise ValueError('invalid case budget, reward or turn sequence')
        ids[role]={r['task_id'] for r in rows}
        metrics=[case_metrics(r) for r in rows]
        by_kind={}
        for kind in sorted({m['kind'] for m in metrics}):
            selected=[m for m in metrics if m['kind']==kind]
            by_kind[kind]=dict(tasks=len(selected),successes=sum(m['success'] for m in selected))
        actions=Counter()
        for m in metrics:actions.update(m['actions'])
        roles[role]=dict(tasks=len(metrics),successes=sum(m['success'] for m in metrics),by_kind=by_kind,
            actions=dict(actions),no_submit=sum(m['submissions']==0 for m in metrics),
            repeat_cases=sum(m['consecutive_unchanged_revision_repeats']>0 for m in metrics),
            context_failures=sum(m['context_failure'] for m in metrics),
            successful_mutations=sum(m['successful_mutations'] for m in metrics),cases=metrics)
        if roles[role]['successes']!=receipt[role+'_successes']:raise ValueError('success denominator differs')
    if len(receipt['pairs'])!=receipt['tasks'] or ids['base']!=ids['adapter'] or ids['base']!={p['task_id'] for p in receipt['pairs']}:
        raise ValueError('paired task IDs differ')
    for pair in receipt['pairs']:
        for role in ('base','adapter'):
            actual=next(m['success'] for m in roles[role]['cases'] if m['task_id']==pair['task_id'])
            if actual!=pair[role+'_success']:raise ValueError('paired case success differs')
    result=dict(schema='power-grid-env.engineering-failure-audit/v1',paired_manifest_sha256=manifest_sha256,
        maximum_turns=receipt['maximum_turns'],maximum_new_tokens=receipt['maximum_new_tokens'],public_view=receipt.get('public_view','raw'),repeat_limit=receipt.get('repeat_limit',0),roles=roles,
        scope='Public transcript diagnostics only. Repeated actions are observations, not a causal explanation. No heldout labels exported for training; no change to benchmark score.')
    dest.mkdir(parents=True,exist_ok=False)
    (dest/'summary.json').write_text(json.dumps(result,indent=2)+'\n');seal(dest)
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--paired',required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=audit(a.paired,a.manifest_sha256,a.out)
    print(json.dumps({role:{k:v for k,v in data.items() if k!='cases'} for role,data in r['roles'].items()}))

if __name__=='__main__':main()
