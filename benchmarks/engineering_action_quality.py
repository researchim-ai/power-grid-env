"""Separate valid wire syntax,required arguments,tool execution and physics.

A syntactically correct empty call is not a successful engineering action.
"""
from collections import Counter
import hashlib
from pathlib import Path
from jsonschema import Draft202012Validator
from benchmarks.engineering_learning_compare import audit
from benchmarks.engineering_scenarios import ScenarioEngine
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import seal


def quality(case,row):
    specs={s['function']['name']:s['function']['parameters'] for s in ScenarioEngine(case).schemas()};counts=Counter()
    for turn in row['turns']:
        counts['attempts']+=1
        if 'error' in turn:counts['transport_failures']+=1;continue
        if 'protocol_error' in turn:counts['wire_errors']+=1;continue
        action=turn['action'];counts['wire_valid_calls']+=1;schema=specs[action['name']];args=action['arguments'];required=schema.get('required',[])
        counts['required_slots']+=len(required)
        counts['present_required_slots']+=sum(k in args for k in required)
        counts['nonempty_required_slots']+=sum(k in args and args[k] is not None and args[k]!='' for k in required)
        if required:counts['calls_requiring_arguments']+=1
        if required and not args:counts['empty_required_calls']+=1
        errors=list(Draft202012Validator(schema).iter_errors(args))
        counts['schema_invalid_calls' if errors else 'schema_valid_calls']+=1
        if turn['response'].get('ok') is not True:counts['tool_errors']+=1
        if action['name']=='submit':counts['submits']+=1
    return dict(task_id=case['id'],kind=case['kind'],success=row['success'],counts=dict(counts),terminal=row['terminal'])


def report(evaluation,anchor,cases,contract,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh action-quality output required')
    source,rows=audit(evaluation,anchor,cases,contract);details=[quality(c,r) for c,r in zip(cases,rows)];counts=Counter()
    for row in details:counts.update(row['counts'])
    result=dict(schema='power-grid-env.action-quality-audit/v1',evaluation_manifest_sha256=anchor,source=source['source'],candidate=source['candidate'],
        tasks=len(cases),successes=sum(r['success'] for r in details),counts=dict(counts),rows=details,
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),scope='Replayed action/schema/required-argument diagnostics;not a new policy or changed score')
    write_json(root/'summary.json',result);seal(root);return result
