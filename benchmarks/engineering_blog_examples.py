"""Publish selected TRAIN illustrations from sealed, physically replayed runs.

No held-out case/prompt/answer publication. Original state measurements come
from the trained model's first simulation, not from the failed base model.
"""
import copy
import hashlib
import json
from pathlib import Path
from benchmarks.engineering_compact_workflow import CompactEpisode
from benchmarks.engineering_workflow_replay import replay
from benchmarks.studio_exchange import project
from power_grid_env.study_artifacts import read_json,verify

ROOT=Path(__file__).resolve().parents[1]
SELECTED=('healthy-catalog-9653ee31e697cab955c3fc88','tradeoff-catalog-65776384dbdcef09f5342767')
INPUTS={
 'plan':('engineering-workflow-holdout-plan-2026-11-19','9ee8749480a77dbcc9d140d20a0be0e061f6d420efeeef5ec53b6c0a67b2300f'),
 'base':('engineering-workflow-holdout-qwen4b-2026-11-19','52661227160abdce0e156eaae92cdf7bcd57c99c281c7c3467df782cd559c030'),
 'expanded':('engineering-decision-regression-qwen4b-2026-11-19','3d6a390ccc7fe4009d51fe768a8eb1efa4a232bea0df3cf556e58a25880567d0')}

def snapshot(owner):
    card,_=owner.episode.observe()
    if not card['has_fresh_measurements']:raise ValueError('fresh physical snapshot required')
    return dict(nodes=card['study']['nodes'],branches=card['study']['branches'],assessment=owner.public()['current_result'])


def build(out):
    folders={};provenance={}
    for name,(folder,expected) in INPUTS.items():
        root=ROOT/'reports'/folder;anchor=verify(root)
        if expected is not None and anchor!=expected:raise ValueError('sealed source differs: '+name)
        folders[name]=root;provenance[name]=dict(manifest_sha256=anchor,folder=folder)
    cases={c['id']:c for c in read_json(folders['plan']/'cases.json')}
    base={e['task_id']:e for e in read_json(folders['base']/'base.json')['episodes']}
    after={e['task_id']:e for e in read_json(folders['expanded']/'sft.json')['episodes']}
    examples=[];out=Path(out);out.mkdir(exist_ok=True,parents=True)
    for index,id in enumerate(SELECTED,1):
        case=cases[id];before=base[id];trained=after[id]
        if case['split']!='train' or before['success'] or not trained['success']:raise ValueError('only failed-base/successful-SFT TRAIN cases may be illustrated')
        replay(case,before);replay(case,trained)
        if before['events'][0]['public']['scene']!=trained['events'][0]['public']['scene']:raise ValueError('baseline and SFT initial networks differ')
        owner=CompactEpisode(case);initial=None;final=None;previews=[]
        card,_=owner.episode.observe();scene=card['scene']
        native=dict(case['project']);native.update(schema='power-grid-env.grid-editor/v5',source_node=scene['source'],
            nodes=[dict(id=n,nominal_ll_v=scene['nominal_ll_v'],x=100.+(i%4)*350,y=150.+(i//4)*250) for i,n in enumerate(scene['nodes'])],
            lines=scene['lines'],loads=[r['installed'] for r in scene['loads']],fault=dict(node_id=scene['source'],r_ohm=.001,x_ohm=0.))
        native=project(native)
        for event in trained['events']:
            if owner.public()!=event['public']:raise ValueError('example public state does not replay')
            actual_before=owner.public()['current_result']
            reply=owner.apply(event['action'])
            if event['action']['name']=='preview_line_catalog' and reply.get('ok'):
                if owner.public()['current_result']!=actual_before:
                    raise ValueError('hypothetical preview changed actual measurements')
                args=event['action']['arguments'];result=reply['result']
                previews.append(dict(equipment_id=args['equipment_id'],catalog_id=args['catalog_id'],
                    passport=next(c for c in scene['catalog'] if c['id']==args['catalog_id']),
                    assessment=result['assessment'],actual_assessment=actual_before,
                    actual_revision=result['base_revision']))
            if event['action']['name']=='simulate' and event['reply'].get('ok'):
                if initial is None:initial=snapshot(owner)
                final=snapshot(owner)
        changed=[e['action']['arguments']['equipment_id'] for e in trained['events'] if e['action']['name']=='set_line_catalog']
        kind='verify_healthy' if id.startswith('healthy-') else 'compare_replacements'
        if initial is None or final is None or len(changed)!=(0 if kind=='verify_healthy' else 1):
            raise ValueError('physical studies and the declared number of edits required')
        if kind=='verify_healthy' and (previews or initial!=final):
            raise ValueError('healthy verification must keep the original network')
        if kind=='compare_replacements' and not (any(not p['assessment']['measurement_feasible'] for p in previews) and any(p['assessment']['measurement_feasible'] for p in previews)):
            raise ValueError('comparison example requires rejected and accepted hypothetical studies')
        filename=f'learning-example-{index}.project.json';(out/filename).write_text(json.dumps(native,ensure_ascii=False,indent=2)+'\n')
        def trace(e):return [dict(action=t['action'],raw=t['completion'],decode_error=t['decode_error'],ok=t['reply'].get('ok'),error=t['reply'].get('error')) for t in e['events']]
        chosen=next((e['action']['arguments']['catalog_id'] for e in trained['events'] if e['action']['name']=='set_line_catalog'),None)
        passport=next((c for c in scene['catalog'] if c['id']==chosen),None)
        fixed=copy.deepcopy(native)
        if passport:next(l for l in fixed['lines'] if l['id']==changed[0]).update({k:v for k,v in passport.items() if k!='id'})
        fixed=project(fixed);fixed_file=f'learning-example-{index}-after.project.json'
        (out/fixed_file).write_text(json.dumps(fixed,ensure_ascii=False,indent=2)+'\n')
        examples.append(dict(id=f'train-example-{index}',kind=kind,split='train',task_id=id,model='Qwen3.5-4B',training_phase='decision240',
            nodes=scene['nodes'],source=scene['source'],lines=scene['lines'],changed_line=changed[0] if changed else None,initial=initial,final=final,previews=previews,
            base=dict(success=False,calls=before['calls'],trace=trace(before)),sft=dict(success=True,calls=trained['calls'],trace=trace(trained)),project_file=filename,modified_project_file=fixed_file,selected_passport=passport))
    task_types=[]
    for kind,prefix in [('verify_healthy','healthy-'),('compare_replacements','tradeoff-')]:
        ids=[c['id'] for c in cases.values() if c['id'].startswith(prefix)]
        task_types.append(dict(kind=kind,total=len(ids),base_successes=sum(base[id]['success'] for id in ids),
            sft_successes=sum(after[id]['success'] for id in ids),evaluation='previously_disclosed_regression'))
    result=dict(schema='power-grid-env.blog-learning-examples/v1',date='2026-11-19',source=provenance,
        generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),examples=examples,task_types=task_types,
        scope='Retrospectively selected TRAIN illustrations; not a new test or representative success-rate sample. Initial-state measurements are from the trained model first simulation; the base model did not run a valid study. Layout is schematic.')
    (out/'learning-examples.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');return result

if __name__=='__main__':
    r=build(ROOT/'web/assets');print(dict(examples=len(r['examples']),splits=sorted({e['split'] for e in r['examples']})))
