"""Four predetermined fresh installed-wheel generations, one per modality."""
import argparse
import importlib.abc
from pathlib import Path
import sys


def run(profile, training, training_manifest, predictions, predictions_manifest, out, runtime_dir):
    runtime = Path(runtime_dir).resolve()
    sys.path.insert(0, str(runtime))
    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split('.')[0] in {'tests', 'benchmarks', 'andes', 'motulator'}:
                raise ImportError('reference imports blocked')
    sys.meta_path.insert(0, Block())
    from power_grid_env.qwen35_training import (
        local_target, load_native, attach_adapters, frozen_fingerprint, restore_adapters, adapter_state,
    )
    from power_grid_env.qwen35_mixed_training import MixedData, MixedPlan, SCHEMA, read_checkpoint
    from power_grid_env.qwen35_adapter_evaluation import audited_predictions, generate_prediction
    from power_grid_env.qwen35_evaluation import GenerationPlan, heldout_exports
    from power_grid_env.design_multimodal import IndicationGrader
    from power_grid_env.evidence import package_fingerprint
    from power_grid_env.study_artifacts import read_json, encoded, verify, seal
    from safetensors.torch import load_file
    import torch
    producer = package_fingerprint()
    p = read_json(profile)
    if verify(training) != training_manifest:
        raise ValueError('owner complete training differs')
    report = read_json(Path(training)/'receipt.json')
    target = local_target(p['target_path'], p['target_sha256'])
    data = MixedData(p['collection_root'], p['collection_manifest'])
    plan = MixedPlan.model_validate(p['training_plan'])
    if (report['schema'] != SCHEMA or report['code_sha256'] != producer or not report['complete'] or
            not report['passed'] or not report['frozen_backbone_unchanged'] or report['plan'] != plan.model_dump() or
            report['target_metadata_sha256'] != p['target_sha256'] or
            report['collection_manifest_sha256'] != p['collection_manifest'] or
            report['corpus_manifest_sha256'] != p['corpus_manifest']):
        raise ValueError('complete producer-pinned training required')
    schedule = data.schedule(plan)
    total = (len(schedule)+plan.accumulation-1)//plan.accumulation
    if report['last_checkpoint'] != f'checkpoints/update-{total:06d}':
        raise ValueError('checkpoint path differs')
    ckroot = Path(training)/report['last_checkpoint']
    ck = read_checkpoint(ckroot, report['last_checkpoint_manifest'],
        {k: report[k] for k in ('schema', 'code_sha256', 'model', 'target_metadata_sha256',
            'collection_manifest_sha256', 'corpus_manifest_sha256', 'plan', 'observation_ids',
            'schedule_sha256', 'export_manifests', 'frozen_backbone_sha256')}, schedule, plan)
    source, expected = audited_predictions(predictions, predictions_manifest,
                                           p['corpus_root'], p['corpus_manifest'])
    if (source['training_manifest_sha256'] != training_manifest or
            source['collection_manifest_sha256'] != p['collection_manifest'] or
            source['plan'] != p['generation_plan'] or source['split'] != 'dev'):
        raise ValueError('source prediction protocol differs')
    generation = GenerationPlan.model_validate(p['generation_plan'])
    torch.manual_seed(plan.seed)
    processor, model = load_native(target, generation.threads)
    names = attach_adapters(model, plan.adapter_plan())
    if names != report['trainable_modules'] or frozen_fingerprint(model) != report['frozen_backbone_sha256']:
        raise ValueError('native model differs')
    restore_adapters(model, load_file(str(ckroot/'adapter.safetensors')))
    before = adapter_state(model)
    datasets = heldout_exports(p['collection_root'], p['collection_manifest'],
                               p['corpus_root'], p['corpus_manifest'], 'dev')
    grader = IndicationGrader(p['corpus_root'], p['corpus_manifest'])
    rows = []
    for dataset in datasets:
        actual = generate_prediction(model, processor, dataset, 0, generation, grader)
        wanted = expected[(dataset.mode, actual['task_id'])]['prediction']
        if {k:v for k,v in actual.items() if k != 'elapsed_s'} != {k:v for k,v in wanted.items() if k != 'elapsed_s'}:
            raise ValueError('installed native generation differs')
        rows.append(actual)
        print('installed mixed generation exact', dataset.mode, flush=True)
    imports = {n: str(Path(m.__file__).resolve()) for n, m in tuple(sys.modules.items())
               if n.startswith('power_grid_env') and getattr(m, '__file__', None)}
    if (any(not Path(path).is_relative_to(runtime) for path in imports.values()) or
            frozen_fingerprint(model) != report['frozen_backbone_sha256'] or
            any(not torch.equal(v, before[k]) for k, v in adapter_state(model).items()) or
            verify(training) != training_manifest or verify(predictions) != predictions_manifest or
            package_fingerprint() != producer):
        raise ValueError('installed runtime, model or evidence changed')
    data.check_unchanged()
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    result = dict(passed=True, code_sha256=producer, source_predictions_manifest_sha256=predictions_manifest,
        training_manifest_sha256=training_manifest, replays=4, selection='first_index_0_of_each_modality',
        text_tokens_scores_exact=True, imports=imports, reference_imports_blocked=True, rows=rows,
        same_host=True, test_evaluated=False,
        scope='Four predetermined installed-wheel replays; not full48 replay or independent host.')
    (root/'receipt.json').write_bytes(encoded(result))
    seal(root)
    return {k:v for k,v in result.items() if k not in ('imports','rows')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for key in ('profile', 'training', 'training-manifest', 'predictions', 'predictions-manifest', 'out', 'runtime-dir'):
        parser.add_argument('--'+key, required=True)
    print(run(**vars(parser.parse_args())))
