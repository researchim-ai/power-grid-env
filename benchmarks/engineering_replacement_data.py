"""Replay-qualified v2 teaching data; failed controller actions are never gold.

A physically feasible but over-budget network is a negative readiness example,
not a successful design. Every source card/response and terminal is replayed.
"""
from pathlib import Path
import json
from collections import Counter
from benchmarks.engineering_replacement_challenges import ReplacementEpisode
from benchmarks.engineering_action_menu import candidates
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_skill_tasks import identity
from power_grid_env.study_artifacts import read_json,verify,seal


def from_case(record):
    case=record['case'];rows=[]
    if case['split']!='train':raise ValueError('replacement teaching is train-only')
    for source in ('expert','rebuild_all'):
        e=ReplacementEpisode(case);trace=record[source]
        for turn,event in enumerate(trace['events']):
            card,_=e.observe()
            if card!=event['card']:raise ValueError('replacement source public card does not replay')
            ready=e.readiness_label()
            base=dict(task_id=case['id'],base_project_id=case['base_project_id'],family_sha256=case['family_sha256'],split='train',
                      profile='catalog-replacement/v2',source=source,turn=turn,card=card)
            if source=='expert':
                menu=candidates(card);gold=next(m['choice'] for m in menu if m['action']==event['action'])
                rows.append(dict(**base,task='choice',candidates=menu,answer=dict(choice=gold)))
            if source=='expert' or (card['has_fresh_measurements'] and len(card['changed_routes'])>1):
                rows.append(dict(**base,task='readiness',answer=ready))
            reply,_=e.apply(event['action'])
            if reply!=event['response']:raise ValueError('replacement source response does not replay')
        if e.engine.state['terminal']!=trace['terminal'] or (e.engine.state['terminal']['success'] is True)!=trace['success']:
            raise ValueError('replacement source terminal differs')
        if trace['success'] is not (source=='expert'):raise ValueError('replacement source outcome attribution differs')
    return rows


def export(source,anchor,out):
    if verify(source)!=anchor:raise ValueError('owner replacement audit differs')
    r=read_json(Path(source)/'summary.json')
    if r.get('schema')!='power-grid-env.replacement-challenge-audit/v2':raise ValueError('replacement v2 audit required')
    for f,sha in r['source_sha256'].items():
        if identity(Path(__file__).with_name(f))!=sha:raise ValueError('replacement replay implementation changed')
    root=Path(out)
    if root.exists():raise ValueError('fresh replacement data required')
    records=[]
    for p in sorted((Path(source)/'cases').glob('*.json')):records.extend(from_case(read_json(p)))
    tasks={x['task_id'] for x in records}
    if len(tasks)!=r['cases']:raise ValueError('replacement case inventory differs')
    root.mkdir(parents=True)
    with (root/'records.jsonl').open('w') as f:
        for row in records:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    summary=dict(schema='power-grid-env.replacement-teaching-data/v2',source_manifest_sha256=anchor,records=len(records),cases=len(tasks),
                 objectives=dict(Counter(x['task'] for x in records)),base_networks=len({x['base_project_id'] for x in records}),
                 hard_operational_negatives=sum(x['source']=='rebuild_all' for x in records),
                 failed_actions_admitted=0,all_cards_responses_terminals_replayed=True,source_sha256=identity(__file__),
                 eligible_for_gold_sft=False,scope='New v2 replay-qualified choice/readiness teaching candidates;native target admission and learned-policy gates still required')
    write_json(root/'summary.json',summary);seal(root);return summary
