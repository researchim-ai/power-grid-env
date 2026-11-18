"""Train-only paired public-project scenes; no latent/future diagnosis labels."""
import argparse
import hashlib
import io
import json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from benchmarks.engineering_factory import ProceduralDataset,write_json
from benchmarks.engineering_protocol import RunContract,render_history
from benchmarks.engineering_recovery import episode_messages
from power_grid_env.study_artifacts import seal,verify,read_json

FONT='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


def public_snapshot(messages):
    task=json.loads(messages[1]['content'])['task'];project=None
    for i in range(2,len(messages),2):
        action=json.loads(messages[i]['content']);response=json.loads(messages[i+1]['content'])
        if action['name']=='inspect_project' and response.get('ok') is True:project=response['result']
    if project is None:raise ValueError('public inspection required before rendering')
    return dict(requirements=task['requirements'],project=project,
        scope='Last inspected public project;nominal/passport quantities only,no solved/future measurements')


def render(snapshot):
    req=snapshot['requirements'];project=snapshot['project']
    image=Image.new('RGB',(1400,940),'#f7f8fa');draw=ImageDraw.Draw(image)
    fonts={s:ImageFont.truetype(FONT,s) for s in (11,13,15,17,21)}
    def text(x,y,value,size=15,fill='#243546'):draw.text((x,y),str(value),font=fonts[size],fill=fill)
    text(30,22,'POWER GRID / PUBLIC PROJECT SNAPSHOT',21)
    text(30,56,'Nominal/passport values only. Required routes are dashed; installed lines are solid.',15)
    root=req['source_node'];children={n['id']:[] for n in project['nodes']}
    for route in req['routes']:children[route['from_node']].append(route['to_node'])
    slots={};depth={};counter=[0]
    def place(node,level):
        depth[node]=level
        if children[node]:
            for child in sorted(children[node]):place(child,level+1)
            slots[node]=sum(slots[c] for c in children[node])/len(children[node])
        else:slots[node]=counter[0];counter[0]+=1
    place(root,0)
    points={n:(65+min(125,760/max(1,max(depth.values())))*depth[n],145+slots[n]*min(110,510/max(1,counter[0]-1))) for n in slots}
    for route in req['routes']:
        a,b=points[route['from_node']],points[route['to_node']]
        installed=next((l for l in project['lines'] if {l['from_node'],l['to_node']}=={route['from_node'],route['to_node']}),None)
        if installed:draw.line((*a,*b),fill='#286a8f',width=3)
        else:
            for i in range(0,20,2):
                aa=(a[0]+(b[0]-a[0])*i/20,a[1]+(b[1]-a[1])*i/20)
                bb=(a[0]+(b[0]-a[0])*(i+1)/20,a[1]+(b[1]-a[1])*(i+1)/20)
                draw.line((*aa,*bb),fill='#82909a',width=2)
    for node,(x,y) in points.items():
        draw.ellipse((x-8,y-8,x+8,y+8),fill='#ffffff',outline='#286a8f',width=2)
        text(x-43,y+12,node,11)
        loads=[l for l in project['loads'] if l['node_id']==node]
        if node==root:text(x-38,y-28,'SOURCE 400 V',11)
        for i,load in enumerate(loads):
            text(x-43,y+29+15*i,f"{load['p_kw']:.2f} kW / {load['q_kvar']:.2f} kvar",11)
    draw.line((925,100,925,900),fill='#b9c3cd',width=1)
    text(950,105,'INSTALLED LINE PASSPORTS',17)
    for i,line in enumerate(sorted(project['lines'],key=lambda l:l['id'])):
        y=140+76*i;text(950,y,line['id'],15)
        text(950,y+22,f"L={line['length_m']:.2f} m  R={line['r_ohm_km']:.3f} ohm/km",13)
        text(950,y+42,f"X={line['x_ohm_km']:.3f} ohm/km  I_rated={line['rating_a']:.2f} A",13)
        text(950,y+61,f"{line['from_node']} -> {line['to_node']}",11)
    text(30,760,'OWNER CATALOG / PUBLIC REQUIREMENTS',17)
    for i,(name,c) in enumerate(sorted(req['catalog'].items())):
        text(30,795+30*i,f"{name}: R={c['r_ohm_km']:.3f} ohm/km; X={c['x_ohm_km']:.3f} ohm/km; I={c['rating_a']:.2f} A",15)
    text(30,875,'Display is rounded. Full precision remains in the paired public JSON observation.',13)
    text(30,900,'No hidden fault class, teacher action, or future solver result is rendered.',13)
    stream=io.BytesIO();image.save(stream,format='PNG');return stream.getvalue()


def export_visual(train,manifest,out):
    contract=RunContract(protocol='qwen-native-v1',maximum_actions=24,context_tokens=24576)
    data=ProceduralDataset(train,manifest,contract);root=Path(out)
    root.mkdir(parents=True,exist_ok=False);(root/'images').mkdir()
    rows=[];images=set()
    for episode in data.episodes:
        for messages,event in episode_messages(episode):
            if event['source']!='public_expert' or len(messages)<4:continue
            previous=json.loads(messages[-2]['content']);response=json.loads(messages[-1]['content'])
            if previous['name']!='inspect_project' or response.get('ok') is not True:continue
            snapshot=public_snapshot(messages);png=render(snapshot);digest=hashlib.sha256(png).hexdigest()
            path='images/'+digest+'.png'
            if digest not in images:(root/path).write_bytes(png);images.add(digest)
            rows.append(dict(episode_id=episode['episode_id'],task_id=episode['task_id'],split='train',family_sha256=episode['family_sha256'],turn=event['turn'],
                image=path,image_sha256=digest,public_snapshot_sha256=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest(),
                messages=messages,completion=event['action'],eligible_for_gold_sft=False))
            break
    with (root/'records.jsonl').open('w') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    summary=dict(schema='power-grid-env.public-visual-train/v1',records=len(rows),unique_images=len(images),train_manifest_sha256=manifest,
        renderer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),font_sha256=hashlib.sha256(Path(FONT).read_bytes()).hexdigest(),
        eligible_for_gold_sft=False,scope='Public post-inspection static passport scene pairs;not VLM training or proof of visual reasoning')
    write_json(root/'summary.json',summary);seal(root);return summary


def pack_visual(processor,record,image_path,contract):
    import torch
    if contract.protocol not in ('qwen-native-v1','qwen-native-strict-v1'):raise ValueError('visual action collator requires native tool contract')
    from benchmarks.engineering_protocol import parse_native,native_completion
    rendered,kwargs=render_history(record['messages'],contract)
    with Image.open(image_path) as image:
        image=image.convert('RGB')
        rendered.append(dict(role='user',content=[dict(type='image',image=image),dict(type='text',text='Visible public project snapshot at the last inspection. Select the next engineering tool action.')]))
        settings=dict(tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False,**kwargs)
        prompt=processor.apply_chat_template(rendered,add_generation_prompt=True,**settings)
        action=record['completion'];parse_native(native_completion(action),json.loads(record['messages'][1]['content'])['task']['tools'])
        completion=dict(role='assistant',content='',tool_calls=[dict(id='target_call',type='function',function=action)])
        full=processor.apply_chat_template(rendered+[completion],add_generation_prompt=False,**settings)
    n=prompt['input_ids'].shape[1];total=full['input_ids'].shape[1]
    if total>contract.context_tokens or total<=n or total-n>contract.action_tokens or not torch.equal(prompt['input_ids'],full['input_ids'][:,:n]):raise ValueError('VLM prefix/context differs')
    if 'pixel_values' not in full or 'image_grid_thw' not in full:raise ValueError('native visual tensors missing')
    if not torch.equal(prompt['image_grid_thw'],full['image_grid_thw']) or not torch.equal(prompt['pixel_values'],full['pixel_values']):raise ValueError('visual prompt changed while appending target')
    labels=full['input_ids'].clone();labels[:,:n]=-100;full['labels']=labels
    return full,dict(prompt_tokens=n,label_tokens=total-n,pixel_shape=list(full['pixel_values'].shape),image_grid=full['image_grid_thw'].tolist())


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--train',required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();print(json.dumps(export_visual(a.train,a.manifest_sha256,a.out)))
