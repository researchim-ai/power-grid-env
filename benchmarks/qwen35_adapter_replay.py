"""Fresh-process CPU replay of a sealed train-only Qwen adapter checkpoint."""
import argparse
import importlib.abc
from pathlib import Path
import sys


def run(runtime_dir, training, training_manifest, target_path, target_sha256,
        export_root, export_manifest, out):
    runtime = Path(runtime_dir).resolve()
    sys.path.insert(0, str(runtime))
    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split('.')[0] in {'tests', 'benchmarks', 'andes', 'motulator'}:
                raise ImportError('reference imports blocked')
    sys.meta_path.insert(0, Block())
    from power_grid_env.study_artifacts import read_json, verify, encoded, seal, digest
    from power_grid_env.evidence import package_fingerprint
    from power_grid_env.qwen35_training import (
        local_target, load_native, attach_adapters, restore_adapters,
        frozen_fingerprint, supervised_forward, file_sha256)
    from power_grid_env.vlm_batches import VLMExportDataset
    from safetensors.torch import load_file, save_file
    import torch
    source = Path(training)
    if verify(source) != training_manifest:
        raise ValueError('owner training artifact differs')
    receipt = read_json(source / 'receipt.json')
    if (not receipt['passed'] or receipt['code_sha256'] != package_fingerprint() or
            receipt['target_metadata_sha256'] != target_sha256 or
            receipt['export_manifest_sha256'] != export_manifest or
            file_sha256(source / 'adapter.safetensors') != receipt['adapter_sha256']):
        raise ValueError('training runtime/base/data/checkpoint identity differs')
    target = local_target(target_path, target_sha256)
    data = VLMExportDataset(export_root, export_manifest)
    if data.split != 'train' or data.kind != 'sft':
        raise ValueError('expected original train SFT export')
    torch.manual_seed(receipt['plan']['seed'])
    processor, model = load_native(target, receipt['plan']['threads'])
    names = attach_adapters(model, receipt['plan'])
    if names != receipt['trainable_modules'] or frozen_fingerprint(model) != receipt['frozen_backbone_sha256']:
        raise ValueError('loaded base or adapter architecture differs')
    restore_adapters(model, load_file(str(source / 'adapter.safetensors')))
    batch, meta = data.pack(processor, receipt['plan']['indices'][-1])
    with torch.no_grad():
        loss, logits = supervised_forward(model, batch)
    if float(loss) != receipt['last_example_loss_after']:
        raise ValueError('fresh-process native loss differs')
    if any(not Path(m.__file__).resolve().is_relative_to(runtime)
           for n, m in sys.modules.items() if n.startswith('power_grid_env') and getattr(m, '__file__', None)):
        raise ValueError('mixed source/installed module paths')
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    save_file({'logits': logits.cpu().contiguous()}, str(root / 'selected-logits.safetensors'))
    result = dict(passed=True, code_sha256=package_fingerprint(),
        training_manifest_sha256=training_manifest, target_metadata_sha256=target_sha256,
        export_manifest_sha256=export_manifest, adapter_sha256=receipt['adapter_sha256'],
        loss=float(loss), logits_sha256=digest((root/'selected-logits.safetensors').read_bytes()),
        observation=meta, reference_imports_blocked=True, runtime_imports_verified=True,
        independent_host=False, trained_model_quality_claimed=False)
    if (verify(source) != training_manifest or verify(export_root) != export_manifest or
            local_target(target_path, target_sha256) != target):
        raise ValueError('replay inputs changed')
    (root/'receipt.json').write_bytes(encoded(result))
    seal(root)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for name in ('runtime-dir', 'training', 'training-manifest', 'target-path',
                 'target-sha256', 'export-root', 'export-manifest', 'out'):
        p.add_argument('--'+name, required=True)
    print(run(**vars(p.parse_args())))
