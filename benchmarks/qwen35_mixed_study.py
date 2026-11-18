"""Isolated owner-profile launcher for mixed training and schema dev evaluation."""
import argparse
from pathlib import Path
import sys


def run(action, profile, out, runtime_dir=None, resume=None, resume_manifest=None,
        stop_after_updates=None, training=None, training_manifest=None,
        base=None, base_manifest=None, adapter=None, adapter_manifest=None):
    runtime = Path(runtime_dir).resolve() if runtime_dir else Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(runtime))
    from power_grid_env.study_artifacts import read_json
    from power_grid_env.qwen35_mixed_training import train_mixed, evaluate_mixed
    from power_grid_env.qwen35_evaluation import evaluate_base
    from power_grid_env.qwen35_adapter_evaluation import compare_evaluations
    p = read_json(profile)
    shared = {k: p[k] for k in ('target_path', 'target_sha256', 'collection_root', 'collection_manifest')}
    if action == 'train':
        return train_mixed(**shared, out=out, plan=p['training_plan'], resume=resume,
            resume_manifest=resume_manifest, stop_after_updates=stop_after_updates)
    shared.update({k: p[k] for k in ('corpus_root', 'corpus_manifest')})
    if action == 'base':
        return evaluate_base(**shared, out=out, split='dev', plan=p['generation_plan'])
    if action == 'evaluate':
        if not training or not training_manifest:
            raise ValueError('owner training path and manifest required')
        return evaluate_mixed(**shared, training=training, training_manifest=training_manifest,
                              out=out, plan=p['generation_plan'])
    if action == 'compare':
        if not all((base, base_manifest, adapter, adapter_manifest)):
            raise ValueError('both owner prediction anchors required')
        return compare_evaluations(base, base_manifest, adapter, adapter_manifest,
                                   p['corpus_root'], p['corpus_manifest'], out)
    raise ValueError('unknown mixed study action')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('train', 'base', 'evaluate', 'compare'))
    for key in ('profile', 'out'):
        parser.add_argument('--'+key, required=True)
    for key in ('runtime-dir', 'resume', 'resume-manifest', 'training', 'training-manifest',
                'base', 'base-manifest', 'adapter', 'adapter-manifest'):
        parser.add_argument('--'+key)
    parser.add_argument('--stop-after-updates', type=int)
    print(run(**vars(parser.parse_args())))
