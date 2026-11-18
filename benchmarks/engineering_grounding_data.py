"""Train-only public perception examples with visual counterfactual pairs."""
import argparse
import copy
import json
from pathlib import Path
import hashlib
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_learning_gate import strict_contract
from benchmarks.engineering_recovery import episode_messages
from benchmarks.engineering_grounding import scene_from_messages,render_scene,focus_line,question_records,counterfactual,digest,FONT,MONO
from power_grid_env.study_artifacts import seal


def export(train,manifest,out):
    data=ProceduralDataset(train,manifest,strict_contract());root=Path(out)
    if root.exists():raise ValueError('fresh grounding export required')
    root.mkdir(parents=True);scenes={};rows=[];images=set()
    def save_image(png):
        sha=hashlib.sha256(png).hexdigest();p=root/'images'/(sha+'.png')
        if sha not in images:p.parent.mkdir(exist_ok=True);p.write_bytes(png);images.add(sha)
        return 'images/'+sha+'.png',sha
    for episode in data.episodes:
        if episode['variant']!='clean':continue
        messages,event=next((m,e) for m,e in episode_messages(episode) if e['turn']==1)
        scene,aliases=scene_from_messages(messages);sid=digest(scene)
        if sid in scenes:continue
        scenes[sid]=dict(scene=scene,public_raw_alias_map=aliases,base_project_id=episode['base_project_id'],family_sha256=episode['family_sha256'],split='train')
        write_json(root/'scenes'/(sid+'.json'),scenes[sid]);image,sha=save_image(render_scene(scene))
        for q in question_records(scene):
            row=dict(**q,scene_sha256=sid,image=image,image_sha256=sha,view='full_scene',variant='factual',split='train',family_sha256=episode['family_sha256'])
            row['record_id']=digest(row);rows.append(row)
            if q['kind']!='line_rating':continue
            other=counterfactual(scene,q['subject']);cfid=digest(other)
            write_json(root/'scenes'/(cfid+'.json'),dict(scene=other,base_project_id=episode['base_project_id'],family_sha256=episode['family_sha256'],split='train',
                parent_scene_sha256=sid,intervention=dict(line=q['subject'],field='rating_a',scope='Synthetic passport-readout counterfactual,not a qualified power-flow case')))
            cfimg,cfsha=save_image(render_scene(other));answer=next(x for x in question_records(other) if x['kind']==q['kind'] and x['subject']==q['subject'])['answer']
            cf=dict(row,scene_sha256=cfid,image=cfimg,image_sha256=cfsha,variant='counterfactual',answer=answer,parent_record_id=row['record_id']);cf['record_id']=digest({k:v for k,v in cf.items() if k!='record_id'});rows.append(cf)
            # Query-selected equipment zoom is a separate curriculum view.
            for value,sc,key in ((row,scene,sid),(cf,other,cfid)):
                crop,cs=save_image(focus_line(sc,q['subject']));z=dict(value,image=crop,image_sha256=cs,view='equipment_zoom')
                z['record_id']=digest({k:v for k,v in z.items() if k!='record_id'});rows.append(z)
    with (root/'records.jsonl').open('w') as stream:
        for row in rows:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    result=dict(schema='power-grid-env.visual-grounding-data/v1',train_manifest_sha256=manifest,factual_scenes=len(scenes),records=len(rows),images=len(images),
        base_networks=len({v['base_project_id'] for v in scenes.values()}),families=len({v['family_sha256'] for v in scenes.values()}),
        renderer_sha256=hashlib.sha256(Path(__file__).with_name('engineering_grounding.py').read_bytes()).hexdigest(),
        exporter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),font_sha256={str(p):hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (FONT,MONO)},
        source='Public fresh train inspections only;IDs converted to public aliases;no hidden truth',eligible_for_gold_sft=False,
        scope='Visual perception/field binding curriculum;counterfactual readouts are not physical studies or policy success')
    write_json(root/'summary.json',result);seal(root);return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for f in ('train','manifest-sha256','out'):p.add_argument('--'+f,required=True)
    a=p.parse_args();print(json.dumps(export(a.train,a.manifest_sha256,a.out)))
