"""Independent tensor comparison of continuous2 versus chunk1+resume1 SFT.

This proves a two-position native resume check, not learned-agent quality.
"""
import argparse
import json
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_sft_release_v2 import sha
from power_grid_env.study_artifacts import read_json, seal, verify


def audit(continuous, continuous_anchor, chunk, chunk_anchor, resumed, resumed_anchor, out):
    root = Path(out)
    if root.exists():
        raise ValueError('fresh resume proof required')
    summaries = []
    for path, anchor in ((continuous, continuous_anchor), (chunk, chunk_anchor), (resumed, resumed_anchor)):
        if verify(path) != anchor:
            raise ValueError('training manifest differs')
        value = read_json(Path(path) / 'summary.json')
        if (value.get('schema') != 'power-grid-env.exact-sft-training/v2' or
                value.get('frozen_base_unchanged') is not True or value.get('adapter_roundtrip_exact') is not True or
                value.get('inference_release') is not False or value.get('closed_loop_quality_gate_passed') is not False):
            raise ValueError('complete smoke evidence required without quality promotion')
        summaries.append(value)
    direct, first, continuation = summaries
    if (direct['completed_steps'], first['completed_steps'], continuation['completed_steps']) != (2, 1, 2):
        raise ValueError('requires continuous2 versus chunk1+resume1')
    if sha(direct['identity']) != sha(first['identity']) or sha(direct['identity']) != sha(continuation['identity']):
        raise ValueError('training identities differ')
    if continuation['resumed_from_manifest'] != chunk_anchor or direct['resumed_from_manifest'] is not None or first['resumed_from_manifest'] is not None:
        raise ValueError('resume ancestry differs')
    if sha(direct['history']) != sha(continuation['history']) or sha(direct['history'][:1]) != sha(first['history']):
        raise ValueError('loss/gradient/sample histories differ')
    from safetensors.torch import load_file
    import torch
    tensors = {}
    for name in ('adapter.safetensors', 'optimizer.safetensors', 'rng.safetensors'):
        left = load_file(str(Path(continuous) / name))
        right = load_file(str(Path(resumed) / name))
        if left.keys() != right.keys() or any(not torch.equal(left[k], right[k]) for k in left):
            raise ValueError('native resume tensors differ: ' + name)
        tensors[name] = len(left)
    summary = dict(schema='power-grid-env.exact-sft-native-resume-audit/v2', passed=True,
                   continuous_manifest_sha256=continuous_anchor, chunk_manifest_sha256=chunk_anchor,
                   resumed_manifest_sha256=resumed_anchor, identity=direct['identity'],
                   exact_histories=True, exact_tensors=tensors, unique_schedule_positions=2,
                   executed_optimizer_updates=4, schedule_complete=False, learned_quality_gate_passed=False,
                   scope='Real native CPU Qwen3.5-0.8B continuous2 versus chunk1+resume1; '
                         'adapter/AdamW/RNG and losses/gradients exact; not policy or VLM training qualification.')
    write_json(root / 'summary.json', summary)
    seal(root)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for key in ('continuous', 'continuous-manifest-sha256', 'chunk', 'chunk-manifest-sha256',
                'resumed', 'resumed-manifest-sha256', 'out'):
        parser.add_argument('--' + key, required=True)
    a = parser.parse_args()
    print(json.dumps(audit(a.continuous, a.continuous_manifest_sha256, a.chunk,
        a.chunk_manifest_sha256, a.resumed, a.resumed_manifest_sha256, a.out)))
