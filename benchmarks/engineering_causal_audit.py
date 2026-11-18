"""Read-only native causal/cache/train-mode consistency checks.

Teacher-forced logits must not depend on future answer tokens. Generating an
image answer must not change later text generation for identical input/weights.
No optimizer updates or changes to original model quality scores.
"""
import json,hashlib
from pathlib import Path
from benchmarks.engineering_native_staged_policy import load as load_policy
from benchmarks.engineering_physics_campaign import load_plan,TARGET
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_skill_tasks import identity
from power_grid_env.study_artifacts import verify,seal


def compare(a,b):
    import torch
    aa=a.detach().float().cpu();bb=b.detach().float().cpu()
    if aa.shape!=bb.shape or not bool(torch.isfinite(aa).all()) or not bool(torch.isfinite(bb).all()):raise ValueError('causal comparison requires matching finite logits')
    return dict(maximum_absolute_difference=float((aa-bb).abs().max()),top_token_equal=int(aa.argmax())==int(bb.argmax()))


def run(plan,plan_anchor,result,result_anchor,target,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh causal audit required')
    if verify(result)!=result_anchor:raise ValueError('owner causal-audit model differs')
    data,p=load_plan(plan,plan_anchor);actor=load_policy(result,result_anchor,plan,plan_anchor,target,TARGET)
    from power_grid_env.qwen35_training import supervised_forward,frozen_fingerprint
    import torch
    model=actor.model;processor=actor.processor;frozen=frozen_fingerprint(model);rows=[]
    chosen=[]
    for i in p['indices']:
        r=data.rows[i]
        if (r['skill']=='operation' and r['answer']['operation'] in ('simulate','set_line_catalog')) or (r['skill']=='conditional_arguments' and not any(data.rows[j]['skill']=='conditional_arguments' for j in chosen)):
            chosen.append(i)
    for i in chosen:
        batch,mask,meta=data.batch(processor,i);n=meta['prompt_tokens'];ids=batch['input_ids'];labels=batch['labels'][batch['labels']!=-100]
        model.eval()
        with torch.inference_mode():_,full=supervised_forward(model,batch)
        model.train()
        with torch.inference_mode():_,training=supervised_forward(model,batch)
        model.eval()
        critical=[j for j,x in enumerate(mask) if x]
        positions=sorted(set([0,critical[0],critical[-1]]))
        for j in positions:
            prefix=dict(input_ids=ids[:,:n+j],attention_mask=batch['attention_mask'][:,:n+j])
            with torch.inference_mode():
                no_cache=model(**prefix,use_cache=False,logits_to_keep=1).logits[0,-1]
                cached=model(**prefix,use_cache=True,logits_to_keep=1).logits[0,-1]
            expected=int(labels[j]);probs=full[0,j].float().softmax(-1)
            row=dict(index=i,skill=meta['skill'],target_position=j,expected_token=processor.tokenizer.decode([expected]),
                preferred_token=processor.tokenizer.decode([int(full[0,j].argmax())]),gold_probability=float(probs[expected]),
                teacher_vs_prefix=compare(full[0,j],no_cache),cache_prefill=compare(no_cache,cached),train_vs_eval=compare(full[0,j],training[0,j]))
            rows.append(row);print(json.dumps(row),flush=True)
        del full,training
    # Actual generate() state isolation,including multimodal RoPE/cache state.
    text_index=chosen[0];m,_=data.messages(text_index)
    text=processor.apply_chat_template(m,add_generation_prompt=True,tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)
    def generate(inputs):
        with torch.inference_mode():return model.generate(**inputs,do_sample=False,num_beams=1,max_new_tokens=16,use_cache=True,logits_to_keep=1,
            pad_token_id=processor.tokenizer.pad_token_id,eos_token_id=processor.tokenizer.eos_token_id,return_dict_in_generate=True,output_scores=True)
    before=generate(text);first=before.scores[0].detach().clone();before_ids=before.sequences[0,text['input_ids'].shape[1]:].tolist();del before
    image_index=next(i for i in p['indices'] if data.rows[i]['skill']=='perception' and data.rows[i]['mode']=='image')
    m,_=data.messages(image_index);image=processor.apply_chat_template(m,add_generation_prompt=True,tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)
    visual=generate(image);del visual
    after=generate(text);after_ids=after.sequences[0,text['input_ids'].shape[1]:].tolist()
    state=dict(first_step_logits=compare(first,after.scores[0]),generated_ids_equal=before_ids==after_ids)
    if frozen_fingerprint(model)!=frozen:raise ValueError('causal audit changed frozen model parameters')
    max_error=max(x[k]['maximum_absolute_difference'] for x in rows for k in ('teacher_vs_prefix','cache_prefill','train_vs_eval'))
    passed=max_error<=.001 and state['first_step_logits']['maximum_absolute_difference']<=.001 and state['generated_ids_equal']
    summary=dict(schema='power-grid-env.native-causal-audit/v1',plan_manifest_sha256=plan_anchor,result_manifest_sha256=result_anchor,
        rows=rows,text_image_text=state,maximum_logit_difference=max_error,passed=passed,optimizer_run=False,frozen_base_unchanged=True,
        source_sha256=identity(__file__),scope='Read-only causal/train-mode/cache consistency on selected TRAIN prefixes;not policy improvement or all-runtime qualification')
    write_json(root/'summary.json',summary);seal(root);return summary
