"""Reproduce the owner-pinned raw-adapter study and separate schema curriculum."""
import argparse
from pathlib import Path
import sys


def run(action, profile, out, runtime_dir=None, adapter=None, adapter_manifest=None):
    runtime = Path(runtime_dir).resolve() if runtime_dir else Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(runtime))
    from power_grid_env.study_artifacts import read_json
    from power_grid_env.qwen35_adapter_evaluation import evaluate_adapter, compare_evaluations
    from power_grid_env.design_response_schema import build_collection
    p = read_json(profile)
    if action == 'evaluate':
        keys = ('target_path', 'target_sha256', 'collection_root', 'collection_manifest',
                'corpus_root', 'corpus_manifest', 'training', 'training_manifest',
                'train_export', 'train_export_manifest', 'split', 'plan', 'expected_training_producer')
        return evaluate_adapter(**{k: p[k] for k in keys}, out=out)
    if action == 'compare':
        if not adapter or not adapter_manifest:
            raise ValueError('owner adapter prediction anchor required')
        return compare_evaluations(p['base'], p['base_manifest'], adapter, adapter_manifest,
            p['corpus_root'], p['corpus_manifest'], out, p['expected_base_producer'])
    if action == 'curriculum':
        return build_collection(p['corpus_root'], p['corpus_manifest'], p['collection_root'],
                                p['collection_manifest'], out)
    raise ValueError('unknown study action')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('evaluate', 'compare', 'curriculum'))
    p.add_argument('--profile', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--runtime-dir')
    p.add_argument('--adapter')
    p.add_argument('--adapter-manifest')
    print(run(**vars(p.parse_args())))
