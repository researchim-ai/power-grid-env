"""Heldout-family visual readout regression:base versus adapter65.

One deterministic field/counterfactual pair per dev base network. No optimizer
or test data;counterfactuals are readouts,not electrical qualification cases.
"""
import argparse
import hashlib
import json
from pathlib import Path
from benchmarks.engineering_learning_gate import load_evaluation,strict_contract
from benchmarks.engineering_scenarios import ScenarioEngine
from benchmarks.engineering_grounding import scene_from_messages,render_scene,counterfactual,question_records,digest
from benchmarks.engineering_grounding_runtime import prompt_batch,score
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_v2_adapter import receipt,restore
from power_grid_env.study_artifacts import verify,seal


def evaluate(corpus,anchor,target,target_sha,adapter,adapter_anchor,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh heldout readout report required')
    cases=load_evaluation(corpus,anchor,'dev');bases={}
    for c in cases:
        if c['kind']=='repair':bases.setdefault(c['base_project_id'],c)
    root.mkdir(parents=True);records=[]
    for key,c in sorted(bases.items()):
        e=ScenarioEngine(c);initial=e.observation();response=e.apply('inspect_project',{})
        m=[dict(role='system',content='public'),dict(role='user',content=json.dumps(initial)),dict(role='assistant',content=json.dumps(dict(name='inspect_project',arguments={}))),dict(role='user',content=json.dumps(response))]
        scene,_=scene_from_messages(m);line=scene['lines'][int(key[:8],16)%len(scene['lines'])]['id'];other=counterfactual(scene,line)
        for variant,s in [('factual',scene),('counterfactual',other)]:
            q=next(q for q in question_records(s) if q['kind']=='line_rating' and q['subject']==line);png=render_scene(s);p=root/(key+'-'+variant+'.png');p.write_bytes(png)
            records.append(dict(**q,variant=variant,base_project_id=key,family_sha256=c['family_sha256'],split='dev',image=str(p),_scene=s))
    from power_grid_env.qwen35_training import local_target,load_native
    import torch,gc
    meta=receipt(adapter,adapter_anchor,target_sha,strict_contract());t=local_target(target,target_sha);results=[]
    for candidate in ('base','adapter65'):
        processor,model=load_native(t,4)
        if candidate=='adapter65':restore(model,adapter,meta)
        for r in records:
            inputs=prompt_batch(processor,r,r['image'],'image');n=inputs['input_ids'].shape[1]
            with torch.inference_mode():ids=model.generate(**inputs,do_sample=False,num_beams=1,max_new_tokens=128,use_cache=True,logits_to_keep=1,
                pad_token_id=processor.tokenizer.pad_token_id,eos_token_id=processor.tokenizer.eos_token_id)
            raw=processor.tokenizer.decode(ids[0,n:],skip_special_tokens=True)
            row=dict(candidate=candidate,base_project_id=r['base_project_id'],family_sha256=r['family_sha256'],variant=r['variant'],expected=r['answer'],completion=raw,**score(raw,r['answer']))
            results.append(row);write_json(root/'partial.json',dict(complete=False,results=results));print(json.dumps(row),flush=True)
        del model;gc.collect()
    report=dict(schema='power-grid-env.heldout-grounding-regression/v1',complete=True,source_evaluation_manifest_sha256=anchor,target_sha256=target_sha,adapter_manifest_sha256=adapter_anchor,
        split='dev',base_networks=len(bases),families=len({r['family_sha256'] for r in records}),records_per_candidate=len(records),results=results,
        exact_pairs={c:sum(all(r['semantic'] for r in results if r['candidate']==c and r['base_project_id']==b) for b in bases) for c in ('base','adapter65')},
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),scope='Fixed heldout-family passport perception regression,not full grid-policy quality or physical counterfactual validation')
    write_json(root/'summary.json',report);seal(root);return report


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for f in ('corpus','manifest-sha256','target','target-sha256','adapter','adapter-manifest-sha256','out'):p.add_argument('--'+f,required=True)
    a=p.parse_args();print(json.dumps(evaluate(a.corpus,a.manifest_sha256,a.target,a.target_sha256,a.adapter,a.adapter_manifest_sha256,a.out)))
