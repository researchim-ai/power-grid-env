"""Complete held-out greedy modality evaluation, including every invalid answer.

No labels are passed to the model. A separate host-owned indication grader
scores generated text. The small authored corpus does not establish general
engineering ability or an advantage over other simulators.
"""
from pathlib import Path
import time

from pydantic import BaseModel, ConfigDict, Field

from power_grid_env.study_artifacts import read_json, encoded, verify, seal
from power_grid_env.evidence import package_fingerprint
from power_grid_env.design_multimodal import IndicationGrader, verify_dataset
from power_grid_env.vlm_batches import VLMExportDataset
from power_grid_env.qwen35_training import local_target, load_native


class GenerationPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    maximum_new_tokens: int = Field(default=96, ge=32, le=256)
    threads: int = Field(default=4, ge=1, le=16)


def heldout_exports(collection_root, collection_manifest, corpus_root,
                    corpus_manifest, split):
    if split not in ('dev', 'test'):
        raise ValueError('held-out split required')
    root = Path(collection_root)
    if verify(root) != collection_manifest:
        raise ValueError('owner export collection differs')
    corpus = verify_dataset(corpus_root, corpus_manifest)
    expected = {r['id'] for r in corpus['rows'] if r['split'] == split}
    if not expected:
        raise ValueError('empty held-out split')
    collection = read_json(root / 'receipt.json')
    if collection['dataset_manifest_sha256'] != corpus_manifest:
        raise ValueError('private grading corpus and public exports differ')
    selected = [r for r in collection['exports'] if r['split'] == split]
    if len(selected) != 4 or {r['mode'] for r in selected} != {'image', 'text', 'combined', 'schematic'}:
        raise ValueError('complete four-modality evaluation required')
    data = []
    for row in selected:
        # Collection manifest is the owner anchor, not a candidate-supplied path.
        if row['path'] != split + '-' + row['mode']:
            raise ValueError('unexpected held-out export path')
        d = VLMExportDataset(root / row['path'], row['manifest_sha256'])
        if (d.kind != 'prompts' or d.split != split or d.mode != row['mode'] or
                {d.record(i)['metadata']['task_id'] for i in range(len(d))} != expected):
            raise ValueError('all held-out tasks must appear exactly once in each modality')
        data.append(d)
    return data


def summarize(rows, task_count):
    modes = ('image', 'text', 'combined', 'schematic')
    result = {}
    for mode in modes:
        selected = [r for r in rows if r['mode'] == mode]
        if len(selected) != task_count or len({r['task_id'] for r in selected}) != task_count:
            raise ValueError('incomplete modality denominator')
        result[mode] = dict(examples=task_count,
            correct=sum(r['reward'] == 1. for r in selected),
            valid_json_decisions=sum(r['valid'] for r in selected),
            exact_match=sum(r['reward'] == 1. for r in selected) / task_count,
            token_budget_exhausted=sum(r['token_budget_exhausted'] for r in selected))
    if len(rows) != 4 * task_count:
        raise ValueError('unexpected evaluated rows')
    return result


def evaluate_base(target_path, target_sha256, collection_root, collection_manifest,
                  corpus_root, corpus_manifest, out, split='dev', plan=None):
    import torch
    start = time.monotonic()
    plan = GenerationPlan.model_validate(plan or {})
    root = Path(out)
    if root.exists():
        raise ValueError('fresh evaluation output required')
    producer = package_fingerprint()
    target = local_target(target_path, target_sha256)
    datasets = heldout_exports(collection_root, collection_manifest,
                               corpus_root, corpus_manifest, split)
    grader = IndicationGrader(corpus_root, corpus_manifest)
    processor, model = load_native(target, plan.threads)
    rows = []
    root.mkdir(parents=True, exist_ok=False)
    with (root / 'predictions.jsonl').open('wb') as stream:
        for dataset in datasets:
            for index in range(len(dataset)):
                batch, meta = dataset.pack(processor, index)
                if 'labels' in batch:
                    raise ValueError('held-out labels entered model input')
                tick = time.monotonic()
                with torch.no_grad():
                    ids = model.generate(**batch, do_sample=False, num_beams=1,
                        max_new_tokens=plan.maximum_new_tokens, use_cache=True,
                        logits_to_keep=1, pad_token_id=processor.tokenizer.pad_token_id,
                        eos_token_id=processor.tokenizer.eos_token_id)
                completion_ids = ids[0, batch['input_ids'].shape[1]:]
                reply = processor.tokenizer.decode(completion_ids, skip_special_tokens=True)
                reward = grader.reward_batch([reply], [meta['task_id']], dataset.mode)[0]
                # Structured validity is measured separately; a valid but wrong
                # answer remains in the accuracy denominator.
                import json
                try:
                    answer = json.loads(reply)
                    valid = grader.score(meta['task_id'], answer, dataset.mode)['valid']
                    # Batch callback additionally rejects duplicate JSON keys.
                    def pairs(items):
                        obj = {}
                        for k, v in items:
                            if k in obj:
                                raise ValueError('duplicate reply key')
                            obj[k] = v
                        return obj
                    json.loads(reply, object_pairs_hook=pairs)
                except (ValueError, RecursionError):
                    valid = False
                eos = processor.tokenizer.eos_token_id
                exhausted = len(completion_ids) >= plan.maximum_new_tokens and int(completion_ids[-1]) != eos
                row = dict(**meta, completion=reply, generated_tokens=len(completion_ids),
                    token_budget_exhausted=exhausted, reward=reward, valid=valid,
                    elapsed_s=time.monotonic()-tick)
                rows.append(row)
                stream.write(encoded(row) + b'\n')
                stream.flush()
                print('base evaluation', dataset.mode, index+1, '/', len(dataset),
                      'reward', reward, flush=True)
    metrics = summarize(rows, len(datasets[0]))
    if (package_fingerprint() != producer or verify(collection_root) != collection_manifest or
            verify(corpus_root) != corpus_manifest or local_target(target_path, target_sha256) != target):
        raise ValueError('evaluation inputs changed')
    result = dict(schema='power-grid-env.qwen35-base-evaluation/v1', passed=True,
        code_sha256=producer, model=target['model'], target_metadata_sha256=target_sha256,
        collection_manifest_sha256=collection_manifest, corpus_manifest_sha256=corpus_manifest,
        split=split, plan=plan.model_dump(), metrics=metrics, predictions=len(rows),
        device='cpu', dtype='float32', elapsed_s=time.monotonic()-start,
        decoding='greedy', labels_passed_to_model=False, adapters_loaded=False,
        partial_results_excluded=False, model_improvement_claimed=False,
        scope='Complete small authored topology-split diagnostic benchmark; '
              'displayed-indication accuracy only, no engineering-design or simulator-superiority claim.')
    (root / 'receipt.json').write_bytes(encoded(result))
    seal(root)
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    for name in ('target-path', 'target-sha256', 'collection-root', 'collection-manifest',
                 'corpus-root', 'corpus-manifest', 'out'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--split', choices=('dev', 'test'), default='dev')
    p.add_argument('--plan')
    args = vars(p.parse_args())
    if args['plan']:
        args['plan'] = read_json(args['plan'])
    print(evaluate_base(**args))
