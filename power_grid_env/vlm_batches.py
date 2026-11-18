"""Owner-anchored multimodal exports and native processor SFT loss masks.

Loads a local processor only, never model weights or remote Python code.
The qualified checkpoint is Qwen3.5-4B; this module does not train a model.
"""
from pathlib import Path
import json
import re
import struct
from power_grid_env.study_artifacts import verify,read_json,digest


def local_qwen35_processor(checkpoint,expected_config_sha256):
    root=Path(checkpoint).resolve()
    if not re.fullmatch(r'[0-9a-f]{64}',expected_config_sha256) or digest((root/'config.json').read_bytes())!=expected_config_sha256:raise ValueError('owner checkpoint config differs')
    config=read_json(root/'config.json')
    if config.get('model_type')!='qwen3_5' or config.get('architectures')!=['Qwen3_5ForConditionalGeneration']:raise ValueError('qualified Qwen3.5 conditional-generation checkpoint required')
    from transformers import AutoProcessor
    processor=AutoProcessor.from_pretrained(str(root),local_files_only=True,trust_remote_code=False)
    if type(processor).__name__!='Qwen3VLProcessor':raise ValueError('unexpected local processor class')
    return processor


class VLMExportDataset:
    def __init__(self,root,expected_manifest):
        self.root=Path(root).resolve()
        if verify(self.root)!=expected_manifest:raise ValueError('owner export manifest differs')
        self.manifest_sha256=expected_manifest;receipt=read_json(self.root/'receipt.json')
        if receipt.get('schema') not in ('power-grid-env.design-multimodal-export/v1','power-grid-env.design-multimodal-export/v2') or receipt.get('passed') is not True or receipt.get('kind') not in ('sft','prompts'):raise ValueError('verified multimodal export required')
        if receipt['kind']=='sft' and receipt.get('split')!='train':raise ValueError('held-out examples cannot carry SFT labels')
        if receipt.get('split') not in ('train','dev','test') or receipt.get('mode') not in ('image','text','combined','schematic'):raise ValueError('unsupported split/modality')
        revisions=receipt.get('physics_code_sha256s')
        if receipt['schema'].endswith('/v2'):
            if (not isinstance(revisions,list) or not 1<=len(revisions)<=16 or
                    any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in revisions) or
                    revisions!=sorted(set(revisions)) or receipt.get('physics_code_sha256') not in revisions):
                raise ValueError('export physics revision inventory differs')
        elif revisions is not None:raise ValueError('mixed export revisions require v2')
        seen_revisions=set()
        raw=(self.root/'messages.jsonl').read_bytes()
        if digest(raw)!=receipt['messages_sha256'] or len(raw)>64_000_000:raise ValueError('bounded export messages differ')
        self.kind=receipt['kind'];self.split=receipt['split'];self.mode=receipt['mode'];self._records=tuple(raw.splitlines())
        if len(self._records)!=receipt['rows'] or not 1<=len(self._records)<=100_000:raise ValueError('export row inventory differs')
        seen=set()
        for i in range(len(self)):
            record=self.record(i);meta=record['metadata'];messages=record['messages']
            if meta['task_id'] in seen or meta['split']!=self.split or meta['modality']!=self.mode or meta['corpus_manifest_sha256']!=receipt['dataset_manifest_sha256']:raise ValueError('record metadata differs')
            seen.add(meta['task_id'])
            if revisions is not None:
                if meta.get('physics_code_sha256') not in revisions:raise ValueError('record physics revision differs')
                seen_revisions.add(meta['physics_code_sha256'])
            if len(messages)!=(2 if self.kind=='sft' else 1) or messages[0]['role']!='user' or not isinstance(messages[0]['content'],list):raise ValueError('unexpected conversation boundary')
            if self.kind=='sft' and (messages[1]['role']!='assistant' or not isinstance(messages[1]['content'],str)):raise ValueError('assistant supervision required')
            for block in messages[0]['content']:
                if block.get('type')=='image':self._image(block['image'])
                elif block.get('type')!='text' or not isinstance(block.get('text'),str):raise ValueError('unsupported public content block')
            count=sum(b['type']=='image' for b in messages[0]['content'])
            if count!={'image':3,'combined':3,'schematic':2,'text':0}[self.mode]:raise ValueError('modality image inventory differs')

        if revisions is not None and seen_revisions!=set(revisions):raise ValueError('unused export physics revision')

    def __len__(self):return len(self._records)

    def record(self,index):return json.loads(self._records[index])

    def _image(self,name):
        if not isinstance(name,str) or not re.fullmatch(r'images/[0-9a-f]{64}\.png',name):raise ValueError('safe content-addressed image path required')
        p=self.root/name
        if p.is_symlink() or not p.resolve().is_relative_to(self.root):raise ValueError('image outside owner export')
        raw=p.read_bytes()
        if digest(raw)!=p.stem or len(raw)>8_000_000 or len(raw)<24 or raw[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('image bytes differ')
        width,height=struct.unpack('>II',raw[16:24])
        if not 1<=width<=4096 or not 1<=height<=4096 or width*height>8_000_000:raise ValueError('image pixel budget exceeded')
        return str(p)

    def pack(self,processor,index,*,maximum_tokens=16384):
        import torch
        record=self.record(index);messages=record['messages']
        for block in messages[0]['content']:
            if block['type']=='image':block['image']=self._image(block['image'])
        image_count=sum(b['type']=='image' for b in messages[0]['content'])
        kwargs=dict(tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)
        packed=processor.apply_chat_template(messages,add_generation_prompt=self.kind=='prompts',**kwargs)
        ids=packed['input_ids'];attention=packed['attention_mask']
        if ids.ndim!=2 or ids.shape[0]!=1 or ids.shape!=attention.shape or not 1<=ids.shape[1]<=maximum_tokens:raise ValueError('token budget/axis mismatch; truncation is forbidden')
        prefix=ids.shape[1]
        if self.kind=='sft':
            prompt=processor.apply_chat_template(messages[:1],add_generation_prompt=True,**kwargs)
            prefix=prompt['input_ids'].shape[1]
            if prefix>=ids.shape[1] or not torch.equal(ids[:,:prefix],prompt['input_ids']):raise ValueError('native prompt/full prefix differs; unsafe loss mask')
            labels=ids.clone();labels[:,:prefix]=-100;labels[attention==0]=-100
            if not bool((labels!=-100).any()):raise ValueError('empty assistant supervision')
            packed['labels']=labels
        if image_count:
            if 'pixel_values' not in packed or 'image_grid_thw' not in packed or packed['image_grid_thw'].shape[0]!=image_count or not bool(torch.isfinite(packed['pixel_values']).all()):raise ValueError('native visual inputs missing or nonfinite')
            token_id=processor.tokenizer.convert_tokens_to_ids(processor.image_token)
            image_positions=ids==token_id
            if not bool(image_positions.any()):raise ValueError('native image tokens absent')
            if self.kind=='sft' and not bool((packed['labels'][image_positions]==-100).all()):raise ValueError('image tokens entered assistant loss')
        elif 'pixel_values' in packed:raise ValueError('text-only ablation contains pixels')
        return packed,dict(task_id=record['metadata']['task_id'],tokens=int(ids.shape[1]),prompt_tokens=int(prefix),images=image_count,
            assistant_tokens=int((packed['labels']!=-100).sum()) if self.kind=='sft' else 0,kind=self.kind,split=self.split,mode=self.mode)
