"""Named-operation and image/insufficiency curriculum, with pinned train lineage."""
import copy,json
from pathlib import Path
from collections import Counter
from benchmarks.engineering_grounding_runtime import load_records,messages_for,select_pair
from benchmarks.engineering_operation_probe import messages as operation_messages
from benchmarks.engineering_grounding import digest
from benchmarks.engineering_skill_tasks import score,identity
from benchmarks.engineering_value_loss import payload_mask
from power_grid_env.study_artifacts import read_json,verify


class DecisionDataset:
    def __init__(self,sources):
        self.sources=copy.deepcopy(sources);self.rows=[];self.duplicates=0;seen=set();lineage=[]
        schemas=dict(card='power-grid-env.card-curriculum/v1',replacement='power-grid-env.replacement-teaching-data/v2',visual='power-grid-env.visual-grounding-data/v1')
        for role,schema in schemas.items():
            item=sources[role]
            if verify(item['path'])!=item['manifest_sha256']:raise ValueError('owner decision source differs:'+role)
            summary=read_json(Path(item['path'])/'summary.json')
            if summary.get('schema')!=schema:raise ValueError('decision source role differs')
            if role in ('card','visual'):lineage.append(summary['train_manifest_sha256'])
        if len(set(lineage))!=1:raise ValueError('decision train lineages differ')
        # Preserve the complete v2 equipment acceptance ancestry, not just a
        # coincidentally matching family label.
        parents={}
        for role in ('replacement_audit','catalog_parent'):
            item=sources[role]
            if verify(item['path'])!=item['manifest_sha256']:raise ValueError('decision parent differs:'+role)
            parents[role]=read_json(Path(item['path'])/'summary.json')
        replacement=read_json(Path(sources['replacement']['path'])/'summary.json')
        if (parents['replacement_audit'].get('schema')!='power-grid-env.replacement-challenge-audit/v2' or
            parents['catalog_parent'].get('schema')!='power-grid-env.catalog-intervention-corpus/v1' or
            replacement['source_manifest_sha256']!=sources['replacement_audit']['manifest_sha256'] or
            parents['replacement_audit']['source_manifest_sha256']!=sources['catalog_parent']['manifest_sha256'] or
            parents['catalog_parent']['train_manifest_sha256']!=lineage[0]):
            raise ValueError('replacement v2 train ancestry differs')
        def append(row):
            if row['split']!='train':raise ValueError('train-only decision curriculum')
            row['id']=digest({k:v for k,v in row.items() if not k.startswith('_')})
            if row['id'] in seen:self.duplicates+=1;return
            seen.add(row['id']);self.rows.append(row)
        for role in ('card','replacement'):
            for raw in (Path(sources[role]['path'])/'records.jsonl').read_text().splitlines():
                r=json.loads(raw)
                if role=='card':
                    if r['task']!='full_action':continue
                    action=r['answer']
                else:
                    if r['task']!='choice':continue
                    if r['source']!='expert':raise ValueError('failed actions cannot supervise operations')
                    action=next(m['action'] for m in r['candidates'] if m['choice']==r['answer']['choice'])
                append(dict(skill='operation',source_role=role,split=r['split'],task_id=r['task_id'],
                    base_project_id=r['base_project_id'],family_sha256=r['family_sha256'],card=r['card'],answer=dict(operation=action['name'])))
        visual=sources['visual']
        records=load_records(visual['path'],visual['manifest_sha256'])
        for r in records:
            meta=read_json(Path(visual['path'])/'scenes'/(r['scene_sha256']+'.json'))
            if meta['split']!='train' or meta['family_sha256']!=r['family_sha256']:raise ValueError('visual lineage differs')
            for mode in ('image','none','blank'):
                append(dict(r,skill='perception',source_role='visual',mode=mode,base_project_id=meta['base_project_id'],
                    answer=r['answer'] if mode=='image' else {k:None for k in r['answer']}))
        families={}
        for r in self.rows:
            base=r['base_project_id'];family=r['family_sha256']
            if base in families and families[base]!=family:raise ValueError('base network family attribution differs')
            families[base]=family
        self.inventory=dict(rows=len(self.rows),duplicates_removed=self.duplicates,base_networks=len(families),families=len(set(families.values())),
                            operation_counts=dict(Counter(r['answer']['operation'] for r in self.rows if r['skill']=='operation')))

    def pilot_indices(self):
        result=[]
        # Four v2 states from one public replacement workflow, preventing a
        # profile-name shortcut for set_line_catalog versus validate/simulate/submit.
        replacement=[(i,r) for i,r in enumerate(self.rows) if r['skill']=='operation' and r['source_role']=='replacement']
        task=replacement[0][1]['task_id']
        for name in ('validate','simulate','set_line_catalog','submit'):
            result.append(next(i for i,r in replacement if r['task_id']==task and r['answer']['operation']==name))
        for name in ('add_line','add_load'):
            result.append(next(i for i,r in enumerate(self.rows) if r['skill']=='operation' and r['card']['kind']=='assemble' and r['answer']['operation']==name))
        image_rows=[r for r in self.rows if r['skill']=='perception' and r['mode']=='image']
        pair=select_pair(image_rows,'equipment_zoom')
        for p in pair:
            result.append(next(i for i,r in enumerate(self.rows) if r['skill']=='perception' and r['mode']=='image' and r['record_id']==p['record_id']))
        for mode in ('none','blank'):
            result.append(next(i for i,r in enumerate(self.rows) if r['skill']=='perception' and r['mode']==mode and r['record_id']==pair[0]['record_id']))
        return result

    def transfer_indices(self):
        # Two missing-information questions not in the ten training exemplars.
        used={self.rows[i]['question'] for i in self.pilot_indices() if self.rows[i]['skill']=='perception'}
        result=[]
        for i,r in enumerate(self.rows):
            if r['skill']=='perception' and r['mode']==('none' if not result else 'blank') and r['question'] not in used:
                result.append(i);used.add(r['question'])
                if len(result)==2:break
        if len(result)!=2:raise ValueError('two untrained question presentations required')
        return result

    def messages(self,index):
        r=self.rows[index]
        if r['skill']=='operation':return operation_messages(r['card']),r['answer']
        return messages_for(r,Path(self.sources['visual']['path'])/r['image'],r['mode']),r['answer']

    def batch(self,processor,index):
        import torch
        messages,answer=self.messages(index);settings=dict(tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)
        prompt=processor.apply_chat_template(messages,add_generation_prompt=True,**settings)
        full=processor.apply_chat_template(messages+[dict(role='assistant',content=json.dumps(answer,separators=(',',':')))],add_generation_prompt=False,**settings)
        n,total=prompt['input_ids'].shape[1],full['input_ids'].shape[1]
        if not n<total<=8192 or total-n>128 or not torch.equal(prompt['input_ids'],full['input_ids'][:,:n]):raise ValueError('decision target prefix/budget differs')
        for k in ('pixel_values','image_grid_thw'):
            if k in prompt and (k not in full or not torch.equal(prompt[k],full[k])):raise ValueError('decision image tensors changed')
        full['labels']=full['input_ids'].clone();full['labels'][:,:n]=-100
        ids=full['input_ids'][full['labels']!=-100]
        if not score(processor.tokenizer.decode(ids,skip_special_tokens=True),answer)['exact']:raise ValueError('actual decision labels differ')
        mask=payload_mask(processor.tokenizer,ids,answer)
        pixels='pixel_values' in full
        if pixels!=(self.rows[index]['skill']=='perception' and self.rows[index]['mode'] in ('image','blank')):raise ValueError('decision image presence differs')
        return full,mask,dict(row_id=self.rows[index]['id'],skill=self.rows[index]['skill'],prompt_tokens=n,label_tokens=total-n,payload_tokens=sum(mask),pixels_present=pixels)
