"""Source-admitted physics contrasts,conditional arguments and VLM retention."""
import copy,json
from pathlib import Path
from benchmarks.engineering_grounding_runtime import load_records,select_pair,supervised_batch,messages_for
from benchmarks.engineering_staged_data import pack
from benchmarks.engineering_value_loss import payload_mask
from benchmarks.engineering_grounding import digest
from benchmarks.engineering_skill_tasks import identity
from power_grid_env.study_artifacts import verify,read_json


class PhysicsDataset:
    def __init__(self,sources):
        self.sources=copy.deepcopy(sources);self.rows=[];summaries={}
        schemas=dict(staged='power-grid-env.staged-teaching-data/v1',paired='power-grid-env.matched-stage-physics-data/v1',
            visual='power-grid-env.visual-grounding-data/v1',audit='power-grid-env.replacement-challenge-audit/v2',catalog='power-grid-env.catalog-intervention-corpus/v1')
        for role,schema in schemas.items():
            item=sources[role]
            if verify(item['path'])!=item['manifest_sha256']:raise ValueError('physics source differs:'+role)
            summaries[role]=read_json(Path(item['path'])/'summary.json')
            if summaries[role].get('schema')!=schema:raise ValueError('physics source role differs:'+role)
        if (summaries['staged']['source_manifest_sha256']!=sources['audit']['manifest_sha256'] or
            summaries['paired']['source_manifest_sha256']!=sources['audit']['manifest_sha256'] or
            summaries['audit']['source_manifest_sha256']!=sources['catalog']['manifest_sha256'] or
            summaries['visual']['train_manifest_sha256']!=summaries['catalog']['train_manifest_sha256']):raise ValueError('physics sources train ancestry differs')
        for role,passed,checked in [('staged','admission_passed','checked_targets'),('paired','native_admission_passed','checked_targets')]:
            s=summaries[role]
            if s.get(passed) is not True or s.get('failures') or s[checked]!=s['records']:raise ValueError('all physics targets require native admission')
            for f,sha in s['source_sha256'].items():
                if identity(Path(__file__).with_name(f))!=sha:raise ValueError('physics teaching implementation changed:'+f)
            for text in (Path(sources[role]['path'])/'records.jsonl').read_text().splitlines():
                row=json.loads(text)
                if row['split']!='train':raise ValueError('physics curriculum is train-only')
                row['origin']=role;row['id']=digest(row);self.rows.append(row)
        v=sources['visual'];self.visual=load_records(v['path'],v['manifest_sha256'])
        for original in self.visual:
            meta=read_json(Path(v['path'])/'scenes'/(original['scene_sha256']+'.json'))
            if meta['split']!='train' or meta['family_sha256']!=original['family_sha256']:raise ValueError('visual lineage differs')
            for mode in ('image','none','blank'):
                row=dict(original,origin='visual',skill='perception',mode=mode,base_project_id=meta['base_project_id'],
                    answer=original['answer'] if mode=='image' else {k:None for k in original['answer']})
                row['id']=digest({k:x for k,x in row.items() if not k.startswith('_')});self.rows.append(row)
        if len({r['id'] for r in self.rows})!=len(self.rows):raise ValueError('duplicate source physics rows')

    def select(self):
        rows=self.rows
        a=next(r for r in rows if r['origin']=='paired' and r['skill']=='operation' and r['variant']=='overloaded')
        task=a['task_id'];base=a['base_project_id'];idx=[]
        for variant in ('overloaded','feasible'):
            idx.append(next(i for i,r in enumerate(rows) if r['origin']=='paired' and r['base_project_id']==base and r['task_id']==(task if variant=='overloaded' else 'initially-feasible-'+task) and r['skill']=='operation'))
        for op in ('validate','simulate'):
            idx.append(next(i for i,r in enumerate(rows) if r['origin']=='staged' and r['task_id']==task and r['skill']=='operation' and r['answer']==dict(operation=op)))
        first=next(i for i,r in enumerate(rows) if r['origin']=='staged' and r['task_id']==task and r['skill']=='conditional_arguments')
        second=next(i for i,r in enumerate(rows) if r['origin']=='staged' and r['base_project_id']==base and r['skill']=='conditional_arguments' and r['answer']['catalog_id']!=rows[first]['answer']['catalog_id'])
        idx.extend([first,second])
        for variant in ('overloaded','feasible'):
            idx.append(next(i for i,r in enumerate(rows) if r['origin']=='paired' and r['task_id']==(task if variant=='overloaded' else 'initially-feasible-'+task) and r['skill']=='measurement'))
        idx.append(next(i for i,r in enumerate(rows) if r['origin']=='staged' and r['task_id']==task and r['skill']=='measurement' and r['source']=='rebuild_all' and r['answer']['measurement_feasible'] is True and r['answer']['equipment_budget_ok'] is False))
        pair=select_pair(self.visual,'equipment_zoom')
        for record,mode in [(pair[0],'image'),(pair[1],'image'),(pair[0],'none'),(pair[0],'blank')]:
            idx.append(next(i for i,r in enumerate(rows) if r['origin']=='visual' and r['record_id']==record['record_id'] and r['mode']==mode))
        used={rows[i]['question'] for i in idx if rows[i]['origin']=='visual'};transfer=[]
        for mode in ('none','blank'):
            i=next(i for i,r in enumerate(rows) if r['origin']=='visual' and r['mode']==mode and r['question'] not in used)
            transfer.append(i);used.add(rows[i]['question'])
        if len(set(idx))!=13 or set(idx)&set(transfer):raise ValueError('thirteen exemplars and disjoint question probes required')
        return idx,transfer,[task,rows[second]['task_id']]

    def cases(self,ids):
        paths=Path(self.sources['audit']['path'])/'cases'
        a=read_json(paths/(ids[0]+'.json'))['case'];b=read_json(paths/(ids[1]+'.json'))['case']
        healthy=copy.deepcopy(a);healthy['id']='initially-feasible-'+a['id'];healthy['project']=copy.deepcopy(a['healthy_project'])
        return [a,b,healthy]

    def messages(self,index):
        r=self.rows[index]
        if r['origin']=='visual':return messages_for(r,Path(self.sources['visual']['path'])/r['image'],r['mode']),r['answer']
        return r['messages'],r['answer']

    def batch(self,processor,index):
        r=self.rows[index]
        if r['origin']=='visual':
            batch,meta=supervised_batch(processor,r,Path(self.sources['visual']['path'])/r['image'],r['mode'])
        else:batch,meta=pack(processor,r)
        if batch['input_ids'].shape[1]>8192:raise ValueError('physics context exceeds8192')
        ids=batch['input_ids'][batch['labels']!=-100];mask=payload_mask(processor.tokenizer,ids,r['answer'])
        return batch,mask,dict(row_id=r['id'],skill=r['skill'],prompt_tokens=meta['prompt_tokens'],label_tokens=meta['label_tokens'],payload_tokens=sum(mask),pixels_present='pixel_values' in batch)
