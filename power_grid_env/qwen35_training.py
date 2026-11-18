"""Bounded local Qwen3.5 adapter SFT; no remote code or full-model tuning.

The owner pins checkpoint metadata and an individual train export. Only the
last decoder block's seven linear projections receive trainable adapters.
The frozen vision encoder still processes all images through the native model.
"""
from pathlib import Path
import hashlib
import importlib.metadata
import math
import re
import time

from pydantic import BaseModel, ConfigDict, Field

from power_grid_env.study_artifacts import digest, encoded, read_json, seal, verify
from power_grid_env.evidence import package_fingerprint
from power_grid_env.vlm_batches import VLMExportDataset, local_qwen35_processor


class AdapterPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    seed: int = Field(default=42, ge=0, le=2**31-1)
    rank: int = Field(default=4, ge=1, le=32)
    alpha: float = Field(default=8., gt=0, le=128)
    learning_rate: float = Field(default=0.0002, gt=0, le=0.01)
    gradient_clip: float = Field(default=1., gt=0, le=100)
    indices: list[int] = Field(default=[0, 1], min_length=1, max_length=64)
    threads: int = Field(default=4, ge=1, le=16)


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def local_target(path, expected_manifest):
    """Verify every model shard and processor file before opening any weights."""
    if not re.fullmatch('[0-9a-f]{64}', expected_manifest) or file_sha256(path) != expected_manifest:
        raise ValueError('owner target metadata differs')
    target = read_json(path)
    if target.get('model') not in ('Qwen/Qwen3.5-0.8B', 'Qwen/Qwen3.5-4B'):
        raise ValueError('unsupported qualified model family')
    root = Path(target['local_checkpoint']).resolve()
    config = read_json(root / 'config.json')
    if (config.get('model_type') != 'qwen3_5' or
            config.get('architectures') != ['Qwen3_5ForConditionalGeneration'] or
            file_sha256(root / 'config.json') != target['config_sha256']):
        raise ValueError('native multimodal checkpoint config differs')
    expected_size = 1024 if target['model'].endswith('0.8B') else 2560
    if config['text_config']['hidden_size'] != expected_size:
        raise ValueError('model size and pinned config disagree')
    files = target.get('processor_files_sha256', {})
    if not {'config.json', 'tokenizer.json', 'tokenizer_config.json',
            'preprocessor_config.json', 'chat_template.jinja'} <= files.keys():
        raise ValueError('full processor identity must be owner-pinned')
    allowed = {'config.json', 'tokenizer.json', 'tokenizer_config.json',
               'preprocessor_config.json', 'video_preprocessor_config.json',
               'vocab.json', 'merges.txt', 'chat_template.jinja'}
    for name, sha in files.items():
        p = root / name
        if name not in allowed or p.is_symlink() or not re.fullmatch('[0-9a-f]{64}', sha) or file_sha256(p) != sha:
            raise ValueError('local processor file differs')
    weights = target.get('weights', [])
    if not 1 <= len(weights) <= 16:
        raise ValueError('bounded safetensors inventory required')
    names = set()
    for row in weights:
        name = row['file']
        if not re.fullmatch(r'model(?:-\d{5}-of-\d{5}|\.safetensors-\d{5}-of-\d{5})?\.safetensors', name):
            raise ValueError('safe native safetensors shard name required')
        p = root / name
        if (name in names or p.is_symlink() or not p.resolve().is_relative_to(root) or
                not 0 < row['bytes'] <= 16_000_000_000 or p.stat().st_size != row['bytes'] or
                not re.fullmatch('[0-9a-f]{64}', row['sha256']) or file_sha256(p) != row['sha256']):
            raise ValueError('local checkpoint shard differs')
        names.add(name)
    if names != {p.name for p in root.glob('*.safetensors')}:
        raise ValueError('unlisted checkpoint shard')
    index = root / 'model.safetensors.index.json'
    if index.exists():
        if (index.is_symlink() or file_sha256(index) != target.get('weight_index_sha256') or
                set(read_json(index)['weight_map'].values()) != names):
            raise ValueError('checkpoint shard index differs')
    elif names != {'model.safetensors'}:
        raise ValueError('native shard index missing')
    return target


def attach_adapters(model, plan):
    """Install zero-output LoRA on exactly the final decoder block projections."""
    import torch
    from torch import nn
    plan = AdapterPlan.model_validate(plan)
    last = model.config.text_config.num_hidden_layers - 1
    prefix = f'model.language_model.layers.{last}.'
    names = [prefix + suffix for suffix in (
        'self_attn.q_proj', 'self_attn.k_proj', 'self_attn.v_proj', 'self_attn.o_proj',
        'mlp.gate_proj', 'mlp.up_proj', 'mlp.down_proj')]
    modules = dict(model.named_modules())
    if any(not isinstance(modules.get(name), nn.Linear) for name in names):
        raise ValueError('expected final decoder projections are missing or already adapted')

    class LowRankLinear(nn.Module):
        def __init__(self, base):
            super().__init__()
            self.base = base
            self.a = nn.Parameter(torch.empty(plan.rank, base.in_features,
                                             device=base.weight.device, dtype=base.weight.dtype))
            self.b = nn.Parameter(torch.zeros(base.out_features, plan.rank,
                                             device=base.weight.device, dtype=base.weight.dtype))
            nn.init.kaiming_uniform_(self.a, a=math.sqrt(5))
            self.scale = plan.alpha / plan.rank

        def forward(self, x):
            return self.base(x) + torch.nn.functional.linear(
                torch.nn.functional.linear(x, self.a), self.b) * self.scale

    model.requires_grad_(False)
    for name in names:
        parent, attr = name.rsplit('.', 1)
        setattr(modules[parent], attr, LowRankLinear(modules[name]))
    return names


def adapter_state(model):
    return {name: p.detach().cpu().contiguous().clone()
            for name, p in model.named_parameters() if p.requires_grad}


def restore_adapters(model, state):
    import torch
    params = {name: p for name, p in model.named_parameters() if p.requires_grad}
    if not params or params.keys() != state.keys():
        raise ValueError('adapter tensor keys differ')
    for name, p in params.items():
        value = state[name]
        if value.shape != p.shape or value.dtype != p.dtype or not bool(torch.isfinite(value).all()):
            raise ValueError('adapter shape/dtype/finite check failed')
    with torch.no_grad():
        for name, p in params.items():
            p.copy_(state[name])


def frozen_fingerprint(model):
    """Hash frozen tensors in bounded CPU byte chunks, including tied parameters."""
    h = hashlib.sha256()
    for name, p in model.named_parameters():
        if p.requires_grad:
            continue
        h.update(encoded(dict(name=name, shape=list(p.shape), dtype=str(p.dtype))))
        # Qualification uses CPU float32, avoiding a full GPU-to-CPU tensor copy.
        raw = p.detach().contiguous().view(-1)
        for offset in range(0, raw.numel(), 1_000_000):
            h.update(raw[offset:offset+1_000_000].cpu().numpy().tobytes())
    return h.hexdigest()


def supervised_forward(model, batch):
    """Native forward, causal assistant targets only; no full-sequence vocab logits.

    labels[t] is predicted by hidden[t-1]. This explicit index selection has
    the same mean CE as the full shifted loss, including the assistant EOS.
    """
    import torch
    labels = batch.get('labels')
    ids = batch['input_ids']
    if (labels is None or labels.shape != ids.shape or labels.ndim != 2 or
            labels.shape[0] != 1 or bool((labels[:, 0] != -100).any())):
        raise ValueError('single-example causal SFT labels required')
    active = labels != -100
    if (not bool(active.any()) or not torch.equal(labels[active], ids[active]) or
            bool((active & (batch['attention_mask'] == 0)).any())):
        raise ValueError('assistant labels must match unpadded input tokens')
    positions = torch.nonzero(active[0, 1:], as_tuple=False).flatten()
    output = model(**{k: v for k, v in batch.items() if k != 'labels'},
                   use_cache=False, logits_to_keep=positions)
    logits = output.logits
    targets = labels[0, positions + 1]
    if logits.shape[:2] != (1, len(positions)) or not bool(torch.isfinite(logits).all()):
        raise ValueError('selected native logits are missing or nonfinite')
    loss = torch.nn.functional.cross_entropy(logits[0].float(), targets)
    if not bool(torch.isfinite(loss)):
        raise ValueError('nonfinite assistant loss')
    return loss, logits


def load_native(target, threads):
    import torch
    from transformers import Qwen3_5ForConditionalGeneration
    torch.set_num_threads(threads)
    processor = local_qwen35_processor(target['local_checkpoint'], target['config_sha256'])
    model = Qwen3_5ForConditionalGeneration.from_pretrained(
        target['local_checkpoint'], local_files_only=True, trust_remote_code=False,
        use_safetensors=True, dtype=torch.float32, attn_implementation='sdpa').eval()
    model.requires_grad_(False)
    return processor, model


def train_adapters(target_path, target_sha256, export_root, export_manifest,
                   out, plan=None):
    """Finite CPU adapter qualification; uses only the pinned train export.

    The receipt is a training-tract qualification, not a held-out quality score.
    An adapter safetensors checkpoint is portable with the pinned original model;
    optimizer resume is outside this profile.
    """
    import torch
    from safetensors.torch import save_file, load_file
    start = time.monotonic()
    plan = AdapterPlan.model_validate(plan or {})
    root = Path(out)
    if root.exists():
        raise ValueError('fresh training output required')
    producer = package_fingerprint()
    target = local_target(target_path, target_sha256)
    data = VLMExportDataset(export_root, export_manifest)
    if data.kind != 'sft' or data.split != 'train':
        raise ValueError('only an owner-pinned train SFT export may train adapters')
    if any(isinstance(i, bool) or i < 0 or i >= len(data) for i in plan.indices):
        raise ValueError('training index outside pinned train split')
    torch.manual_seed(plan.seed)
    processor, model = load_native(target, plan.threads)
    names = attach_adapters(model, plan)
    initial = adapter_state(model)
    frozen_before = frozen_fingerprint(model)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=plan.learning_rate, weight_decay=0.)
    rows = []
    for step, index in enumerate(plan.indices):
        batch, meta = data.pack(processor, index)
        optimizer.zero_grad(set_to_none=True)
        loss, logits = supervised_forward(model, batch)
        loss.backward()
        if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in params):
            raise ValueError('missing or nonfinite adapter gradients')
        grad = torch.nn.utils.clip_grad_norm_(params, plan.gradient_clip, error_if_nonfinite=True)
        if float(grad) <= 0:
            raise ValueError('zero adapter gradient')
        optimizer.step()
        if any(not bool(torch.isfinite(p).all()) for p in params):
            raise ValueError('nonfinite updated adapter')
        rows.append(dict(step=step, **meta, loss=float(loss.detach()), gradient_norm=float(grad)))
        del logits, loss
        print('adapter SFT step', step+1, '/', len(plan.indices), rows[-1]['loss'], flush=True)
    state = adapter_state(model)
    if all(torch.equal(initial[k], v) for k, v in state.items()):
        raise ValueError('optimizer made no adapter update')
    with torch.no_grad():
        after_loss, expected_logits = supervised_forward(model, batch)
    expected_logits = expected_logits.detach().clone()
    root.mkdir(parents=True, exist_ok=False)
    save_file(state, str(root / 'adapter.safetensors'))
    restore_adapters(model, {k: torch.zeros_like(v) for k, v in state.items()})
    restore_adapters(model, load_file(str(root / 'adapter.safetensors')))
    with torch.no_grad():
        restored_loss, restored_logits = supervised_forward(model, batch)
    if not torch.equal(expected_logits, restored_logits) or float(after_loss) != float(restored_loss):
        raise ValueError('adapter checkpoint did not replay exact native logits')
    if frozen_fingerprint(model) != frozen_before or any(
            p.grad is not None for p in model.parameters() if not p.requires_grad):
        raise ValueError('frozen backbone mutated or received gradients')
    if (verify(export_root) != export_manifest or package_fingerprint() != producer or
            local_target(target_path, target_sha256) != target):
        raise ValueError('training data, runtime or base checkpoint changed')
    result = dict(schema='power-grid-env.qwen35-adapter-training/v1', passed=True,
        code_sha256=producer, model=target['model'], target_metadata_sha256=target_sha256,
        config_sha256=target['config_sha256'], weights=target['weights'],
        export_manifest_sha256=export_manifest, split='train', mode=data.mode,
        device='cpu', dtype='float32', plan=plan.model_dump(), rows=rows,
        trainable_modules=names, trainable_parameters=sum(p.numel() for p in params),
        frozen_backbone_sha256=frozen_before, frozen_backbone_unchanged=True,
        adapter_sha256=file_sha256(root / 'adapter.safetensors'),
        checkpoint_logits_bitwise_exact=True, last_example_loss_after=float(after_loss),
        elapsed_s=time.monotonic()-start,
        versions={p: importlib.metadata.version(p) for p in ('transformers', 'torch', 'safetensors')},
        weights_loaded=True, forward_pass=True, backward_pass=True, optimizer_steps=len(rows),
        full_model_tuned=False, optimizer_resume_qualified=False,
        heldout_evaluated=False, model_improvement_claimed=False,
        scope='Native image/text forward and assistant-only SFT on final-decoder-block adapters; '
              'frozen vision/backbone, train-only inputs and exact adapter replay. No quality or GRPO claim.')
    (root / 'receipt.json').write_bytes(encoded(result))
    (root / 'target.json').write_bytes(encoded(target))
    seal(root)
    return {k: v for k, v in result.items() if k not in ('rows', 'weights')}


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    for name in ('target-path', 'target-sha256', 'export-root', 'export-manifest', 'out'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--plan')
    args = vars(p.parse_args())
    if args['plan']:
        args['plan'] = read_json(args['plan'])
    print(train_adapters(**args))
