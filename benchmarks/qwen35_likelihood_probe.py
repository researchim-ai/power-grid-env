"""Native causal-prefix/cache checks and semantic-versus-format token NLL.

Uses the trained artifact's historical installed runtime explicitly. Four fixed
seen-train probes, no parameter update, dev/test tuning or performance claim.
"""
import argparse
from pathlib import Path
import re
import sys


def run(profile,training,training_manifest,probes,probes_manifest,out,runtime_dir):
    runtime=Path(runtime_dir).resolve();sys.path.insert(0,str(runtime))
    from power_grid_env.study_artifacts import read_json,encoded,seal,verify
    from power_grid_env.evidence import package_fingerprint
    from power_grid_env.qwen35_mixed_training import MixedData,MixedPlan
    from power_grid_env.qwen35_training import local_target,supervised_forward,adapter_state,frozen_fingerprint
    from power_grid_env.qwen35_adapter_evaluation import load_adapter_model
    from power_grid_env.vlm_batches import VLMExportDataset
    import torch
    if verify(training)!=training_manifest or verify(probes)!=probes_manifest:raise ValueError('owner anchors differ')
    p=read_json(profile);r=read_json(Path(training)/'receipt.json');pr=read_json(Path(probes)/'receipt.json')
    if r['code_sha256']!=package_fingerprint() or pr['training_manifest_sha256']!=training_manifest or not r['complete']:
        raise ValueError('explicit trained-producer runtime required')
    data=MixedData(p['collection_root'],p['collection_manifest']);plan=MixedPlan.model_validate(p['training_plan'])
    target=local_target(p['target_path'],p['target_sha256'])
    processor,model=load_adapter_model(target,r,plan.adapter_plan(),Path(training)/r['last_checkpoint'],plan.threads)
    before=adapter_state(model);rows=[]
    choices=[('text',label,index) for label,index in pr['selected']]+[('schematic',pr['selected'][0][0],pr['selected'][0][1])]
    for mode,label,index in choices:
        d=data.datasets[mode];sft,meta=d.pack(processor,index)
        prompt=VLMExportDataset(Path(probes)/mode,verify(Path(probes)/mode))
        batch,public=prompt.pack(processor,index)
        if 'labels' in batch:raise ValueError('labels entered generation controls')
        with torch.no_grad():
            loss,logits=supervised_forward(model,sft)
            direct=model(**batch,use_cache=False,logits_to_keep=1).logits
            if not torch.isfinite(direct).all():raise ValueError('nonfinite prompt logits')
            causal_error=float((logits[0,0]-direct[0,-1]).abs().max())
            kwargs=dict(do_sample=False,num_beams=1,max_new_tokens=2,logits_to_keep=1,
                pad_token_id=processor.tokenizer.pad_token_id,eos_token_id=processor.tokenizer.eos_token_id,
                return_dict_in_generate=True,output_scores=True)
            cached=model.generate(**batch,use_cache=True,**kwargs)
            full=model.generate(**batch,use_cache=False,**kwargs)
        differences=[]
        for a,b in zip(cached.scores,full.scores,strict=True):
            finite=torch.isfinite(a)&torch.isfinite(b)
            if not torch.equal(torch.isfinite(a),torch.isfinite(b)) or not torch.equal(a[~finite],b[~finite]):
                raise ValueError('nonfinite cache scores differ')
            differences.append(float((a[finite]-b[finite]).abs().max()))
        cache_error=max(differences)
        same=torch.equal(cached.sequences,full.sequences)
        answer=d.record(index)['messages'][1]['content']
        tokenized=processor.tokenizer(answer,add_special_tokens=False,return_offsets_mapping=True)
        targets=sft['labels'][sft['labels']!=-100]
        if tokenized['input_ids']!=targets[:len(tokenized['input_ids'])].tolist():raise ValueError('unsafe answer token alignment')
        spans=[m.span() for m in re.finditer(r'\b(?:true|false)\b|at_running_speed|below_running_speed|disconnected',answer)]
        mask=[any(a<end and b>start for start,end in spans) for a,b in tokenized['offset_mapping']]
        mask+= [False]*(len(targets)-len(mask))
        semantic=torch.tensor(mask,dtype=torch.bool)
        nll=torch.nn.functional.cross_entropy(logits[0].float(),targets,reduction='none')
        if not semantic.any() or semantic.all():raise ValueError('semantic/format token partitions required')
        row=dict(mode=mode,pattern=label,index=index,task_id=meta['task_id'],
            teacher_mean_nll=float(loss),semantic_tokens=int(semantic.sum()),
            semantic_mean_nll=float(nll[semantic].mean()),format_mean_nll=float(nll[~semantic].mean()),
            semantic_teacher_token_accuracy=float((logits[0].argmax(-1)[semantic]==targets[semantic]).float().mean()),
            causal_prefix_max_abs_error=causal_error,two_step_cache_max_abs_error=cache_error,
            two_step_greedy_tokens_equal=same)
        rows.append(row);print('likelihood/cache probe',row,flush=True)
        if causal_error>1e-3 or cache_error>1e-3 or not same:raise ValueError('causal-prefix/cache control failed')
    if (frozen_fingerprint(model)!=r['frozen_backbone_sha256'] or
            any(not torch.equal(v,before[k]) for k,v in adapter_state(model).items())):raise ValueError('parameters changed')
    imports={n:str(Path(m.__file__).resolve()) for n,m in tuple(sys.modules.items()) if n.startswith('power_grid_env') and getattr(m,'__file__',None)}
    if any(not Path(v).is_relative_to(runtime) for v in imports.values()):raise ValueError('mixed runtime imports')
    result=dict(passed=True,code_sha256=package_fingerprint(),training_manifest_sha256=training_manifest,
        probes_manifest_sha256=probes_manifest,rows=rows,fp32_absolute_tolerance=1e-3,
        prefix_or_generation_labels_passed=False,teacher_forced_tokens_explicitly_used_for_loss=True,
        model_updated=False,test_evaluated=False,imports=imports,
        scope='Four seen-train cases; raw first-assistant-token prefix parity and two-token cached/uncached '
              'generation only, not full-decode equivalence or quality. NLL class/value spans include flag and enum tokens.')
    if verify(training)!=training_manifest or verify(probes)!=probes_manifest:raise ValueError('source changed')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);(root/'receipt.json').write_bytes(encoded(result));seal(root)
    return {k:v for k,v in result.items() if k not in ('rows','imports')}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for k in ('profile','training','training-manifest','probes','probes-manifest','out','runtime-dir'):p.add_argument('--'+k,required=True)
    print(run(**vars(p.parse_args())))
