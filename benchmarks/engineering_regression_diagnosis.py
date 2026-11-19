"""Native reproduction and local-gradient diagnosis of two TRAIN regressions.

Negative gradient dot products establish local interference, not sole causality
of an entire finite AdamW trajectory. No optimizer updates or held-out queries.
"""
import argparse
import hashlib
import json
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_focused_curriculum import FocusedDataset
from benchmarks.engineering_focused_phase import parent_receipt
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.engineering_sft_runtime_v2 import NativePolicy
from benchmarks.engineering_value_loss import value_spans
from power_grid_env.study_artifacts import read_json,seal,verify


def field_masks(tokenizer,ids,answer):
    raw=tokenizer.decode(ids,skip_special_tokens=False)
    text,spans=value_spans(answer)
    if not raw.startswith(text):raise ValueError('field alignment differs')
    encoded=tokenizer(raw,add_special_tokens=False,return_offsets_mapping=True)
    values=ids.tolist() if hasattr(ids,'tolist') else list(ids)
    if encoded['input_ids']!=values:raise ValueError('field token roundtrip differs')
    result={}
    for key,(start,end) in zip(answer,spans):
        mask=[a<end and b>start for a,b in encoded['offset_mapping']]
        if not any(mask):raise ValueError('field payload absent')
        result[key]=mask
    return result


def run(focused,anchor,parent,parent_anchor,failed,failed_anchor,target,target_sha,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh regression diagnosis required')
    data=FocusedDataset(focused,anchor,target_sha)
    prior=parent_receipt(parent,parent_anchor,data,target_sha)
    if verify(failed)!=failed_anchor:raise ValueError('failed phase manifest differs')
    bad=read_json(Path(failed)/'summary.json')
    if (bad.get('schema')!='power-grid-env.focused-native-phase/v1' or bad.get('complete') is not True or
            bad['identity']['parent_manifest_sha256']!=parent_anchor or bad['identity']['focused_manifest_sha256']!=anchor):
        raise ValueError('matching completed phase required')
    for name,expected in bad['identity']['source_sha256'].items():
        if hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()!=expected:
            raise ValueError('failed-phase implementation drift')
    selected,_,original,_=data.selection()
    retention_indices=[original[4],original[7]] # mutation and equality, both regressed.
    focus_indices=[next(i for i in selected if data.rows[i]['skill']=='api_field' and data.rows[i]['field']=='fresh_after'),
                   next(i for i in selected if data.rows[i]['skill']=='comparison_field' and data.rows[i]['answer']==dict(violation=None))]
    from power_grid_env.qwen35_training import local_target,load_native,restore_adapters,supervised_forward,frozen_fingerprint
    from benchmarks.engineering_trainer_v2 import LoraSpec,attach_language_lora
    from safetensors.torch import load_file
    import torch
    torch.manual_seed(20261119)
    processor,model=load_native(local_target(target,target_sha),4)
    names=attach_language_lora(model,LoraSpec())
    if names!=prior['adapter_names']:raise ValueError('diagnostic adapters differ')
    frozen=frozen_fingerprint(model)
    observations=[]
    for name,path,receipt in [('parent',parent,prior),('failed',failed,bad)]:
        restore_adapters(model,load_file(str(Path(path)/'adapter.safetensors')));model.eval()
        actor=NativePolicy(processor,model)
        for index in retention_indices:
            row=data.parent.example(index)
            raw=actor(dict(messages=row['messages'],maximum_completion_tokens=128))
            source=next(r for r in receipt['after'] if r['row_id']==row['id'] and (name=='parent' or r['source']=='retention'))
            if raw!=source['completion']:raise ValueError('diagnostic generated replay differs')
            batch,meta=data.parent.batch(processor,index)
            with torch.inference_mode():loss,logits=supervised_forward(model,batch)
            ids=batch['input_ids'][batch['labels']!=-100]
            masks=field_masks(processor.tokenizer,ids,row['answer'])
            probabilities=logits[0].float().softmax(-1).gather(1,ids[:,None]).squeeze(1)
            predicted=logits[0].argmax(-1)
            fields={}
            for field,mask in masks.items():
                active=torch.tensor(mask,dtype=torch.bool)
                fields[field]=dict(mean_gold_probability=float(probabilities[active].mean()),
                                  gold_top1_fraction=float((predicted[active]==ids[active]).float().mean()),
                                  token_count=int(active.sum()),uniform_ce_coefficient=float(active.sum())/len(ids))
            observations.append(dict(candidate=name,row_id=row['id'],index=index,completion=raw,
                                     loss=float(loss),fields=fields,**meta))
            del logits,loss
    # Compute parent gradients under EXACTLY the historical uniform-CE objective.
    restore_adapters(model,load_file(str(Path(parent)/'adapter.safetensors')));model.eval()
    parameters=[p for p in model.parameters() if p.requires_grad]
    gradients=[]
    pairs=[]
    for old_index,new_index in zip(retention_indices,focus_indices):
        pair_gradients=[]
        pair_meta=[]
        for role,dataset,index in [('retention',data.parent,old_index),('focused',data,new_index)]:
            batch,meta=dataset.batch(processor,index)
            loss,logits=supervised_forward(model,batch)
            g=torch.autograd.grad(loss,parameters)
            pair_gradients.append([v.detach().clone() for v in g])
            row=dataset.example(index)
            ids=batch['input_ids'][batch['labels']!=-100]
            masks=field_masks(processor.tokenizer,ids,row['answer'])
            pair_meta.append(dict(role=role,index=index,row_id=row['id'],loss=float(loss.detach()),
                                  field_coefficients={k:sum(v)/len(ids) for k,v in masks.items()},**meta))
            del loss,logits,g
        left,right=pair_gradients
        dot=sum(float((a.double()*b.double()).sum()) for a,b in zip(left,right))
        ln=sum(float((a.double()*a.double()).sum()) for a in left)**.5
        rn=sum(float((b.double()*b.double()).sum()) for b in right)**.5
        if not ln>0 or not rn>0:raise ValueError('zero diagnostic gradient')
        pairs.append(dict(examples=pair_meta,gradient_dot=dot,gradient_cosine=dot/(ln*rn),
                          first_order_retention_ce_change_per_unit_focused_sgd_step=-dot,
                          locally_conflicting=dot<0))
        del pair_gradients,left,right
    if frozen_fingerprint(model)!=frozen:raise ValueError('diagnosis changed frozen base')
    root.mkdir(parents=True)
    result=dict(schema='power-grid-env.native-regression-diagnosis/v1',focused_manifest_sha256=anchor,
                parent_manifest_sha256=parent_anchor,failed_phase_manifest_sha256=failed_anchor,
                generated_replays=observations,gradient_pairs=pairs,optimizer_updates=0,
                frozen_base_unchanged=True,heldout_evaluated=False,
                source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                scope='Two original TRAIN regressions replayed natively and two parent local-gradient pairs. '
                      'CE coefficients are exact objective weights; interference is local, not sole cause of AdamW regression.')
    write_json(root/'summary.json',result);seal(root)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('focused-release','manifest-sha256','parent-pilot','parent-manifest-sha256','failed-phase',
                'failed-manifest-sha256','target','target-sha256','out'):p.add_argument('--'+key,required=True)
    a=p.parse_args()
    print(json.dumps(run(a.focused_release,a.manifest_sha256,a.parent_pilot,a.parent_manifest_sha256,
        a.failed_phase,a.failed_manifest_sha256,a.target,a.target_sha256,a.out)))
