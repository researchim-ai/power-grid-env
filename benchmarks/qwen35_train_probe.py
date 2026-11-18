"""Predeclared seen-training generation probes with answer-free model inputs."""
import argparse
from pathlib import Path
import shutil
import sys


def project(source, anchor, out):
    from power_grid_env.vlm_batches import VLMExportDataset
    from power_grid_env.study_artifacts import read_json, encoded, seal, digest, verify
    data = VLMExportDataset(source, anchor)
    if data.split != 'train' or data.kind != 'sft':
        raise ValueError('only pinned train SFT may be projected')
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    assets = set()
    records = []
    for i in range(len(data)):
        record = data.record(i)
        record['messages'] = record['messages'][:1]
        assets.update(b['image'] for b in record['messages'][0]['content'] if b['type'] == 'image')
        records.append(record)
    (root/'images').mkdir()
    for asset in assets:
        shutil.copyfile(Path(source)/asset, root/asset)
    raw = b''.join(encoded(r)+b'\n' for r in records)
    (root/'messages.jsonl').write_bytes(raw)
    receipt = read_json(Path(source)/'receipt.json')
    receipt.update(kind='prompts', messages_sha256=digest(raw), source_sft_manifest_sha256=anchor)
    (root/'receipt.json').write_bytes(encoded(receipt))
    if verify(source) != anchor:
        raise ValueError('source changed')
    seal(root)
    return VLMExportDataset(root, verify(root))


def selection(data):
    """First train record in each target pattern, before observing predictions."""
    import json
    found = {}
    for i in range(len(data)):
        answer = json.loads(data.record(i)['messages'][1]['content'])
        states = set(answer['motors'].values())
        pattern = ('below' if states == {'below_running_speed'} else
                   'running' if states == {'at_running_speed'} else 'mixed')
        found.setdefault(pattern, i)
    if set(found) != {'below', 'running', 'mixed'}:
        raise ValueError('three predeclared train patterns required')
    return [(k, found[k]) for k in ('below', 'mixed', 'running')]


def run(profile, training, training_manifest, out, runtime_dir=None):
    runtime = Path(runtime_dir).resolve() if runtime_dir else Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(runtime))
    from power_grid_env.study_artifacts import read_json, encoded, verify, seal
    from power_grid_env.evidence import package_fingerprint
    from power_grid_env.qwen35_mixed_training import MixedData, MixedPlan, SCHEMA, read_checkpoint
    from power_grid_env.qwen35_training import local_target, adapter_state, frozen_fingerprint
    from power_grid_env.qwen35_adapter_evaluation import load_adapter_model, generate_prediction
    from power_grid_env.qwen35_evaluation import GenerationPlan
    from power_grid_env.design_multimodal import IndicationGrader
    import torch
    root = Path(out)
    if root.exists(): raise ValueError('fresh probe output required')
    if verify(training) != training_manifest: raise ValueError('owner training differs')
    p = read_json(profile)
    report = read_json(Path(training)/'receipt.json')
    producer = package_fingerprint()
    plan = MixedPlan.model_validate(p['training_plan'])
    data = MixedData(p['collection_root'], p['collection_manifest'])
    if (report['schema'] != SCHEMA or report['code_sha256'] != producer or
            not report['passed'] or not report['complete'] or report['plan'] != plan.model_dump() or
            report['target_metadata_sha256'] != p['target_sha256'] or
            report['collection_manifest_sha256'] != p['collection_manifest']):
        raise ValueError('complete current pinned recipe required')
    target = local_target(p['target_path'], p['target_sha256'])
    ckroot = Path(training)/report['last_checkpoint']
    read_checkpoint(ckroot, report['last_checkpoint_manifest'],
        {k: report[k] for k in ('schema', 'code_sha256', 'model', 'target_metadata_sha256',
            'collection_manifest_sha256', 'corpus_manifest_sha256', 'plan', 'observation_ids')},
        data.schedule(plan), plan)
    chosen = selection(data.datasets['image'])
    root.mkdir(parents=True, exist_ok=False)
    datasets = {mode: project(d.root, d.manifest_sha256, root/mode) for mode, d in data.datasets.items()}
    generation = GenerationPlan.model_validate(p['generation_plan'])
    processor, model = load_adapter_model(target, report, plan.adapter_plan(), ckroot, generation.threads)
    before = adapter_state(model)
    grader = IndicationGrader(p['corpus_root'], p['corpus_manifest'])
    rows = []
    for mode, d in datasets.items():
        for pattern, index in chosen:
            row = generate_prediction(model, processor, d, index, generation, grader)
            rows.append(dict(pattern=pattern, index=index, **row))
            print('seen-train probe',mode,pattern,row['reward'],flush=True)
    if (frozen_fingerprint(model) != report['frozen_backbone_sha256'] or
            any(not torch.equal(v,before[k]) for k,v in adapter_state(model).items()) or
            verify(training) != training_manifest or package_fingerprint() != producer):
        raise ValueError('model or source changed')
    data.check_unchanged()
    result = dict(passed=True, code_sha256=producer, training_manifest_sha256=training_manifest,
        collection_manifest_sha256=p['collection_manifest'], selected=chosen, rows=rows,
        metrics={m:dict(examples=3,correct=sum(r['reward'] for r in rows if r['mode']==m)) for m in datasets},
        labels_passed_to_model=False, seen_train=True, heldout_evaluated=False, test_evaluated=False,
        scope='First below/mixed/running train example per mode, chosen from train labels before inference; '
              'schematic uses same IDs but requires refusal. Diagnostic subset, not train accuracy or generalization.')
    (root/'receipt.json').write_bytes(encoded(result))
    seal(root)
    return {k:v for k,v in result.items() if k!='rows'}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for k in ('profile','training','training-manifest','out'):p.add_argument('--'+k,required=True)
    p.add_argument('--runtime-dir')
    print(run(**vars(p.parse_args())))
