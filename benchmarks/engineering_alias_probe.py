"""Read-only alias-equivariance probe on conditional-argument TRAIN states.

A bijective rename preserves topology,measurements and goal. This is not a new
physical network or heldout-family evaluation. No optimizer or score repair.
"""
import copy,json,random
from pathlib import Path
from benchmarks.engineering_native_staged_policy import load as load_policy
from benchmarks.engineering_physics_campaign import load_plan,TARGET
from benchmarks.engineering_skill_tasks import score
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,read_json,seal


def transform(value,mapping):
    if isinstance(value,str):return mapping.get(value,value)
    if isinstance(value,list):return [transform(x,mapping) for x in value]
    if isinstance(value,dict):return {k:transform(v,mapping) for k,v in value.items()}
    return value


def renamed(messages,answer,seed=424):
    messages=copy.deepcopy(messages);public=json.loads(messages[-1]['content']);scene=public['scene'];rng=random.Random(seed);mapping={}
    groups=[scene['nodes']]+[[r['id'] for r in scene[k]] for k in ('lines','routes','loads')]
    for ids in groups:
        ids=sorted(ids)
        if len(ids)<2:continue
        shift=rng.randrange(1,len(ids));mapping.update(zip(ids,ids[shift:]+ids[:shift]))
    if len(set(mapping.values()))!=len(mapping):raise ValueError('alias transform must be bijective')
    changed=transform(public,mapping);expected=transform(answer,mapping);inverse={v:k for k,v in mapping.items()}
    if transform(changed,inverse)!=public or transform(expected,inverse)!=answer:raise ValueError('alias transform does not roundtrip')
    if expected==answer:raise ValueError('probe must change a predicted public handle')
    messages[-1]['content']=json.dumps(changed,separators=(',',':'))
    return messages,expected,mapping


def run(plan,plan_anchor,result,result_anchor,target,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh alias diagnostic required')
    if verify(result)!=result_anchor:raise ValueError('owner physics checkpoint differs')
    data,p=load_plan(plan,plan_anchor);initial=p['initial'];rows=[]
    for candidate,weight,anchor,recipe,recipe_anchor in [('adapter40',initial['checkpoint'],initial['manifest_sha256'],initial['plan'],initial['plan_manifest_sha256']),
            ('physics78',result,result_anchor,plan,plan_anchor)]:
        actor=load_policy(weight,anchor,recipe,recipe_anchor,target,TARGET)
        for i in p['indices']:
            row=data.rows[i]
            if row['skill']!='conditional_arguments':continue
            messages,expected,mapping=renamed(row['messages'],row['answer'])
            raw=actor(dict(stage='arguments',messages=messages,maximum_completion_tokens=128))
            r=dict(candidate=candidate,row_id=row['id'],mapping=mapping,expected=expected,completion=raw,**score(raw,expected));rows.append(r)
            print(json.dumps(r),flush=True)
        del actor
        import gc;gc.collect()
    summary=dict(schema='power-grid-env.alias-equivariance-probe/v1',plan_manifest_sha256=plan_anchor,result_manifest_sha256=result_anchor,rows=rows,
        correct={c:sum(r['exact'] for r in rows if r['candidate']==c) for c in ('adapter40','physics78')},cases_per_candidate=2,
        optimizer_run=False,scope='Posthoc public alias rename of two TRAIN argument states;not independent electrical cases,heldout family score or full workflow')
    write_json(root/'summary.json',summary);seal(root);return summary
