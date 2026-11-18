import copy
import json
from pathlib import Path
import pytest
import torch
from PIL import Image
from power_grid_env.vlm_batches import VLMExportDataset,local_qwen35_processor
from power_grid_env.study_artifacts import encoded,digest,read_json,seal


def make_export(tmp_path,kind='sft',split='train',images=True):
    root=tmp_path/'export';(root/'images').mkdir(parents=True)
    image=tmp_path/'one.png';Image.new('RGB',(32,32),'white').save(image);name='images/'+digest(image.read_bytes())+'.png';(root/name).write_bytes(image.read_bytes())
    content=[dict(type='text',text='Read the current indication.')]
    if images:content += [dict(type='image',image=name) for _ in range(3)]
    messages=[dict(role='user',content=content)]
    if kind=='sft':messages.append(dict(role='assistant',content='{"motors":{"m":"at_running_speed"},"insufficient_observation":false}'))
    record=dict(messages=messages,metadata=dict(task_id='opaque',split=split,modality='image' if images else 'text',corpus_manifest_sha256='a'*64))
    raw=encoded(record)+b'\n';(root/'messages.jsonl').write_bytes(raw)
    (root/'receipt.json').write_bytes(encoded(dict(schema='power-grid-env.design-multimodal-export/v1',passed=True,kind=kind,split=split,mode=record['metadata']['modality'],rows=1,messages_sha256=digest(raw),dataset_manifest_sha256='a'*64)))
    return root,seal(root)


class Tokenizer:
    def convert_tokens_to_ids(self,value):return 99


class Processor:
    image_token='<image>';tokenizer=Tokenizer()
    def __init__(self,fault=None):self.fault=fault
    def apply_chat_template(self,messages,add_generation_prompt,**kw):
        count=sum(b['type']=='image' for b in messages[0]['content']);prefix=[1,2]+[99]*count
        full=len(messages)>1;ids=prefix+([7,8] if full else [])
        if self.fault=='prefix' and full:ids[0]=3
        result=dict(input_ids=torch.tensor([ids]),attention_mask=torch.ones((1,len(ids)),dtype=torch.long))
        if count:
            result.update(pixel_values=torch.ones((count*4,6)),image_grid_thw=torch.ones((count,3),dtype=torch.long))
            if self.fault=='pixels':result['pixel_values'][0,0]=float('nan')
            if self.fault=='grid':result['image_grid_thw']=torch.ones((count+1,3))
        return result


@pytest.mark.parametrize('images',[True,False])
def test_assistant_only_loss_mask_and_prompt_pixels_are_consistent(tmp_path,images):
    root,anchor=make_export(tmp_path,images=images);d=VLMExportDataset(root,anchor);batch,meta=d.pack(Processor(),0)
    assert meta['assistant_tokens']==2 and meta['images']==(3 if images else 0)
    assert torch.all(batch['labels'][0,:meta['prompt_tokens']]==-100)
    assert batch['labels'][0,-2:].tolist()==[7,8]
    if images:assert torch.all(batch['labels'][batch['input_ids']==99]==-100)
    changed=d.record(0);changed['messages'][0]['content'][0]['text']='changed'
    assert d.record(0)['messages'][0]['content'][0]['text']!='changed'


def test_heldout_prompt_has_no_supervision(tmp_path):
    root,anchor=make_export(tmp_path,kind='prompts',split='test');d=VLMExportDataset(root,anchor);batch,meta=d.pack(Processor(),0)
    assert 'labels' not in batch and meta['assistant_tokens']==0 and meta['split']=='test'


@pytest.mark.parametrize('fault',['prefix','pixels','grid','budget'])
def test_invalid_native_processing_never_silently_truncates_or_masks_wrong_prefix(tmp_path,fault):
    root,anchor=make_export(tmp_path);d=VLMExportDataset(root,anchor)
    with pytest.raises(ValueError):d.pack(Processor(fault),0,maximum_tokens=2 if fault=='budget' else 16384)


def test_heldout_SFT_is_forbidden_even_with_fresh_manifest(tmp_path):
    root,anchor=make_export(tmp_path,split='test')
    with pytest.raises(ValueError,match='held-out'):VLMExportDataset(root,anchor)


def test_asset_tampering_after_dataset_validation_is_rechecked_before_pack(tmp_path):
    root,anchor=make_export(tmp_path);d=VLMExportDataset(root,anchor);name=d.record(0)['messages'][0]['content'][1]['image']
    (root/name).write_bytes(b'replaced')
    with pytest.raises(ValueError):d.pack(Processor(),0)


@pytest.mark.parametrize('fault',['remote','traversal','extra_role'])
def test_untrusted_asset_locations_and_supervision_boundaries_rejected(tmp_path,fault):
    root,_=make_export(tmp_path,kind='prompts',split='dev');record=json.loads((root/'messages.jsonl').read_text())
    if fault=='extra_role':record['messages'].append(dict(role='assistant',content='hidden target'))
    else:record['messages'][0]['content'][1]['image']='https://example.org/image.png' if fault=='remote' else '../private.png'
    raw=encoded(record)+b'\n';(root/'messages.jsonl').write_bytes(raw);receipt=read_json(root/'receipt.json');receipt['messages_sha256']=digest(raw);(root/'receipt.json').write_bytes(encoded(receipt));(root/'manifest.json').unlink();anchor=seal(root)
    with pytest.raises(ValueError):VLMExportDataset(root,anchor)


def test_checkpoint_identity_checked_before_loading_any_processor(tmp_path):
    config=dict(model_type='text_only',architectures=['TextModel']);raw=encoded(config);(tmp_path/'config.json').write_bytes(raw)
    with pytest.raises(ValueError,match='config differs'):local_qwen35_processor(tmp_path,'a'*64)
    with pytest.raises(ValueError,match='conditional-generation'):local_qwen35_processor(tmp_path,digest(raw))
