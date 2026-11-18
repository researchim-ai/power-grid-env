"""Installed replay of the predeclared first dev observation in each modality."""
import argparse
import importlib.abc
from pathlib import Path
import sys


def run(profile, predictions, predictions_manifest, out, runtime_dir):
    runtime = Path(runtime_dir).resolve()
    sys.path.insert(0, str(runtime))
    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split('.')[0] in {'tests', 'benchmarks', 'andes', 'motulator'}:
                raise ImportError('reference imports blocked')
    sys.meta_path.insert(0, Block())
    from power_grid_env.study_artifacts import read_json, encoded, seal, verify
    from power_grid_env.evidence import package_fingerprint
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.qwen35_adapter_evaluation import (
        training_checkpoint, load_adapter_model, generate_prediction, audited_predictions)
    from power_grid_env.qwen35_evaluation import GenerationPlan, heldout_exports
    from power_grid_env.design_multimodal import IndicationGrader
    p = read_json(profile)
    receipt, expected = audited_predictions(predictions, predictions_manifest,
        p['corpus_root'], p['corpus_manifest'])
    if receipt['training_manifest_sha256'] != p['training_manifest'] or receipt['plan'] != p['plan']:
        raise ValueError('source prediction training/protocol differs')
    target = local_target(p['target_path'], p['target_sha256'])
    report, adapter_plan = training_checkpoint(p['training'], p['training_manifest'], target,
        p['target_sha256'], p['train_export'], p['train_export_manifest'], p['corpus_root'],
        p['corpus_manifest'], p['expected_training_producer'])
    plan = GenerationPlan.model_validate(p['plan'])
    datasets = heldout_exports(p['collection_root'], p['collection_manifest'],
                              p['corpus_root'], p['corpus_manifest'], p['split'])
    processor, model = load_adapter_model(target, report, adapter_plan, p['training'], plan.threads)
    grader = IndicationGrader(p['corpus_root'], p['corpus_manifest'])
    rows = []
    for data in datasets:
        row = generate_prediction(model, processor, data, 0, plan, grader)
        source = expected[(row['mode'], row['task_id'])]['prediction']
        if {k: v for k, v in row.items() if k != 'elapsed_s'} != {k: v for k, v in source.items() if k != 'elapsed_s'}:
            raise ValueError('installed generated text/tokens/score differ from source')
        rows.append(row)
        print('installed generation replay', data.mode, 'matched', flush=True)
    if any(not Path(m.__file__).resolve().is_relative_to(runtime)
           for n, m in sys.modules.items() if n.startswith('power_grid_env') and getattr(m, '__file__', None)):
        raise ValueError('mixed source/installed runtime')
    if verify(predictions) != predictions_manifest or verify(p['training']) != p['training_manifest']:
        raise ValueError('source evidence changed')
    output = Path(out)
    output.mkdir(parents=True, exist_ok=False)
    result = dict(passed=True, code_sha256=package_fingerprint(),
        source_predictions_manifest_sha256=predictions_manifest,
        training_manifest_sha256=p['training_manifest'], rows=rows, replays=4,
        reference_imports_blocked=True, runtime_imports_verified=True,
        generated_text_tokens_scores_exact=True, independent_host=False,
        scope='Predetermined first observation per modality, not full48 generation replay or an independent host.')
    (output/'receipt.json').write_bytes(encoded(result))
    seal(output)
    return {k: v for k, v in result.items() if k != 'rows'}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for name in ('profile', 'predictions', 'predictions-manifest', 'out', 'runtime-dir'):
        p.add_argument('--'+name, required=True)
    print(run(**vars(p.parse_args())))
