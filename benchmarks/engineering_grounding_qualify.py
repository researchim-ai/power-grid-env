"""Native admission of image,text and missing-information perception labels."""
import argparse
import hashlib
import json
from pathlib import Path
from benchmarks.engineering_grounding_runtime import load_records,supervised_batch
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import seal


def qualify(root,anchor,target,target_sha,out):
    dest=Path(out)
    if dest.exists():raise ValueError('fresh perception preflight required')
    records=load_records(root,anchor)
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    import torch
    torch.set_num_threads(4);t=local_target(target,target_sha);processor=local_qwen35_processor(t['local_checkpoint'],t['config_sha256'])
    rows=[];failures=[]
    for index,r in enumerate(records):
        for mode in ('image','text','none'):
            try:
                batch,meta=supervised_batch(processor,r,Path(root)/r['image'],mode);rows.append(dict(index=index,mode=mode,**meta))
            except (ValueError,RuntimeError) as exc:failures.append(dict(index=index,mode=mode,error=str(exc)[:1000]))
        if (index+1)%64==0:print(json.dumps(dict(records=index+1,total=len(records),failures=len(failures))),flush=True)
    report=dict(schema='power-grid-env.grounding-native-admission/v1',passed=bool(records) and not failures,source_records=len(records),
        expected_batches=3*len(records),checked_batches=len(rows),failures=failures,corpus_manifest_sha256=anchor,target_sha256=target_sha,
        maximum_prompt_tokens=max((r['prompt_tokens'] for r in rows),default=0),rows=rows,weights_loaded=False,
        runtime_sha256=hashlib.sha256(Path(__file__).with_name('engineering_grounding_runtime.py').read_bytes()).hexdigest(),
        scope='Readout+abstention pixel/prefix/label admission;missing-information labels are null;not VLM optimization or policy success')
    write_json(dest/'summary.json',report);seal(dest);return {k:v for k,v in report.items() if k!='rows'}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for f in ('corpus','manifest-sha256','target','target-sha256','out'):p.add_argument('--'+f,required=True)
    a=p.parse_args();print(json.dumps(qualify(a.corpus,a.manifest_sha256,a.target,a.target_sha256,a.out)))
