"""Read-only public constraint annotation and alias-presentation ablations.

Constraint summaries are computed solely from the transmitted public card. This
changes the observation profile,not model weights;it cannot qualify a training gain.
"""
import copy,json
from pathlib import Path
from benchmarks.engineering_constraint_tasks import assess
from benchmarks.engineering_alias_probe import renamed
from benchmarks.engineering_native_staged_policy import load as load_policy
from benchmarks.engineering_physics_campaign import load_plan,TARGET
from benchmarks.engineering_skill_tasks import score,identity
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,seal


def annotated(request):
    if request.get('stage') not in ('operation','arguments'):raise ValueError('annotation is not allowed on measurement-label prediction tasks')
    q=copy.deepcopy(request);public=json.loads(q['messages'][-1]['content'])
    public['public_constraint_status']=assess(public)
    public['observation_annotation']='public-measurement-summary/v1'
    q['messages'][-1]['content']=json.dumps(public,separators=(',',':'))
    return q


def run(plan,plan_anchor,result,result_anchor,target,out):
    root=Path(out)
    if root.exists():raise ValueError('fresh observation ablation required')
    if verify(result)!=result_anchor:raise ValueError('owner physics result differs')
    data,p=load_plan(plan,plan_anchor);parent=p['initial'];rows=[]
    versions=[('adapter40',parent['checkpoint'],parent['manifest_sha256'],parent['plan'],parent['plan_manifest_sha256']),
              ('physics78',result,result_anchor,plan,plan_anchor)]
    for candidate,weights,anchor,recipe,recipe_anchor in versions:
        actor=load_policy(weights,anchor,recipe,recipe_anchor,target,TARGET)
        for i in p['indices']:
            r=data.rows[i]
            if r['skill']=='conditional_arguments':
                m,expected,mapping=renamed(r['messages'],r['answer'])
                raw=actor(dict(stage='arguments',messages=m,maximum_completion_tokens=128))
                row=dict(candidate=candidate,probe='alias_rename',row_id=r['id'],mapping=mapping,expected=expected,completion=raw,**score(raw,expected));rows.append(row);print(json.dumps(row),flush=True)
            elif r['origin']=='paired' and r['skill']=='operation':
                for view in ('original','public_summary'):
                    q=dict(stage='operation',messages=r['messages'],maximum_completion_tokens=128)
                    if view=='public_summary':q=annotated(q)
                    raw=actor(q);row=dict(candidate=candidate,probe='operation_'+view,row_id=r['id'],expected=r['answer'],completion=raw,**score(raw,r['answer']))
                    rows.append(row);print(json.dumps(row),flush=True)
        del actor
        import gc;gc.collect()
    result=dict(schema='power-grid-env.public-observation-ablation/v1',plan_manifest_sha256=plan_anchor,result_manifest_sha256=result_anchor,rows=rows,
        score={c:{probe:dict(correct=sum(r['exact'] for r in rows if r['candidate']==c and r['probe']==probe),
              total=sum(r['candidate']==c and r['probe']==probe for r in rows)) for probe in ('alias_rename','operation_original','operation_public_summary')} for c in ('adapter40','physics78')},
        optimizer_run=False,source_sha256={f:identity(Path(__file__).with_name(f)) for f in ('engineering_observation_probe.py','engineering_alias_probe.py','engineering_constraint_tasks.py')},
        primary_scores_unchanged=True,scope='Posthoc TRAIN-state observation/presentation ablations;public summaries change the interface;not heldout transfer or weight-only gain')
    write_json(root/'summary.json',result);seal(root);return result
