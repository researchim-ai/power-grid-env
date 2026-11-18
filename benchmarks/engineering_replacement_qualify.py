"""Native text/visual admission of all replay-qualified replacement v2 targets."""
import argparse,json
from pathlib import Path
from benchmarks.engineering_card_batches import pack
from benchmarks.engineering_skill_tasks import identity
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,read_json,seal


def qualify(corpus,anchor,target,target_sha,out):
    if verify(corpus)!=anchor:raise ValueError('owner replacement data differs')
    r=read_json(Path(corpus)/'summary.json')
    if r.get('schema')!='power-grid-env.replacement-teaching-data/v2' or r['failed_actions_admitted']!=0:raise ValueError('qualified replacement v2 data required')
    rows=[json.loads(s) for s in (Path(corpus)/'records.jsonl').read_text().splitlines()]
    if len(rows)!=r['records'] or not rows:raise ValueError('replacement target inventory differs')
    root=Path(out)
    if root.exists():raise ValueError('fresh replacement admission required')
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    import torch
    torch.set_num_threads(4);t=local_target(target,target_sha);p=local_qwen35_processor(t['local_checkpoint'],t['config_sha256'])
    checked=[];failures=[]
    for i,row in enumerate(rows):
        if row['split']!='train' or row['profile']!='catalog-replacement/v2':raise ValueError('replacement role/profile differs')
        candidate=dict(row)
        if row['task']=='choice':
            if row['source']!='expert':raise ValueError('failed actions cannot become native gold')
            candidate['task']='full_action';candidate['answer']=next(m['action'] for m in row['candidates'] if m['choice']==row['answer']['choice'])
        elif row['task']!='readiness':raise ValueError('unexpected replacement objective')
        for mode in ('text','visual_hybrid'):
            try:_,meta=pack(p,candidate,mode);checked.append(dict(index=i,source=row['source'],**meta))
            except (ValueError,RuntimeError) as exc:failures.append(dict(index=i,mode=mode,error=str(exc)[:1000]))
        if (i+1)%64==0:print(json.dumps(dict(records=i+1,total=len(rows),failures=len(failures))),flush=True)
    result=dict(schema='power-grid-env.replacement-native-admission/v2',passed=not failures,corpus_manifest_sha256=anchor,target_sha256=target_sha,
                source_records=len(rows),expected_batches=2*len(rows),checked_batches=len(checked),failures=failures,rows=checked,
                maximum_prompt_tokens=max((x['prompt_tokens'] for x in checked),default=0),weights_loaded=False,
                source_sha256={f:identity(Path(__file__).with_name(f)) for f in ('engineering_replacement_qualify.py','engineering_card_batches.py','engineering_grounding.py')},
                scope='Native alias-action/readiness labels for v2 equipment budget,text/visual-hybrid;no optimizer updates or learned policy claim')
    write_json(root/'summary.json',result);seal(root);return {k:v for k,v in result.items() if k!='rows'}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for f in ('corpus','manifest-sha256','target','target-sha256','out'):p.add_argument('--'+f,required=True)
    a=p.parse_args();print(json.dumps(qualify(a.corpus,a.manifest_sha256,a.target,a.target_sha256,a.out)))
