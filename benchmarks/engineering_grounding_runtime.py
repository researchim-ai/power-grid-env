"""Native VLM perception batches and controlled image/text/missing ablations."""
import argparse
import copy
from decimal import Decimal,InvalidOperation
import hashlib
import io
import json
from pathlib import Path
from PIL import Image
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_grounding import render_scene,focus_line,question_records,counterfactual,digest
from benchmarks.engineering_rollout import strict_json
from power_grid_env.study_artifacts import verify,read_json,seal

SYSTEM='Read the supplied public equipment observation. Return only the requested JSON. Copy values as displayed. If information is absent,return null for the requested fields. Do not guess.'


def load_records(root,anchor):
    if verify(root)!=anchor:raise ValueError('owner grounding manifest differs')
    root=Path(root);s=read_json(root/'summary.json')
    if s.get('schema')!='power-grid-env.visual-grounding-data/v1':raise ValueError('grounding corpus required')
    if s['renderer_sha256']!=hashlib.sha256(Path(__file__).with_name('engineering_grounding.py').read_bytes()).hexdigest():raise ValueError('renderer revision differs')
    for p,sha in s['font_sha256'].items():
        if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=sha:raise ValueError('font revision differs')
    rows=[json.loads(x) for x in (root/'records.jsonl').read_text().splitlines()]
    if len(rows)!=s['records'] or len({r['record_id'] for r in rows})!=len(rows):raise ValueError('grounding inventory differs')
    checked_images={};scenes={};families={};visible_labels={}
    for row in rows:
        if digest({k:v for k,v in row.items() if k!='record_id'})!=row['record_id']:raise ValueError('record identity differs')
        if row['view'] not in ('full_scene','equipment_zoom') or row['variant'] not in ('factual','counterfactual'):raise ValueError('unknown visual view/variant')
        if row['split']!='train':raise ValueError('training record role differs')
        key=row['scene_sha256']
        if key not in scenes:
            spec=read_json(root/'scenes'/(key+'.json'));scene=spec['scene']
            if digest(scene)!=key or spec['split']!='train' or spec['family_sha256']!=row['family_sha256']:raise ValueError('scene identity differs')
            if 'parent_scene_sha256' in spec:
                parent=read_json(root/'scenes'/(spec['parent_scene_sha256']+'.json'))
                if scene!=counterfactual(parent['scene'],spec['intervention']['line']):raise ValueError('counterfactual intervention differs')
            scenes[key]=scene;families[key]=spec['family_sha256']
        if families[key]!=row['family_sha256']:raise ValueError('record family differs from scene')
        scene=scenes[key];expected=next(q for q in question_records(scene) if q['kind']==row['kind'] and q['subject']==row['subject'])
        if row['question']!=expected['question'] or row['answer']!=expected['answer']:raise ValueError('visible question/answer differs')
        image_path=(root/row['image']).resolve()
        if not image_path.is_relative_to(root.resolve()/'images'):raise ValueError('unsafe image path')
        association=(key,row['view'],row['subject'] if row['view']=='equipment_zoom' else None)
        if association not in checked_images:
            png=focus_line(scene,row['subject']) if row['view']=='equipment_zoom' else render_scene(scene)
            if hashlib.sha256(png).hexdigest()!=row['image_sha256'] or image_path.read_bytes()!=png:raise ValueError('pixels do not represent the public scene')
            checked_images[association]=True
        visible=(row['question'],row['image_sha256']);label=digest(row['answer'])
        if visible in visible_labels and visible_labels[visible]!=label:raise ValueError('same visible input has contradictory readout labels')
        visible_labels[visible]=label
        row['_scene']=scene
    return rows


def messages_for(record,image_path,mode):
    if mode not in ('image','text','combined','blank','none'):raise ValueError('unknown modality')
    question=record['question'];content=[]
    if mode in ('image','combined','blank'):
        with Image.open(image_path) as original:
            img=original.convert('RGB')
            if mode=='blank':img=Image.new('RGB',img.size,'white')
        content.append(dict(type='image',image=img))
    if mode in ('text','combined'):
        question+='\nPUBLIC SCENE:\n'+json.dumps(record['_scene'],ensure_ascii=False,separators=(',',':'))
    content.append(dict(type='text',text=question))
    return [dict(role='system',content=SYSTEM),dict(role='user',content=content)]


def prompt_batch(processor,record,image_path,mode):
    return processor.apply_chat_template(messages_for(record,image_path,mode),add_generation_prompt=True,tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)


def supervised_batch(processor,record,image_path,mode='image'):
    import torch
    messages=messages_for(record,image_path,mode);settings=dict(tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)
    prompt=processor.apply_chat_template(messages,add_generation_prompt=True,**settings)
    target={k:None for k in record['answer']} if mode in ('none','blank') else record['answer']
    full=processor.apply_chat_template(messages+[dict(role='assistant',content=json.dumps(target,separators=(',',':')))],add_generation_prompt=False,**settings)
    n=prompt['input_ids'].shape[1];total=full['input_ids'].shape[1]
    if not n<total<=24576 or total-n>128 or not torch.equal(prompt['input_ids'],full['input_ids'][:,:n]):raise ValueError('visual target prefix/context differs')
    for k in ('pixel_values','image_grid_thw'):
        if k in prompt and not torch.equal(prompt[k],full[k]):raise ValueError('visual tensors changed while appending label')
    labels=full['input_ids'].clone();labels[:,:n]=-100;full['labels']=labels
    raw=processor.tokenizer.decode(full['input_ids'][labels!=-100],skip_special_tokens=True)
    if strict_json(raw)!=target:raise ValueError('actual visual labels changed answer')
    return full,dict(prompt_tokens=n,label_tokens=total-n,pixels_present='pixel_values' in full)


def score(raw,expected):
    try:value=strict_json(raw)
    except (ValueError,TypeError):return dict(format_valid=False,exact=False,semantic=False)
    valid=isinstance(value,dict) and set(value)==set(expected)
    semantic=valid
    if valid:
        for k,v in expected.items():
            got=value[k]
            if got==v:continue
            try:
                if isinstance(got,bool) or got is None or not Decimal(str(got)).is_finite() or Decimal(str(got))!=Decimal(str(v)):semantic=False
            except (InvalidOperation,ValueError):semantic=False
    return dict(format_valid=valid,exact=value==expected,semantic=bool(semantic))


def select_pair(rows,view):
    for cf in rows:
        if cf['view']!=view or cf['kind']!='line_rating' or cf['variant']!='counterfactual':continue
        base=next(r for r in rows if r['variant']=='factual' and r['view']==view and r['kind']=='line_rating' and r['subject']==cf['subject'] and r['record_id']==cf.get('parent_record_id')) if view=='full_scene' else None
        if base is None:
            # Zoom parents refer to the factual full-scene record.
            original=next(r for r in rows if r['record_id']==cf['parent_record_id'])
            base=next(r for r in rows if r['view']==view and r['variant']=='factual' and r['question']==cf['question'] and r['scene_sha256']==original['scene_sha256'])
        if base['question']!=cf['question'] or base['answer']==cf['answer']:raise ValueError('counterfactual pair does not isolate image information')
        return [base,cf]
    raise ValueError('complete counterfactual pair required')


def evaluate(root,anchor,target,target_sha,out):
    dest=Path(out)
    if dest.exists():raise ValueError('fresh VLM ablation output required')
    rows=load_records(root,anchor);pairs={view:select_pair(rows,view) for view in ('equipment_zoom','full_scene')}
    from power_grid_env.qwen35_training import local_target,load_native
    import torch
    processor,model=load_native(local_target(target,target_sha),4);dest.mkdir(parents=True);results=[]
    for view,modes in [('equipment_zoom',['image','text','blank','none']),('full_scene',['image'])]:
        for mode in modes:
            for record in pairs[view]:
                inputs=prompt_batch(processor,record,Path(root)/record['image'],mode)
                n=inputs['input_ids'].shape[1]
                if n+128>24576:raise ValueError('VLM ablation context exceeds budget')
                with torch.inference_mode():generated=model.generate(**inputs,do_sample=False,num_beams=1,max_new_tokens=128,use_cache=True,logits_to_keep=1,
                    pad_token_id=processor.tokenizer.pad_token_id,eos_token_id=processor.tokenizer.eos_token_id)
                raw=processor.tokenizer.decode(generated[0,n:],skip_special_tokens=True)
                try:abstained=strict_json(raw)=={k:None for k in record['answer']}
                except (ValueError,TypeError):abstained=False
                result=dict(view=view,mode=mode,variant=record['variant'],record_id=record['record_id'],expected=record['answer'],completion=raw,abstained=abstained,**score(raw,record['answer']))
                results.append(result);write_json(dest/'partial.json',dict(complete=False,results=results));print(json.dumps(result),flush=True)
    r=dict(schema='power-grid-env.visual-grounding-ablation/v1',complete=True,corpus_manifest_sha256=anchor,target_sha256=target_sha,candidate='fresh_base',results=results,
        paired_success={v+'|'+m:all(r['semantic'] for r in results if r['view']==v and r['mode']==m) for v,m in {(r['view'],r['mode']) for r in results}},
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),scope='One chosen train scene and passport counterfactual;visual sensitivity diagnostic,not engineering task success or heldout VLM quality')
    write_json(dest/'summary.json',r);seal(dest);return r


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for f in ('corpus','manifest-sha256','target','target-sha256','out'):p.add_argument('--'+f,required=True)
    a=p.parse_args();print(json.dumps(evaluate(a.corpus,a.manifest_sha256,a.target,a.target_sha256,a.out)))
