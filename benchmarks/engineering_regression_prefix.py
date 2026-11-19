"""Conditional next-token intervention: gold versus self-generated wrong prefix.

Model-only diagnostic. Counterfactual assistant text is never training data or a
physical-network case. No optimizer is created.
"""
import argparse
import json
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_focused_curriculum import FocusedDataset
from benchmarks.engineering_regression_diagnosis import field_masks
from benchmarks.engineering_staged_data import pack
from power_grid_env.study_artifacts import read_json,verify,seal


def run(focused,anchor,failed,failed_anchor,target,target_sha,out):
    root=Path(out)
    if root.exists() or verify(failed)!=failed_anchor:raise ValueError('fresh prefix proof and matching phase required')
    data=FocusedDataset(focused,anchor,target_sha)
    report=read_json(Path(failed)/'summary.json')
    if report.get('complete') is not True or report['identity']['focused_manifest_sha256']!=anchor:
        raise ValueError('complete matching failed phase required')
    from power_grid_env.qwen35_training import local_target,load_native,restore_adapters,frozen_fingerprint
    from benchmarks.engineering_trainer_v2 import attach_language_lora,LoraSpec
    from safetensors.torch import load_file
    import torch
    processor,model=load_native(local_target(target,target_sha),4)
    attach_language_lora(model,LoraSpec())
    restore_adapters(model,load_file(str(Path(failed)/'adapter.safetensors')))
    frozen=frozen_fingerprint(model)
    index=data.parent.pilot_indices()[0][4]
    row=data.parent.example(index)
    values={v:processor.tokenizer(v,add_special_tokens=False)['input_ids'] for v in ('false','true')}
    if any(len(ids)!=1 for ids in values.values()):raise ValueError('single boolean token required for this diagnostic')
    results=[]
    for fresh in (False,True):
        modified=dict(row,answer=dict(row['answer'],fresh_after=fresh))
        batch,meta=pack(processor,modified)
        ids=batch['input_ids'][batch['labels']!=-100]
        mask=field_masks(processor.tokenizer,ids,modified['answer'])['finished']
        position=next(i for i,m in enumerate(mask) if m)
        if int(ids[position])!=values['false'][0]:raise ValueError('finished target token differs')
        prefix=meta['prompt_tokens']+position
        model.eval()
        with torch.inference_mode():
            output=model(input_ids=batch['input_ids'][:,:prefix],attention_mask=batch['attention_mask'][:,:prefix],
                         use_cache=False,logits_to_keep=1)
            logits=output.logits[0,-1].float()
            probability=logits.softmax(-1)
        results.append(dict(preceding_fresh_after=fresh,finished_false_probability=float(probability[values['false'][0]]),
                            finished_true_probability=float(probability[values['true'][0]]),
                            greedy_next_token=processor.tokenizer.decode([int(logits.argmax())]),prefix_tokens=prefix,
                            assistant_prefix=processor.tokenizer.decode(batch['input_ids'][0,meta['prompt_tokens']:prefix],skip_special_tokens=False)))
    if frozen_fingerprint(model)!=frozen:raise ValueError('prefix diagnostic changed model base')
    root.mkdir(parents=True)
    result=dict(schema='power-grid-env.regression-prefix-intervention/v1',failed_phase_manifest_sha256=failed_anchor,
                focused_manifest_sha256=anchor,row_id=row['id'],intervention='change only preceding assistant fresh_after false→true',
                measurements=results,optimizer_updates=0,
                scope='One TRAIN conditional next-token intervention using the failed checkpoint; '
                      'counterfactual assistant prefixes are not SFT gold or power-flow cases. No all-runtime or general causal claim.')
    write_json(root/'summary.json',result);seal(root)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('focused-release','manifest-sha256','failed-phase','failed-manifest-sha256','target','target-sha256','out'):
        p.add_argument('--'+key,required=True)
    a=p.parse_args();print(json.dumps(run(a.focused_release,a.manifest_sha256,a.failed_phase,
        a.failed_manifest_sha256,a.target,a.target_sha256,a.out)))
