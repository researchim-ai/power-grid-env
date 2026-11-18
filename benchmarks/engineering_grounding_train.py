"""Two-image native VLM optimizer smoke;not a policy-learning experiment."""
import argparse
import hashlib
import json
from pathlib import Path
from benchmarks.engineering_grounding_runtime import load_records,select_pair,supervised_batch
from benchmarks.engineering_trainer_v2 import LoraSpec,attach_language_lora
from benchmarks.engineering_train import save_optimizer
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,seal


def run(corpus,anchor,target,target_sha,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh native visual smoke output required')
    records=load_records(corpus,anchor);pair=select_pair(records,'equipment_zoom')
    from power_grid_env.qwen35_training import local_target,load_native,adapter_state,restore_adapters,frozen_fingerprint,supervised_forward
    from safetensors.torch import save_file,load_file
    import torch
    torch.manual_seed(42);processor,model=load_native(local_target(target,target_sha),4)
    names=attach_language_lora(model,LoraSpec());frozen=frozen_fingerprint(model);before=adapter_state(model)
    visual_calls=[0]
    def count(*a):visual_calls[0]+=1
    hook=model.model.visual.register_forward_hook(count)
    params=[p for p in model.parameters() if p.requires_grad];optimizer=torch.optim.AdamW(params,lr=.0001,weight_decay=0.)
    history=[]
    for step,record in enumerate(pair):
        batch,meta=supervised_batch(processor,record,Path(corpus)/record['image'],'image')
        if 'pixel_values' not in batch or not meta['pixels_present']:raise ValueError('actual image tensors required')
        optimizer.zero_grad(set_to_none=True);loss,logits=supervised_forward(model,batch);loss.backward()
        if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in params):raise ValueError('missing/nonfinite visual-conditioned gradients')
        grad=torch.nn.utils.clip_grad_norm_(params,1.,error_if_nonfinite=True)
        if float(grad)<=0:raise ValueError('zero visual-conditioned gradient')
        optimizer.step();history.append(dict(step=step,record_id=record['record_id'],loss=float(loss.detach()),gradient_norm=float(grad),**meta))
        print(json.dumps(history[-1]),flush=True);del loss,logits
    if frozen_fingerprint(model)!=frozen:raise ValueError('frozen visual/text base changed')
    state=adapter_state(model)
    if all(torch.equal(v,before[k]) for k,v in state.items()):raise ValueError('visual-conditioned optimizer made no update')
    root.mkdir(parents=True)
    with torch.inference_mode():expected_loss,expected=supervised_forward(model,batch)
    save_file(state,str(root/'adapter.safetensors'));optimizer_names=save_optimizer(model,optimizer,root/'optimizer.safetensors')
    restore_adapters(model,{k:torch.zeros_like(v) for k,v in state.items()});restore_adapters(model,load_file(str(root/'adapter.safetensors')))
    with torch.inference_mode():actual_loss,actual=supervised_forward(model,batch)
    if not torch.equal(expected,actual) or not torch.equal(expected_loss,actual_loss):raise ValueError('visual checkpoint roundtrip differs')
    hook.remove()
    if visual_calls[0]<4:raise ValueError('native visual encoder was not exercised')
    r=dict(schema='power-grid-env.visual-optimizer-smoke/v1',corpus_manifest_sha256=anchor,target_sha256=target_sha,optimizer_steps=2,
        trainable_parameters=sum(p.numel() for p in params),adapter_names=names,optimizer_parameter_names=optimizer_names,
        visual_encoder_forward_calls=visual_calls[0],frozen_visual_and_text_base_unchanged=True,adapter_roundtrip_exact=True,history=history,
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),runtime_sha256=hashlib.sha256(Path(__file__).with_name('engineering_grounding_runtime.py').read_bytes()).hexdigest(),
        policy_quality_claim=False,scope='Actual native image-conditioned gradients on one train readout/counterfactual pair;not visual-policy improvement or heldout skill')
    write_json(root/'summary.json',r);seal(root);return r


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for f in ('corpus','manifest-sha256','target','target-sha256','out'):p.add_argument('--'+f,required=True)
    a=p.parse_args();print(json.dumps(run(a.corpus,a.manifest_sha256,a.target,a.target_sha256,a.out)))
