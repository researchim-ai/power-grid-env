"""Pinned Qwen raw JSON/native tool policy over the same owner tool interface."""
import argparse
import json
import sys
from benchmarks.engineering_protocol import RunContract,render_history,parse_native


def adapt_completion(raw,contract,allowed):
    if contract.protocol=='json-action-v1':return raw
    try:return json.dumps(parse_native(raw,allowed),ensure_ascii=False,separators=(',',':'))
    except ValueError:return 'INVALID_NATIVE_TOOL_CALL\n'+raw


def main():
    p=argparse.ArgumentParser();p.add_argument('--target',required=True);p.add_argument('--target-sha256',required=True)
    p.add_argument('--protocol',choices=['json-action-v1','qwen-native-v1','qwen-native-strict-v1'],required=True)
    p.add_argument('--public-view',choices=['raw','compact-pf-v1'],default='compact-pf-v1')
    p.add_argument('--action-tokens',type=int,default=256)
    p.add_argument('--maximum-actions',type=int,default=12)
    p.add_argument('--repeat-limit',type=int,default=2)
    p.add_argument('--context-tokens',type=int,default=16384)
    p.add_argument('--experimental-v2-adapter');p.add_argument('--adapter-manifest-sha256')
    a=p.parse_args()
    if bool(a.experimental_v2_adapter)!=bool(a.adapter_manifest_sha256):p.error('experimental adapter requires owner manifest')
    contract=RunContract(protocol=a.protocol,public_view=a.public_view,action_tokens=a.action_tokens,maximum_actions=a.maximum_actions,repeat_limit=a.repeat_limit,context_tokens=a.context_tokens)
    from power_grid_env.qwen35_training import local_target,load_native
    import torch
    metadata=None
    if a.experimental_v2_adapter:
        from benchmarks.engineering_v2_adapter import receipt
        metadata=receipt(a.experimental_v2_adapter,a.adapter_manifest_sha256,a.target_sha256,contract)
    target=local_target(a.target,a.target_sha256);processor,model=load_native(target,4)
    if metadata is not None:
        from benchmarks.engineering_v2_adapter import restore
        restore(model,a.experimental_v2_adapter,metadata)
    for line in sys.stdin:
        try:
            request=json.loads(line)
            if set(request)!={'messages'}:raise ValueError('public messages only')
            messages=request['messages'];rendered,kwargs=render_history(messages,contract)
            batch=processor.apply_chat_template(rendered,add_generation_prompt=True,tokenize=True,
                return_dict=True,return_tensors='pt',enable_thinking=False,**kwargs)
            n=int(batch['input_ids'].shape[1])
            if n+contract.action_tokens>contract.context_tokens:raise ValueError('context_budget_exceeded: no truncation')
            with torch.inference_mode():
                result=model.generate(**batch,do_sample=False,num_beams=1,max_new_tokens=contract.action_tokens,
                    use_cache=True,logits_to_keep=1,pad_token_id=processor.tokenizer.pad_token_id,eos_token_id=processor.tokenizer.eos_token_id)
            ids=result[0,n:];raw=processor.tokenizer.decode(ids,skip_special_tokens=True)
            allowed=json.loads(messages[1]['content'])['task']['tools']
            text=adapt_completion(raw,contract,allowed)
            print(json.dumps(dict(text=text,protocol_completion=raw,input_tokens=n,output_tokens=len(ids),
                token_budget_exhausted=len(ids)>=contract.action_tokens)),flush=True)
        except Exception as exc:
            print(json.dumps(dict(error=type(exc).__name__,detail=str(exc)[:500])),flush=True)

if __name__=='__main__':main()
