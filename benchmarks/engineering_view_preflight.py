"""Native train-only token preflight for a separately versioned public view."""
import argparse
import hashlib
import json
from pathlib import Path
from benchmarks.engineering_observations import referenced_messages
from benchmarks.engineering_qwen_policy import pack
from power_grid_env.study_artifacts import verify,read_json,seal


def preflight(processor,corpus,manifest,target_sha,out):
    root=Path(corpus)
    if verify(root)!=manifest:raise ValueError('owner train manifest differs')
    summary=read_json(root/'summary.json')
    if summary.get('split')!='train' or summary.get('model_target_sha256')!=target_sha:
        raise ValueError('matching train corpus required')
    dest=Path(out)
    if dest.exists():raise ValueError('fresh preflight output required')
    rows=[]
    for line in (root/'train-actions.jsonl').read_text().splitlines():
        record=json.loads(line)
        if record['split']!='train':raise ValueError('non-train record rejected')
        _,raw=pack(processor,record['messages'],16384,64)
        _,small=pack(processor,referenced_messages(record['messages']),16384,64)
        rows.append(dict(task_id=record['task_id'],raw_tokens=raw,compact_tokens=small))
    if len(rows)!=summary['examples']:raise ValueError('example denominator differs')
    raw=sum(r['raw_tokens'] for r in rows);compact=sum(r['compact_tokens'] for r in rows)
    result=dict(schema='power-grid-env.engineering-view-preflight/v1',profile='compact-pf-v1',examples=len(rows),
        raw_tokens=raw,compact_tokens=compact,token_reduction_fraction=1-compact/raw,
        maximum_raw_tokens=max(r['raw_tokens'] for r in rows),maximum_compact_tokens=max(r['compact_tokens'] for r in rows),
        rows=rows,corpus_manifest_sha256=manifest,target_sha256=target_sha,
        projection_sha256=hashlib.sha256(Path(__file__).with_name('engineering_observations.py').read_bytes()).hexdigest(),
        weights_loaded=False,scope='Train-prefix tokenizer measurement only; no runtime speedup, quality, VLM or gold-SFT claim')
    dest.mkdir(parents=True,exist_ok=False);(dest/'summary.json').write_text(json.dumps(result,indent=2)+'\n');seal(dest)
    return {k:v for k,v in result.items() if k!='rows'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--corpus',required=True);p.add_argument('--manifest-sha256',required=True)
    p.add_argument('--target',required=True);p.add_argument('--target-sha256',required=True);p.add_argument('--out',required=True)
    a=p.parse_args()
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    target=local_target(a.target,a.target_sha256)
    processor=local_qwen35_processor(target['local_checkpoint'],target['config_sha256'])
    print(json.dumps(preflight(processor,a.corpus,a.manifest_sha256,a.target_sha256,a.out)))

if __name__=='__main__':main()
