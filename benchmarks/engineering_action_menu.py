"""Public schema-valid choices;decision targets instead of opaque-ID spelling.

No feasibility labels are used to build a menu. Wrong catalog/entity/early
submission remain selectable and fail the physical/operational grader.
"""
from itertools import product
import json
import hashlib
from pathlib import Path
from jsonschema import Draft202012Validator
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,read_json,seal


def candidates(card):
    scene=card['scene'];lines=[l['id'] for l in scene['lines']];routes=[r['id'] for r in scene['routes']];loads=[d['id'] for d in scene['loads']]
    equipment=sorted(scene['nodes']+lines+loads);result=[]
    for spec in sorted(card['tools'],key=lambda s:s['function']['name']):
        name=spec['function']['name'];schema=spec['function']['parameters'];required=schema.get('required',[]);options=[]
        for key in required:
            prop=schema['properties'][key]
            if 'enum' in prop:values=sorted(prop['enum'])
            elif key=='equipment_id':values=equipment if name=='inspect_equipment' else lines
            elif key=='location':values=sorted(lines+routes)
            else:raise ValueError('unbounded action field not qualified for finite menu:'+key)
            options.append(values)
        for values in product(*options):
            action=dict(name=name,arguments=dict(zip(required,values)))
            if not Draft202012Validator(schema).is_valid(action['arguments']):raise ValueError('menu entry violates public schema')
            result.append(dict(choice='A'+str(len(result)),action=action))
    return result


def resolve(card,choice):
    if not isinstance(choice,str):raise ValueError('one public choice ID required')
    return next((r['action'] for r in candidates(card) if r['choice']==choice),None)


def export(card_root,card_anchor,catalog_root,catalog_anchor,out):
    if verify(card_root)!=card_anchor or verify(catalog_root)!=catalog_anchor:raise ValueError('owner curriculum manifests differ')
    root=Path(out)
    if root.exists():raise ValueError('fresh action-choice corpus required')
    rows=[json.loads(x) for x in (Path(card_root)/'records.jsonl').read_text().splitlines()];frames=[]
    for row in rows:
        if row['task']=='full_action':frames.append(dict(task_id=row['task_id'],base_project_id=row['base_project_id'],family_sha256=row['family_sha256'],card=row['card'],action=row['answer']))
    for path in sorted((Path(catalog_root)/'cases').glob('*.json')):
        record=read_json(path);case=record['case']
        for event in record['expert']['events']:frames.append(dict(task_id=case['id'],base_project_id=case['base_project_id'],family_sha256=case['family_sha256'],card=event['card'],action=event['action']))
    root.mkdir(parents=True);sizes=[]
    with (root/'records.jsonl').open('w') as f:
        for row in frames:
            menu=candidates(row['card']);gold=next((m['choice'] for m in menu if m['action']==row['action']),None)
            if gold is None:raise ValueError('expert action absent from public menu')
            result=dict(profile='action-choice/v1',split='train',task_id=row['task_id'],base_project_id=row['base_project_id'],family_sha256=row['family_sha256'],
                card=row['card'],candidates=menu,answer=dict(choice=gold));f.write(json.dumps(result,ensure_ascii=False)+'\n');sizes.append(len(menu))
    r=dict(schema='power-grid-env.action-choice-data/v1',card_manifest_sha256=card_anchor,catalog_manifest_sha256=catalog_anchor,records=len(frames),
        base_networks=len({r['base_project_id'] for r in frames}),maximum_menu_size=max(sizes),source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='New bounded action interface,dense decision labels;schema validity is enforced by construction,not a learned-policy achievement',eligible_for_gold_sft=False)
    write_json(root/'summary.json',r);seal(root);return r
