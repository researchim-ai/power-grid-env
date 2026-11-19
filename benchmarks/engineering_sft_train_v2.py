"""Bounded research SFT consumer with exact same-phase resume.

Requires a passing all-target native admission. Never promotes learning quality
from loss/checkpoint completion. Partial adapters are not inference releases.
"""
import argparse
import hashlib
import json
from pathlib import Path

from benchmarks.engineering_factory import write_json
from benchmarks.engineering_sft_admission_v2 import AdmittedDataset
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.engineering_train import save_optimizer, restore_optimizer
from benchmarks.engineering_trainer_v2 import LoraSpec, attach_language_lora
from power_grid_env.study_artifacts import read_json, seal, verify


def train(target_path, target_sha, admission, anchor, out, max_new_steps, resume=None, resume_anchor=None):
    if type(max_new_steps) is not int or not 1 <= max_new_steps <= 10000:
        raise ValueError('explicit bounded chunk size required')
    if bool(resume) != bool(resume_anchor):
        raise ValueError('resume requires manifest')
    root = Path(out)
    if root.exists():
        raise ValueError('fresh training output required')
    data = AdmittedDataset(admission, anchor, target_sha)
    recipe = data.plan['recipe']
    from power_grid_env.evidence import package_fingerprint
    identity = dict(admission_manifest_sha256=anchor, target_sha256=target_sha,
                    core_sha256=package_fingerprint(), recipe_sha256=sha(recipe),
                    source_sha256={name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                   for name in ('engineering_sft_train_v2.py', 'engineering_sft_admission_v2.py',
                                                'engineering_sft_contract_v2.py', 'engineering_staged_data.py',
                                                'engineering_train.py', 'engineering_trainer_v2.py')})
    cursor, history, previous = 0, [], None
    if resume:
        if verify(resume) != resume_anchor:
            raise ValueError('resume manifest differs')
        previous = read_json(Path(resume) / 'summary.json')
        if previous.get('schema') != 'power-grid-env.exact-sft-training/v2' or previous.get('identity') != identity:
            raise ValueError('resume identity differs')
        cursor, history = previous['completed_steps'], previous['history']
        if type(cursor) is not int or not 0 < cursor < len(data.schedule) or len(history) != cursor:
            raise ValueError('invalid or completed resume cursor')
    import torch
    from safetensors.torch import load_file, save_file
    from power_grid_env.qwen35_training import (local_target, load_native, adapter_state,
        restore_adapters, frozen_fingerprint, supervised_forward)
    torch.manual_seed(recipe['seed'])
    processor, model = load_native(local_target(target_path, target_sha), 4)
    names = attach_language_lora(model, LoraSpec.model_validate(recipe['lora']))
    frozen = frozen_fingerprint(model)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=recipe['learning_rate'], weight_decay=recipe['weight_decay'])
    if previous:
        if previous['adapter_names'] != names:
            raise ValueError('resume adapter inventory differs')
        restore_adapters(model, load_file(str(Path(resume) / 'adapter.safetensors')))
        restore_optimizer(model, optimizer, Path(resume) / 'optimizer.safetensors', previous['optimizer_parameter_names'], cursor)
        torch.set_rng_state(load_file(str(Path(resume) / 'rng.safetensors'))['torch_cpu'])
    before = adapter_state(model)
    stop = min(len(data.schedule), cursor + max_new_steps)
    root.mkdir(parents=True)
    try:
        for step in range(cursor, stop):
            batch, meta = data.batch(processor, step)
            # Frozen backbone uses deterministic eval-mode kernels; LoRA gradients
            # are enabled by autograd independently of module.training.
            model.eval()
            optimizer.zero_grad(set_to_none=True)
            loss, logits = supervised_forward(model, batch)
            if not bool(torch.isfinite(loss)):
                raise ValueError('nonfinite loss')
            loss.backward()
            if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in params):
                raise ValueError('missing/nonfinite adapter gradients')
            grad = torch.nn.utils.clip_grad_norm_(params, recipe['gradient_clip'], error_if_nonfinite=True)
            if float(grad) <= 0:
                raise ValueError('zero gradient')
            optimizer.step()
            if any(not bool(torch.isfinite(p).all()) for p in params):
                raise ValueError('nonfinite adapter weights')
            history.append(dict(step=step, **meta, loss=float(loss.detach()), gradient_norm=float(grad)))
            print(json.dumps(dict(step=step+1, total=len(data.schedule), skill=meta['skill'], loss=history[-1]['loss'])), flush=True)
            del loss, logits
        state = adapter_state(model)
        if all(torch.equal(before[k], value) for k, value in state.items()):
            raise ValueError('no adapter update')
        if frozen_fingerprint(model) != frozen:
            raise ValueError('frozen base changed')
        with torch.inference_mode():
            expected_loss, expected = supervised_forward(model, batch)
        save_file(state, str(root / 'adapter.safetensors'))
        optimizer_names = save_optimizer(model, optimizer, root / 'optimizer.safetensors')
        save_file({'torch_cpu': torch.get_rng_state()}, str(root / 'rng.safetensors'))
        restore_adapters(model, {k: torch.zeros_like(v) for k, v in state.items()})
        restore_adapters(model, load_file(str(root / 'adapter.safetensors')))
        with torch.inference_mode():
            actual_loss, actual = supervised_forward(model, batch)
        if not torch.equal(expected, actual) or not torch.equal(expected_loss, actual_loss):
            raise ValueError('adapter roundtrip differs')
        receipt = dict(schema='power-grid-env.exact-sft-training/v2', identity=identity,
                       completed_steps=stop, schedule_complete=stop == len(data.schedule),
                       resumed_from_manifest=resume_anchor, history=history, adapter_names=names,
                       optimizer_parameter_names=optimizer_names, frozen_base_unchanged=True,
                       adapter_roundtrip_exact=True, inference_release=False,
                       closed_loop_quality_gate_passed=False, closed_loop_quality_gate_status='not_run',
                       scope='Research-only text SFT; schedule/loss do not qualify workflow success or vision.')
        write_json(root / 'summary.json', receipt)
        seal(root)
        return {k: v for k, v in receipt.items() if k not in ('history', 'adapter_names', 'optimizer_parameter_names')}
    except Exception as exc:
        write_json(root / 'failure.json', dict(identity=identity, complete=False, error=type(exc).__name__, detail=str(exc)[:1000]))
        seal(root)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for key in ('target', 'target-sha256', 'admission', 'manifest-sha256', 'out'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--max-new-steps', type=int, required=True)
    parser.add_argument('--resume')
    parser.add_argument('--resume-manifest-sha256')
    args = parser.parse_args()
    print(json.dumps(train(args.target, args.target_sha256, args.admission, args.manifest_sha256,
                           args.out, args.max_new_steps, args.resume, args.resume_manifest_sha256)))
