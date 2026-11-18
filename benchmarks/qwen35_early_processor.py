"""Native processor and optional installed rebuild of train-only early views."""
import argparse
import importlib.abc
from pathlib import Path
import runpy
import sys


def run(profile, out, runtime_dir=None, rebuild_profile=None, expected_rebuild_manifest=None):
    runtime = Path(runtime_dir).resolve() if runtime_dir else Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(runtime))
    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split('.')[0] in {'tests', 'benchmarks', 'andes', 'motulator'}:
                raise ImportError('reference imports blocked')
    sys.meta_path.insert(0, Block())
    from power_grid_env.qwen35_training import local_target, file_sha256
    from power_grid_env.vlm_batches import VLMExportDataset, local_qwen35_processor
    from power_grid_env.study_artifacts import read_json, encoded, verify, seal
    from power_grid_env.evidence import package_fingerprint
    from power_grid_env.qwen35_mixed_training import MixedData
    import torch
    torch.set_num_threads(4)
    p = read_json(profile)
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    target = local_target(p['target_path'], p['target_sha256'])
    processor = local_qwen35_processor(target['local_checkpoint'], target['config_sha256'])
    data = MixedData(p['collection_root'], p['collection_manifest'])
    collection = read_json(Path(p['collection_root'])/'receipt.json')
    rows = []
    for entry in collection['exports']:
        dataset = VLMExportDataset(Path(p['collection_root'])/entry['path'], entry['manifest_sha256'])
        if dataset.split not in ('train', 'dev'):
            raise ValueError('early curriculum cannot contain test exports')
        for i in range(len(dataset)):
            batch, meta = dataset.pack(processor, i)
            if (dataset.split == 'dev' and 'labels' in batch or
                    dataset.split == 'train' and not 0 < meta['assistant_tokens'] <= 96):
                raise ValueError('unsafe native target mask/budget')
            rows.append(meta)
            del batch
    if len(rows) != 300 or sum(r['split'] == 'train' for r in rows) != 252:
        raise ValueError('complete early curriculum packs required')
    replay = None
    if rebuild_profile:
        if not expected_rebuild_manifest:
            raise ValueError('owner full augmentation manifest required')
        # Run the same bounded builder with installed production imports. No
        # historical solver, external reference package or test code executes.
        builder = runpy.run_path(str(Path(__file__).with_name('design_train_early_observations.py')),
                                 run_name='early_observation_builder')
        builder['run'](rebuild_profile, root/'rebuilt')
        replay = verify(root/'rebuilt')
        if replay != expected_rebuild_manifest:
            raise ValueError('installed early data rebuild is not byte-identical')
    imports = {n: str(Path(m.__file__).resolve()) for n, m in tuple(sys.modules.items())
               if n.startswith('power_grid_env') and getattr(m, '__file__', None)}
    if any(not Path(path).is_relative_to(runtime) for path in imports.values()):
        raise ValueError('production imports escaped selected runtime')
    result = dict(schema='power-grid-env.qwen35-early-processor/v1', passed=True,
        code_sha256=package_fingerprint(), profile_sha256=file_sha256(profile),
        collection_manifest_sha256=p['collection_manifest'], packs=300, train_loss_masks=252,
        dev_prompts=48, test_exported=False, model_weights_loaded=False, model_trained=False,
        maximum_tokens=max(r['tokens'] for r in rows), rows=rows,
        rebuilt_manifest_sha256=replay, imports=imports, reference_imports_blocked=True,
        scope='Native processor-only checks; optional same-host installed full data rebuild, not model accuracy.')
    data.check_unchanged()
    (root/'receipt.json').write_bytes(encoded(result))
    seal(root)
    return {k: v for k, v in result.items() if k not in ('rows', 'imports')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for key in ('profile', 'out'):
        parser.add_argument('--'+key, required=True)
    for key in ('runtime-dir', 'rebuild-profile', 'expected-rebuild-manifest'):
        parser.add_argument('--'+key)
    print(run(**vars(parser.parse_args())))
