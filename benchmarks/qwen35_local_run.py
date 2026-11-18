"""Isolated-interpreter entry point for source or installed local Qwen runtime."""
import argparse
from pathlib import Path
import sys


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('train', 'evaluate', 'evaluate-adapter', 'preflight'))
    parser.add_argument('--runtime-dir')
    parser.add_argument('--target-path', required=True)
    parser.add_argument('--target-sha256', required=True)
    parser.add_argument('--export-root')
    parser.add_argument('--export-manifest')
    parser.add_argument('--collection-root')
    parser.add_argument('--collection-manifest')
    parser.add_argument('--corpus-root')
    parser.add_argument('--corpus-manifest')
    parser.add_argument('--split', choices=('dev', 'test'), default='dev')
    parser.add_argument('--out')
    parser.add_argument('--plan')
    parser.add_argument('--training')
    parser.add_argument('--training-manifest')
    parser.add_argument('--expected-training-producer')
    args = vars(parser.parse_args())
    runtime = args.pop('runtime_dir')
    sys.path.insert(0, str(Path(runtime).resolve() if runtime else Path(__file__).resolve().parents[1]))
    from power_grid_env.qwen35_training import local_target, train_adapters
    from power_grid_env.qwen35_evaluation import evaluate_base
    from power_grid_env.qwen35_adapter_evaluation import evaluate_adapter
    from power_grid_env.study_artifacts import read_json
    action = args.pop('action')
    if action == 'preflight':
        target = local_target(args['target_path'], args['target_sha256'])
        print(dict(passed=True, model=target['model'], weights=target['weights']))
    else:
        if not args['out']:
            parser.error('--out required')
        keys = ('target_path', 'target_sha256', 'export_root', 'export_manifest', 'out') if action == 'train' else (
            'target_path', 'target_sha256', 'collection_root', 'collection_manifest',
            'corpus_root', 'corpus_manifest', 'out', 'split')
        if any(args[k] is None for k in keys):
            parser.error('all data anchors for the chosen action are required')
        kwargs = {k: args[k] for k in keys}
        if action == 'evaluate-adapter':
            for key in ('training', 'training_manifest', 'export_root', 'export_manifest'):
                if args[key] is None:
                    parser.error('adapter inference requires training and train-export anchors')
            kwargs.update(training=args['training'], training_manifest=args['training_manifest'],
                          train_export=args['export_root'], train_export_manifest=args['export_manifest'],
                          expected_training_producer=args['expected_training_producer'])
        if args['plan']:
            kwargs['plan'] = read_json(args['plan'])
        print({'train': train_adapters, 'evaluate': evaluate_base,
               'evaluate-adapter': evaluate_adapter}[action](**kwargs))
