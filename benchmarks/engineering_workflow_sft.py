"""Replayed full-action engineering SFT, including nested argument payloads.

Scripted controller traces are teaching candidates, not learned model results.
No dev/test labels may enter this dataset or its native supervision checks.
"""
import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

from benchmarks.engineering_assisted_workflow import run,control
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.engineering_staged_agent import bounded_json
from benchmarks.engineering_workflow_campaign import load_plan
from power_grid_env.study_artifacts import read_json,seal,verify


def leaf_spans(answer):
    text=json.dumps(answer,separators=(',',':'),allow_nan=False)
    if len(text)>16384 or not isinstance(answer,dict) or not answer:raise ValueError('bounded nonempty action object required')
    decoder=json.JSONDecoder();spans=[]
    def walk(cursor,path,depth):
        if depth>16:raise ValueError('bounded JSON nesting required')
        char=text[cursor]
        if char=='{':
            cursor+=1
            if text[cursor]=='}':return cursor+1
            while True:
                key,end=decoder.raw_decode(text,cursor)
                if not isinstance(key,str) or text[end]!=':':raise ValueError('JSON object alignment differs')
                cursor=walk(end+1,path+(key,),depth+1)
                if text[cursor]=='}':return cursor+1
                if text[cursor]!=',':raise ValueError('JSON delimiter differs')
                cursor+=1
        elif char=='[':
            cursor+=1;index=0
            if text[cursor]==']':return cursor+1
            while True:
                cursor=walk(cursor,path+(str(index),),depth+1);index+=1
                if text[cursor]==']':return cursor+1
                if text[cursor]!=',':raise ValueError('JSON array delimiter differs')
                cursor+=1
        else:
            value,end=decoder.raw_decode(text,cursor)
            start=cursor+1 if isinstance(value,str) and value else cursor
            stop=end-1 if isinstance(value,str) and value else end
            spans.append(dict(path=list(path),start=start,end=stop))
            return end
    if walk(0,(),0)!=len(text) or not spans:raise ValueError('complete JSON leaves required')
    return text,spans


def pack(processor,row):
    import torch
    answer=row['answer'];m=row['messages']
    text,spans=leaf_spans(answer)
    settings=dict(tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)
    prompt=processor.apply_chat_template(m,add_generation_prompt=True,**settings)
    batch=processor.apply_chat_template(m+[dict(role='assistant',content=text)],add_generation_prompt=False,**settings)
    n,total=prompt['input_ids'].shape[1],batch['input_ids'].shape[1]
    if not n<total<=8192 or total-n>128 or not torch.equal(prompt['input_ids'],batch['input_ids'][:,:n]):
        raise ValueError('full-action native prefix or token cap differs')
    if 'pixel_values' in batch:raise ValueError('workflow SFT profile is text only')
    batch['labels']=batch['input_ids'].clone();batch['labels'][:,:n]=-100
    ids=batch['input_ids'][batch['labels']!=-100]
    raw=processor.tokenizer.decode(ids,skip_special_tokens=False)
    actual=processor.tokenizer.decode(ids,skip_special_tokens=True)
    if sha(bounded_json(actual))!=sha(answer) or not raw.startswith(text):raise ValueError('actual nested action labels differ')
    encoding=processor.tokenizer(raw,add_special_tokens=False,return_offsets_mapping=True)
    if encoding['input_ids']!=ids.tolist():raise ValueError('nested action token offsets differ')
    payload=[False]*len(ids);leaves=[]
    for span in spans:
        mask=[a<span['end'] and b>span['start'] for a,b in encoding['offset_mapping']]
        if not any(mask):raise ValueError('nested action argument payload missing')
        payload=[a or b for a,b in zip(payload,mask)]
        leaves.append(dict(path=span['path'],tokens=sum(mask)))
    return batch,dict(prompt_tokens=n,label_tokens=total-n,payload_tokens=sum(payload),leaves=leaves)


FILES=('engineering_workflow_sft.py','engineering_workflow_campaign.py','engineering_assisted_workflow.py')
def sources():return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in FILES}


def load_train(plan_root,anchor):
    p,_=load_plan(plan_root,anchor)
    rows=[json.loads(line) for line in (Path(plan_root)/'train-sft.jsonl').read_text().splitlines()]
    episodes=read_json(Path(plan_root)/'train-episodes.json');rebuilt=[]
    if len(episodes)!=p['train_sft_episodes']:raise ValueError('complete train episode inventory required')
    for item in episodes:
        case=item['case']
        if case['split']!='train':raise ValueError('heldout case cannot become workflow SFT')
        trace=run(case,control)
        if sha(trace)!=sha(item['trace']) or not trace['success']:raise ValueError('positive workflow trace does not replay')
        for turn,event in enumerate(trace['events']):
            row=dict(split='train',family_sha256=case['family_sha256'],base_project_id=case['base_project_id'],task_id=case['id'],turn=turn,
                     messages=event['query']['messages'],answer=event['action'],source='scripted_public_tool_controller',terminal_physics_replayed=True)
            row['id']=sha(row);rebuilt.append(row)
    if sha(rows)!=sha(rebuilt) or len(rows)!=p['train_sft_records']:raise ValueError('workflow SFT labels do not reconstruct')
    return rows,p


def qualify(plan_root,anchor,target,target_sha,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh full-action SFT admission required')
    rows,p=load_train(plan_root,anchor)
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    import torch
    torch.set_num_threads(4)
    spec=local_target(target,target_sha)
    processor=local_qwen35_processor(spec['local_checkpoint'],spec['config_sha256'])
    checks=[];failures=[]
    for index,row in enumerate(rows):
        try:_,meta=pack(processor,row);checks.append(dict(index=index,row_id=row['id'],**meta))
        except (ValueError,RuntimeError) as exc:failures.append(dict(index=index,error=str(exc)[:500]))
    root.mkdir(parents=True);write_json(root/'checks.json',checks)
    result=dict(schema='power-grid-env.workflow-sft-native-admission/v1',plan=str(Path(plan_root).resolve()),plan_manifest_sha256=anchor,
                target_sha256=target_sha,passed=not failures,failures=failures,records=len(rows),checked_targets=len(checks),
                actions=dict(Counter(r['answer']['name'] for r in rows)),source_sha256=sources(),
                maximum_prompt_tokens=max(c['prompt_tokens'] for c in checks),optimizer_run=False,model_weights_loaded=False,
                scope='Fully replayed TRAIN-only scripted full-action workflow SFT with native nested argument masks. '
                      'Not a trained model or heldout model result; sampling/loss need a separate declared recipe.')
    write_json(root/'summary.json',result);seal(root);return result


class WorkflowSFTDataset:
    def __init__(self,admission,anchor,target_sha):
        if verify(admission)!=anchor:raise ValueError('workflow SFT admission manifest differs')
        p=read_json(Path(admission)/'summary.json')
        if (p.get('schema')!='power-grid-env.workflow-sft-native-admission/v1' or p.get('passed') is not True or p.get('failures') or
                p.get('target_sha256')!=target_sha or p.get('source_sha256')!=sources()):
            raise ValueError('passing matching workflow SFT admission required')
        self.rows,_=load_train(p['plan'],p['plan_manifest_sha256']);self.plan=p
        checks=read_json(Path(admission)/'checks.json')
        if len(checks)!=len(self.rows) or [(c['index'],c['row_id']) for c in checks]!=[(i,r['id']) for i,r in enumerate(self.rows)]:
            raise ValueError('complete nested workflow native inventory required')
    def example(self,index):
        if type(index) is not int or not 0<=index<len(self.rows):raise ValueError('bounded TRAIN workflow index required')
        row=self.rows[index]
        if row['split']!='train':raise ValueError('heldout workflow labels forbidden in SFT')
        return copy.deepcopy(row)
    def batch(self,processor,index):return pack(processor,self.example(index))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('plan','manifest-sha256','target','target-sha256','out'):p.add_argument('--'+key,required=True)
    a=p.parse_args();print(json.dumps(qualify(a.plan,a.manifest_sha256,a.target,a.target_sha256,a.out)))
