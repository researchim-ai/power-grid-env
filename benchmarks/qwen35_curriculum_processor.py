"""Pack every schema-curriculum train/dev example with the native processor."""
import argparse
import importlib.abc
from pathlib import Path
import sys


def run(curriculum, expected_manifest, target_path, target_sha256, out, runtime_dir=None,
        raw_collection=None, raw_manifest=None, corpus=None, corpus_manifest=None):
    runtime = Path(runtime_dir).resolve() if runtime_dir else Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(runtime))
    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split('.')[0] in {'tests', 'benchmarks', 'andes', 'motulator'}:
                raise ImportError('reference imports blocked')
    sys.meta_path.insert(0, Block())
    from power_grid_env.study_artifacts import read_json, encoded, seal, verify
    from power_grid_env.evidence import package_fingerprint
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import VLMExportDataset, local_qwen35_processor
    from power_grid_env.design_response_schema import PROTOCOL, build_collection
    import torch
    torch.set_num_threads(4)
    root = Path(curriculum)
    if verify(root) != expected_manifest:
        raise ValueError('owner schema collection differs')
    receipt = read_json(root/'receipt.json')
    if (receipt['response_protocol'] != PROTOCOL or receipt['test_exported'] is not False or
            len(receipt['exports']) != 8):
        raise ValueError('eight train/dev schema exports without test required')
    target = local_target(target_path, target_sha256)
    processor = local_qwen35_processor(target['local_checkpoint'], target['config_sha256'])
    rows = []
    for entry in receipt['exports']:
        data = VLMExportDataset(root/entry['path'], entry['manifest_sha256'])
        if data.split == 'test':
            raise ValueError('test entered schema curriculum')
        for index in range(len(data)):
            record = data.record(index)
            if record['metadata'].get('response_protocol') != PROTOCOL:
                raise ValueError('explicit response schema metadata missing')
            batch, meta = data.pack(processor, index)
            if data.kind == 'sft' and (meta['assistant_tokens'] <= 0 or meta['assistant_tokens'] > 96):
                raise ValueError('train target is empty or exceeds fixed output budget')
            if data.kind == 'prompts' and 'labels' in batch:
                raise ValueError('dev labels entered native batch')
            rows.append(meta)
            del batch
    if len(rows) != 192 or sum(r['kind'] == 'sft' for r in rows) != 144 or sum(r['split'] == 'dev' for r in rows) != 48:
        raise ValueError('complete 144 train and48 dev packs required')
    output = Path(out)
    output.mkdir(parents=True, exist_ok=False)
    replay = None
    if raw_collection:
        if not all((raw_manifest, corpus, corpus_manifest)):
            raise ValueError('raw/corpus owner anchors required for rebuild')
        build_collection(corpus, corpus_manifest, raw_collection, raw_manifest, output/'rebuilt-curriculum')
        replay = verify(output/'rebuilt-curriculum')
        if replay != expected_manifest:
            raise ValueError('source/installed schema curriculum bytes differ')
    if any(not Path(m.__file__).resolve().is_relative_to(runtime)
           for n, m in sys.modules.items() if n.startswith('power_grid_env') and getattr(m, '__file__', None)):
        raise ValueError('mixed PGE runtime imports')
    result = dict(passed=True, code_sha256=package_fingerprint(), curriculum_manifest_sha256=expected_manifest,
        replayed_curriculum_manifest_sha256=replay, target_metadata_sha256=target_sha256,
        packs=len(rows), train_loss_masks=144, dev_prompts=48, rows=rows,
        maximum_tokens=max(r['tokens'] for r in rows), native_processor=type(processor).__name__,
        weights_loaded=False, model_trained=False, reference_imports_blocked=True,
        runtime_imports_verified=True, test_exported=False)
    if verify(root) != expected_manifest or local_target(target_path, target_sha256) != target:
        raise ValueError('processor inputs changed')
    (output/'receipt.json').write_bytes(encoded(result))
    seal(output)
    return {k: v for k, v in result.items() if k != 'rows'}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for name in ('curriculum', 'expected-manifest', 'target-path', 'target-sha256', 'out'):
        p.add_argument('--'+name, required=True)
    for name in ('runtime-dir', 'raw-collection', 'raw-manifest', 'corpus', 'corpus-manifest'):
        p.add_argument('--'+name)
    print(run(**vars(p.parse_args())))
