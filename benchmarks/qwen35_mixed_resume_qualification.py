"""Fresh-process installed-runtime continuation against a continuous native run."""
import argparse
import importlib.abc
from pathlib import Path
import sys


def run(profile, reference_checkpoint, reference_manifest, start_checkpoint,
        start_manifest, out, runtime_dir):
    runtime = Path(runtime_dir).resolve()
    sys.path.insert(0, str(runtime))
    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split('.')[0] in {'tests', 'benchmarks', 'andes', 'motulator'}:
                raise ImportError('reference imports blocked')
    sys.meta_path.insert(0, Block())
    from power_grid_env.qwen35_mixed_training import train_mixed
    from power_grid_env.qwen35_training import file_sha256
    from power_grid_env.evidence import package_fingerprint
    from power_grid_env.study_artifacts import read_json, encoded, seal, verify
    from safetensors.torch import load_file
    import torch
    if verify(reference_checkpoint) != reference_manifest or verify(start_checkpoint) != start_manifest:
        raise ValueError('owner continuous checkpoints differ')
    reference = read_json(Path(reference_checkpoint)/'receipt.json')
    start = read_json(Path(start_checkpoint)/'receipt.json')
    if reference['completed_updates'] != start['completed_updates']+1:
        raise ValueError('qualification must resume exactly the next update')
    p = read_json(profile)
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    result = train_mixed(**{k: p[k] for k in ('target_path', 'target_sha256', 'collection_root', 'collection_manifest')},
        out=root/'continued', plan=p['training_plan'], resume=start_checkpoint,
        resume_manifest=start_manifest, stop_after_updates=reference['completed_updates'])
    recovered = root/'continued'/result['last_checkpoint']
    comparisons = {}
    for name in ('adapter', 'optimizer', 'rng'):
        filename = name+'.safetensors'
        left, right = load_file(str(Path(reference_checkpoint)/filename)), load_file(str(recovered/filename))
        exact = left.keys() == right.keys() and all(torch.equal(v, right[k]) for k, v in left.items())
        byte_exact = file_sha256(Path(reference_checkpoint)/filename) == file_sha256(recovered/filename)
        if not exact or not byte_exact:
            raise ValueError('native resume failed exact '+name+' replay')
        comparisons[name] = dict(tensors_bitwise_exact=exact, files_byte_exact=byte_exact,
                                 sha256=file_sha256(recovered/filename))
    if read_json(recovered/'receipt.json') != reference or verify(recovered) != reference_manifest:
        raise ValueError('native resumed history or checkpoint manifest differs')
    imports = {n: str(Path(m.__file__).resolve()) for n, m in tuple(sys.modules.items())
               if n.startswith('power_grid_env') and getattr(m, '__file__', None)}
    if any(not Path(path).is_relative_to(runtime) for path in imports.values()):
        raise ValueError('source package imported into installed continuation')
    receipt = dict(schema='power-grid-env.qwen35-mixed-resume-qualification/v1', passed=True,
        code_sha256=package_fingerprint(), profile_sha256=file_sha256(profile),
        start_manifest_sha256=start_manifest, reference_manifest_sha256=reference_manifest,
        resumed_manifest_sha256=verify(recovered), comparisons=comparisons,
        history_exact=True, imports=imports, reference_imports_blocked=True,
        same_host=True, split='train', test_used=False,
        scope='One native optimizer update continued in a fresh installed-wheel process; '
              'adapter/AdamW/RNG/history exact, not arbitrary-host bitwise determinism.')
    if verify(reference_checkpoint) != reference_manifest or verify(start_checkpoint) != start_manifest:
        raise ValueError('reference checkpoints changed')
    (root/'receipt.json').write_bytes(encoded(receipt))
    seal(root)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for key in ('profile', 'reference-checkpoint', 'reference-manifest', 'start-checkpoint',
                'start-manifest', 'out', 'runtime-dir'):
        parser.add_argument('--'+key, required=True)
    print(run(**vars(parser.parse_args())))
