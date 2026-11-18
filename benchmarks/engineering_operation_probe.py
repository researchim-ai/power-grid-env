"""Read-only operation-name ablation on the fixed pilot's two catalog states.

Removes opaque menu IDs and argument binding. This isolates operation selection;
results do not count as complete actions or an engineering workflow.
"""
import argparse,copy,json
from pathlib import Path
from benchmarks.engineering_skill_pilot import check_plan
from benchmarks.engineering_skill_tasks import score
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,read_json,seal


def messages(card):
    c=copy.deepcopy(card);tools=c.pop('tools')
    c['allowed_operations']=sorted(t['function']['name'] for t in tools)
    instruction=('OPERATION-PROBE/V1. Choose the next operation for the public goal and current state. '
                 'Return only JSON {"operation":"one allowed name"}, without arguments. '
                 'Validation does not establish electrical feasibility. A fresh study can still violate '
                 'constraints. Construction/repair must satisfy every declared constraint before submit.')
    return [dict(role='system',content=instruction),dict(role='user',content=json.dumps(c,separators=(',',':')))]


def run(plan,plan_anchor,result,result_anchor,target,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh operation probe required')
    if verify(result)!=result_anchor:raise ValueError('owner skill result differs')
    receipt=read_json(Path(result)/'summary.json')
    if receipt.get('complete') is not True or receipt['plan_manifest_sha256']!=plan_anchor:raise ValueError('complete matching skill result required')
    data,p=check_plan(plan,plan_anchor)
    from power_grid_env.qwen35_training import local_target,load_native,restore_adapters
    from benchmarks.engineering_trainer_v2 import attach_language_lora,LoraSpec
    from safetensors.torch import load_file
    import torch
    torch.manual_seed(p['seed']);processor,model=load_native(local_target(target,p['target_sha256']),4)
    examples=[e for e in p['exemplars'] if e['skill']=='choice'];rows=[];root.mkdir(parents=True)
    for candidate in ('base','adapter32'):
        if candidate=='adapter32':
            names=attach_language_lora(model,LoraSpec.model_validate(p['lora']))
            if names!=receipt['adapter_names']:raise ValueError('skill adapter modules differ')
            restore_adapters(model,load_file(str(Path(result)/'adapter.safetensors')));model.eval()
        for example in examples:
            r=data.rows[example['index']];m=messages(r['card']);expected=dict(operation=r['action']['name'])
            inputs=processor.apply_chat_template(m,add_generation_prompt=True,tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)
            n=inputs['input_ids'].shape[1]
            if n+128>8192:raise ValueError('operation probe context exceeds budget')
            with torch.inference_mode():ids=model.generate(**inputs,do_sample=False,num_beams=1,max_new_tokens=128,use_cache=True,logits_to_keep=1,
                pad_token_id=processor.tokenizer.pad_token_id,eos_token_id=processor.tokenizer.eos_token_id)
            raw=processor.tokenizer.decode(ids[0,n:],skip_special_tokens=True)
            item=dict(candidate=candidate,row_id=example['row_id'],expected=expected,completion=raw,**score(raw,expected));rows.append(item)
            write_json(root/'partial.json',dict(complete=False,rows=rows));print(json.dumps(item),flush=True)
    r=dict(schema='power-grid-env.operation-name-probe/v1',complete=True,plan_manifest_sha256=plan_anchor,result_manifest_sha256=result_anchor,rows=rows,
        correct={c:sum(r['exact'] for r in rows if r['candidate']==c) for c in ('base','adapter32')},cases_per_candidate=len(examples),
        optimizer_run=False,scope='Posthoc train-state operation-only prompt ablation;not full action,independent test or proof of a causal failure mechanism')
    write_json(root/'summary.json',r);seal(root);return r


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for f in ('plan','plan-manifest-sha256','result','result-manifest-sha256','target','out'):p.add_argument('--'+f,required=True)
    a=p.parse_args();r=run(a.plan,a.plan_manifest_sha256,a.result,a.result_manifest_sha256,a.target,a.out)
    print({k:v for k,v in r.items() if k!='rows'})
