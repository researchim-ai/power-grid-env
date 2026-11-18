"""Sequential paired base/adapter dev evaluation with identical public budgets."""
import argparse
import json
from pathlib import Path
from benchmarks.engineering_rollout import ProcessPolicy,evaluate
from benchmarks.engineering_adapter import adapter_receipt
from power_grid_env.study_artifacts import seal,verify


def compare(python,target,target_sha,adapter,adapter_manifest,out,turns=12,new_tokens=64,timeout=180,public_view='raw',repeat_limit=0):
    if type(turns) is not int or not 8<=turns<=32:raise ValueError('comparison requires 8..32 actions, enough for authored assembly')
    if type(new_tokens) is not int or not 32<=new_tokens<=512:raise ValueError('invalid generation budget')
    if public_view not in ('raw','compact-pf-v1') or repeat_limit not in (0,2,3,4):raise ValueError('invalid observation/guard profile')
    adapter_receipt(adapter,adapter_manifest,target_sha)
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    common=[python,'-m','benchmarks.engineering_qwen_policy','--target',target,'--target-sha256',target_sha,
        '--new-tokens',str(new_tokens),'--context-tokens','16384','--threads','4','--public-view',public_view]
    summaries={}
    for role,extra in [('base',[]),('adapter',['--adapter',adapter,'--adapter-manifest-sha256',adapter_manifest])]:
        policy=ProcessPolicy(common+extra,timeout)
        try:summaries[role]=evaluate(policy,root/role,'dev',turns,repeat_limit)
        finally:policy.close()
    cases={}
    for role in summaries:
        rows=[json.loads(p.read_text()) for p in (root/role).glob('task-*.json')]
        cases[role]={r['task_id']:r for r in rows}
    if set(cases['base'])!=set(cases['adapter']) or len(cases['base'])!=7:raise ValueError('paired dev inventory differs')
    pairs=[dict(task_id=i,kind=cases['base'][i]['kind'],base_success=cases['base'][i]['success'],
        adapter_success=cases['adapter'][i]['success'],base_stop=cases['base'][i]['stop_reason'],
        adapter_stop=cases['adapter'][i]['stop_reason']) for i in sorted(cases['base'])]
    result=dict(schema='power-grid-env.engineering-paired-evaluation/v1',split='dev',tasks=7,
        maximum_turns=turns,maximum_new_tokens=new_tokens,public_view=public_view,repeat_limit=repeat_limit,adapter_manifest_sha256=adapter_manifest,
        base_manifest_sha256=verify(root/'base'),adapter_evaluation_manifest_sha256=verify(root/'adapter'),
        base_successes=summaries['base']['successes'],adapter_successes=summaries['adapter']['successes'],
        improved=sum(not p['base_success'] and p['adapter_success'] for p in pairs),
        regressed=sum(p['base_success'] and not p['adapter_success'] for p in pairs),pairs=pairs,
        scope='Seven authored topology-disjoint dev cases; no test tuning, visual qualification or general engineering/simulator ranking claim')
    (root/'summary.json').write_text(json.dumps(result,indent=2)+'\n');seal(root)
    return {k:v for k,v in result.items() if k!='pairs'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--python',required=True);p.add_argument('--target',required=True)
    p.add_argument('--target-sha256',required=True);p.add_argument('--adapter',required=True)
    p.add_argument('--adapter-manifest-sha256',required=True);p.add_argument('--out',required=True)
    p.add_argument('--turns',type=int,default=12);p.add_argument('--new-tokens',type=int,default=64)
    p.add_argument('--public-view',choices=['raw','compact-pf-v1'],default='raw');p.add_argument('--repeat-limit',type=int,default=0)
    a=p.parse_args();print(json.dumps(compare(a.python,a.target,a.target_sha256,a.adapter,a.adapter_manifest_sha256,a.out,a.turns,a.new_tokens,public_view=a.public_view,repeat_limit=a.repeat_limit)))

if __name__=='__main__':main()
