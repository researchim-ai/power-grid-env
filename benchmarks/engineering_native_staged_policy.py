"""Owner-pinned experimental native policy for the public two-stage API.

Loads only complete local checkpoints. A failed learning gate remains failed;
loading for diagnostic rollouts is not deployment qualification or output repair.
"""
from pathlib import Path
from benchmarks.engineering_skill_tasks import identity
from power_grid_env.study_artifacts import verify,read_json
from power_grid_env.evidence import package_fingerprint


def checkpoint(root,anchor,plan,plan_anchor,target_sha):
    if verify(root)!=anchor or verify(plan)!=plan_anchor:raise ValueError('owner staged weights/recipe differ')
    r=read_json(Path(root)/'summary.json');p=read_json(Path(plan)/'summary.json')
    pairs={'power-grid-env.decision-pilot-result/v1':('power-grid-env.decision-pilot-plan/v1',40),
           'power-grid-env.physics-curriculum-result/v1':('power-grid-env.physics-curriculum-plan/v1',78)}
    expected=pairs.get(r.get('schema'))
    if expected is None or p.get('schema')!=expected[0] or r.get('steps')!=expected[1] or len(r.get('history',[]))!=expected[1]:raise ValueError('unsupported or incomplete staged checkpoint')
    if r.get('complete') is not True or r.get('frozen_base_unchanged') is not True or r.get('adapter_roundtrip_exact') is not True:
        raise ValueError('complete frozen-base/roundtrip evidence required')
    if r.get('plan_manifest_sha256')!=plan_anchor or p.get('target_sha256')!=target_sha or r.get('core_sha256')!=package_fingerprint():raise ValueError('staged model/core ancestry differs')
    if not p.get('source_sha256'):raise ValueError('staged source inventory required')
    for f,sha in p['source_sha256'].items():
        if identity(Path(__file__).with_name(f))!=sha:raise ValueError('staged checkpoint source changed:'+f)
    if not (Path(root)/'adapter.safetensors').is_file():raise ValueError('staged weights missing')
    return r,p


class NativePolicy:
    """Receives only stage/messages/token cap; no engine,reference or teacher."""
    def __init__(self,processor,model):self.processor=processor;self.model=model;self.calls=[]
    def __call__(self,request):
        import torch
        if set(request)!={'stage','messages','maximum_completion_tokens'} or request['stage'] not in ('operation','arguments') or request['maximum_completion_tokens']!=128:
            raise ValueError('qualified staged request required')
        messages=request['messages']
        if not isinstance(messages,list) or len(messages)!=2 or [m.get('role') for m in messages]!=['system','user'] or any(not isinstance(m.get('content'),str) for m in messages):
            raise ValueError('this staged policy profile requires public text messages')
        inputs=self.processor.apply_chat_template(messages,add_generation_prompt=True,tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)
        n=inputs['input_ids'].shape[1]
        if n+128>8192:raise ValueError('staged native context budget exceeded')
        self.model.eval()
        with torch.inference_mode():ids=self.model.generate(**inputs,do_sample=False,num_beams=1,max_new_tokens=128,use_cache=True,logits_to_keep=1,
            pad_token_id=self.processor.tokenizer.pad_token_id,eos_token_id=self.processor.tokenizer.eos_token_id)
        count=ids.shape[1]-n
        if not 0<count<=128:raise ValueError('staged native generation budget violated')
        raw=self.processor.tokenizer.decode(ids[0,n:],skip_special_tokens=True)
        if len(raw.encode())>8192:raise ValueError('staged native byte budget exceeded')
        self.calls.append(dict(stage=request['stage'],prompt_tokens=n,generated_tokens=count))
        return raw


def load(root,anchor,plan,plan_anchor,target,target_sha):
    r,p=checkpoint(root,anchor,plan,plan_anchor,target_sha)
    from power_grid_env.qwen35_training import local_target,load_native,restore_adapters
    from benchmarks.engineering_trainer_v2 import attach_language_lora,LoraSpec
    from safetensors.torch import load_file
    processor,model=load_native(local_target(target,target_sha),4)
    names=attach_language_lora(model,LoraSpec.model_validate(p['lora']))
    if names!=r['adapter_names']:raise ValueError('staged adapter module inventory differs')
    restore_adapters(model,load_file(str(Path(root)/'adapter.safetensors')))
    return NativePolicy(processor,model)
