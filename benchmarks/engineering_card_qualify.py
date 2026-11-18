"""Full native card-label admission;separate versioned text/visual objectives."""
import argparse
from collections import Counter
import json
import hashlib
from pathlib import Path
from benchmarks.engineering_card_batches import pack
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,read_json,seal


def qualify(root,anchor,target,target_sha,out):
    if verify(root)!=anchor:raise ValueError('owner card corpus differs')
    root=Path(root);r=read_json(root/'summary.json');rows=[json.loads(x) for x in (root/'records.jsonl').read_text().splitlines()]
    if r['schema']!='power-grid-env.card-curriculum/v1' or len(rows)!=r['records'] or any(x['split']!='train' for x in rows):raise ValueError('card role/inventory differs')
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    import torch
    torch.set_num_threads(4);t=local_target(target,target_sha);processor=local_qwen35_processor(t['local_checkpoint'],t['config_sha256'])
    good=[];failed=[]
    for i,row in enumerate(rows):
        for mode in ('text','visual_hybrid'):
            try:batch,meta=pack(processor,row,mode);good.append(dict(index=i,**meta))
            except (ValueError,RuntimeError) as exc:failed.append(dict(index=i,mode=mode,error=str(exc)[:1000]))
        if (i+1)%64==0:print(json.dumps(dict(records=i+1,total=len(rows),failures=len(failed))),flush=True)
    result=dict(schema='power-grid-env.card-native-admission/v1',passed=bool(rows) and not failed,corpus_manifest_sha256=anchor,target_sha256=target_sha,
        records=len(rows),expected_batches=2*len(rows),checked_batches=len(good),failures=failed,maximum_prompt_tokens=max((g['prompt_tokens'] for g in good),default=0),
        rows=good,weights_loaded=False,source_sha256={f:hashlib.sha256(Path(__file__).with_name(f).read_bytes()).hexdigest() for f in ('engineering_card_batches.py','engineering_grounding.py')},
        scope='New-interface solver-completed teaching candidates,native token/pixel/label checks;not model training or a matched raw-interface quality gain')
    dest=Path(out)
    if dest.exists():raise ValueError('fresh card admission output required')
    write_json(dest/'summary.json',result);seal(dest);return {k:v for k,v in result.items() if k!='rows'}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for f in ('corpus','manifest-sha256','target','target-sha256','out'):p.add_argument('--'+f,required=True)
    a=p.parse_args();print(json.dumps(qualify(a.corpus,a.manifest_sha256,a.target,a.target_sha256,a.out)))
