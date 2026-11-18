"""Replayed, owner-pinned paired tiny-policy evidence; no evaluation-to-SFT path."""
import argparse
import hashlib
from pathlib import Path
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_learning_gate import rollout
from benchmarks.engineering_protocol import RunContract
from power_grid_env.study_artifacts import read_json,verify,seal


def audit(root,anchor,cases,contract):
    if verify(root)!=anchor:raise ValueError('evaluation owner manifest differs')
    report=read_json(Path(root)/'summary.json')
    ids=[c['id'] for c in cases]
    if (report.get('schema')!='power-grid-env.procedural-standalone-eval/v1' or report.get('complete') is not True or
        report.get('task_ids')!=ids or report.get('tasks')!=len(ids) or report.get('contract')!=contract.model_dump() or
        report.get('contract_sha256')!=contract.fingerprint()):raise ValueError('evaluation denominator or profile differs')
    if len(set(ids))!=len(ids):raise ValueError('duplicate evaluation task')
    if {p.stem for p in Path(root).glob('case-*.json')}!=set(ids):raise ValueError('evaluation episode inventory differs')
    replayed=[]
    for case in cases:
        row=read_json(Path(root)/(case['id']+'.json'));turns=iter(row['turns'])
        def stored_policy(messages):
            try:t=next(turns)
            except StopIteration:raise ValueError('replay exhausted before recorded stop')
            if 'error' in t:
                class RecordedFailure(RuntimeError):pass
                error=RecordedFailure(t['detail']);error.code=t['error_code'];raise error
            return dict(text=t['completion'],**t['generation'])
        r=rollout(case,stored_policy,contract)
        for key in ('task_id','split','family_sha256','base_project_id','kind','success','reward','stop_reason','calls','simulations','final_revision','terminal'):
            if r[key]!=row.get(key):raise ValueError('replayed outcome differs: '+key)
        if len(r['turns'])!=len(row['turns']):raise ValueError('replayed turn coverage differs')
        for old,new in zip(row['turns'],r['turns']):
            for key in ('action','response','protocol_error','completion','generation','error_code'):
                if old.get(key)!=new.get(key):raise ValueError('replayed transition differs: '+key)
        replayed.append(r)
    metrics=dict(successes=sum(r['success'] for r in replayed),accuracy=sum(r['success'] for r in replayed)/len(replayed),
        protocol_errors=sum('protocol_error' in t for r in replayed for t in r['turns']),
        policy_failures=sum(r['stop_reason']=='policy_failure' for r in replayed),
        edits=sum(t.get('action',{}).get('name') in ('add_line','add_load','set_line_catalog','remove_line') and t.get('response',{}).get('ok') is True for r in replayed for t in r['turns']),
        submits=sum(t.get('action',{}).get('name')=='submit' for r in replayed for t in r['turns']),base_networks=len({r['base_project_id'] for r in replayed}))
    for key,value in metrics.items():
        if report.get(key)!=value:raise ValueError('summary metric differs: '+key)
    for key,field in [('kinds','kind'),('families','family_sha256')]:
        expected={value:dict(tasks=sum(r[field]==value for r in replayed),successes=sum(r['success'] for r in replayed if r[field]==value)) for value in sorted({r[field] for r in replayed})}
        if report.get(key)!=expected:raise ValueError('grouped metric differs: '+key)
    return report,replayed


def compare(base,base_manifest,adapter,adapter_manifest,cases,contract,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh comparison output required')
    b,br=audit(base,base_manifest,cases,contract);a,ar=audit(adapter,adapter_manifest,cases,contract)
    if b['source']!=a['source']:raise ValueError('paired corpus/suite differs')
    bc=b['candidate'];ac=a['candidate']
    if bc.get('kind')!='native_model' or ac.get('kind')!='native_model' or bc.get('adapter_manifest_sha256') is not None or not ac.get('adapter_manifest_sha256') or bc.get('target_sha256')!=ac.get('target_sha256'):
        raise ValueError('paired base and adapter identities required')
    deltas=[dict(task_id=x['task_id'],kind=x['kind'],family_sha256=x['family_sha256'],base_success=x['success'],adapter_success=y['success'],
        base_stop=x['stop_reason'],adapter_stop=y['stop_reason'],base_calls=x['calls'],adapter_calls=y['calls']) for x,y in zip(br,ar)]
    r=dict(schema='power-grid-env.tiny-paired-learning/v1',base_manifest_sha256=base_manifest,adapter_manifest_sha256=adapter_manifest,
        source=b['source'],contract=contract.model_dump(),target_sha256=bc['target_sha256'],checkpoint_manifest_sha256=ac['adapter_manifest_sha256'],
        tasks=len(cases),base_successes=b['successes'],adapter_successes=a['successes'],improved=sum(not d['base_success'] and d['adapter_success'] for d in deltas),
        regressed=sum(d['base_success'] and not d['adapter_success'] for d in deltas),
        tiny_learning_gate_passed=b['source'].get('role')=='tiny_train' and a['successes']==len(cases),
        physical_transition_replay_passed=True,rows=deltas,auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='Fixed-suite paired completion;tiny train memorization is not heldout quality or external qualification')
    write_json(root/'summary.json',r);seal(root);return r
