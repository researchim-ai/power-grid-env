"""Experimental native inference of a complete admitted-mixture phase.

Requires owner-pinned checkpoint/source/contract identities;no deployment gate.
"""
import argparse
import json
from pathlib import Path
import sys
from benchmarks.engineering_learning_gate import strict_contract
from benchmarks.engineering_protocol import render_history
from benchmarks.engineering_protocol_policy import adapt_completion
from benchmarks.engineering_trainer_v3 import DEPENDENCIES
from benchmarks.engineering_student_dataset import source_sha
from power_grid_env.study_artifacts import read_json,verify


def receipt(root,anchor,target_sha):
    if verify(root)!=anchor:raise ValueError('owner v3 checkpoint differs')
    from power_grid_env.evidence import package_fingerprint
    r=read_json(Path(root)/'summary.json');identity=r.get('identity',{});recipe=r.get('recipe',{})
    if (r.get('schema')!='power-grid-env.trainer-v3/v1' or identity.get('target_sha256')!=target_sha or identity.get('core_sha256')!=package_fingerprint() or
        identity.get('source_sha256')!={n:source_sha(n) for n in DEPENDENCIES} or recipe.get('contract')!=strict_contract().model_dump() or
        type(r.get('completed_steps')) is not int or r['completed_steps']<1 or r['completed_steps']!=recipe.get('steps') or
        r.get('schedule_complete') is not True or r.get('frozen_base_unchanged') is not True or r.get('adapter_roundtrip_exact') is not True or
        not isinstance(r.get('adapter_names'),list) or not r['adapter_names'] or not (Path(root)/'adapter.safetensors').is_file()):
        raise ValueError('v3 phase is incomplete or source/target/contract differs')
    return r


def restore(model,root,r):
    from safetensors.torch import load_file
    from benchmarks.engineering_trainer_v2 import LoraSpec,attach_language_lora
    from power_grid_env.qwen35_training import restore_adapters
    names=attach_language_lora(model,LoraSpec.model_validate(r['recipe']['lora']))
    if names!=r['adapter_names']:raise ValueError('v3 module selection differs')
    restore_adapters(model,load_file(str(Path(root)/'adapter.safetensors')));model.eval();model.requires_grad_(False)


def main():
    p=argparse.ArgumentParser()
    for name in ('target','target-sha256','adapter','adapter-manifest-sha256'):p.add_argument('--'+name,required=True)
    a=p.parse_args();r=receipt(a.adapter,a.adapter_manifest_sha256,a.target_sha256)
    from power_grid_env.qwen35_training import local_target,load_native
    import torch
    processor,model=load_native(local_target(a.target,a.target_sha256),4);restore(model,a.adapter,r);contract=strict_contract()
    for line in sys.stdin:
        try:
            request=json.loads(line)
            if set(request)!= {'messages'}:raise ValueError('only public messages are accepted')
            messages=request['messages'];rendered,kwargs=render_history(messages,contract)
            batch=processor.apply_chat_template(rendered,add_generation_prompt=True,tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False,**kwargs)
            n=int(batch['input_ids'].shape[1])
            if n+contract.action_tokens>contract.context_tokens:raise ValueError('context_budget_exceeded:no truncation')
            with torch.inference_mode():
                result=model.generate(**batch,do_sample=False,num_beams=1,max_new_tokens=contract.action_tokens,use_cache=True,logits_to_keep=1,
                    pad_token_id=processor.tokenizer.pad_token_id,eos_token_id=processor.tokenizer.eos_token_id)
            ids=result[0,n:];raw=processor.tokenizer.decode(ids,skip_special_tokens=True)
            text=adapt_completion(raw,contract,json.loads(messages[1]['content'])['task']['tools'])
            print(json.dumps(dict(text=text,protocol_completion=raw,input_tokens=n,output_tokens=len(ids),token_budget_exhausted=len(ids)>=contract.action_tokens)),flush=True)
        except Exception as exc:print(json.dumps(dict(error=type(exc).__name__,detail=str(exc)[:500])),flush=True)

if __name__=='__main__':main()
