import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from power_grid_env.qwen35_training import (
    AdapterPlan, adapter_state, attach_adapters, file_sha256, frozen_fingerprint,
    local_target, restore_adapters, supervised_forward, train_adapters,
)
from power_grid_env.study_artifacts import encoded


class SmallDecoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.config = SimpleNamespace(text_config=SimpleNamespace(num_hidden_layers=1))
        self.model = nn.Module()
        self.model.language_model = nn.Module()
        layer = nn.Module()
        layer.self_attn = nn.ModuleDict({n: nn.Linear(4, 4) for n in (
            'q_proj', 'k_proj', 'v_proj', 'o_proj')})
        layer.mlp = nn.ModuleDict({n: nn.Linear(4, 4) for n in (
            'gate_proj', 'up_proj', 'down_proj')})
        self.model.language_model.layers = nn.ModuleList([layer])

    def forward(self, x):
        layer = self.model.language_model.layers[0]
        for projection in list(layer.self_attn.values()) + list(layer.mlp.values()):
            x = projection(x)
        return x


def test_adapters_start_identical_and_only_adapters_receive_gradients():
    torch.manual_seed(42)
    model = SmallDecoder()
    x = torch.randn(2, 4)
    expected = model(x).detach()
    names = attach_adapters(model, {})
    assert len(names) == 7 and torch.equal(model(x), expected)
    before = frozen_fingerprint(model)
    initial = adapter_state(model)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=.001)
    model(x).square().sum().backward()
    assert all(p.grad is None for p in model.parameters() if not p.requires_grad)
    assert all(p.grad is not None and torch.isfinite(p.grad).all()
               for p in model.parameters() if p.requires_grad)
    opt.step()
    assert frozen_fingerprint(model) == before
    state = adapter_state(model)
    assert any(not torch.equal(state[k], v) for k, v in initial.items())
    y = model(x).detach()
    restore_adapters(model, initial)
    restore_adapters(model, state)
    assert torch.equal(model(x), y)


@pytest.mark.parametrize('fault', ['missing', 'already_adapted'])
def test_adapter_attachment_requires_exact_supported_architecture(fault):
    model = SmallDecoder()
    if fault == 'missing':
        del model.model.language_model.layers[0].self_attn['q_proj']
    else:
        attach_adapters(model, {})
    with pytest.raises(ValueError, match='projections'):
        attach_adapters(model, {})


@pytest.mark.parametrize('fault', ['keys', 'shape', 'dtype', 'nan'])
def test_checkpoint_restore_validates_every_tensor_before_mutating(fault):
    model = SmallDecoder()
    attach_adapters(model, {})
    before = adapter_state(model)
    state = copy.deepcopy(before)
    first = next(iter(state))
    if fault == 'keys':
        state.pop(first)
    elif fault == 'shape':
        state[first] = state[first][:1]
    elif fault == 'dtype':
        state[first] = state[first].double()
    else:
        state[first].flatten()[0] = float('nan')
    with pytest.raises(ValueError):
        restore_adapters(model, state)
    assert all(torch.equal(before[k], v) for k, v in adapter_state(model).items())


class TinyLM(nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding = nn.Embedding(10, 4)
        self.head = nn.Linear(4, 10)

    def forward(self, input_ids, attention_mask, use_cache, logits_to_keep):
        assert use_cache is False
        return SimpleNamespace(logits=self.head(self.embedding(input_ids))[:, logits_to_keep])


def tiny_batch():
    return dict(input_ids=torch.tensor([[1, 2, 3, 4, 5]]),
                attention_mask=torch.ones((1, 5), dtype=torch.long),
                labels=torch.tensor([[-100, -100, -100, 4, 5]]))


def test_selected_assistant_loss_and_gradients_equal_full_shifted_cross_entropy():
    model = TinyLM()
    other = copy.deepcopy(model)
    batch = tiny_batch()
    loss, logits = supervised_forward(model, batch)
    full = other.head(other.embedding(batch['input_ids']))
    reference = torch.nn.functional.cross_entropy(full[0, :-1], batch['labels'][0, 1:])
    torch.testing.assert_close(loss, reference)
    loss.backward()
    reference.backward()
    for p, q in zip(model.parameters(), other.parameters(), strict=True):
        torch.testing.assert_close(p.grad, q.grad)
    assert logits.shape == (1, 2, 10)


@pytest.mark.parametrize('fault', ['missing', 'first_token', 'wrong_token', 'padding', 'empty'])
def test_invalid_causal_supervision_is_rejected_before_model_call(fault):
    batch = tiny_batch()
    if fault == 'missing':
        batch.pop('labels')
    elif fault == 'first_token':
        batch['labels'][0, 0] = 1
    elif fault == 'wrong_token':
        batch['labels'][0, 3] = 9
    elif fault == 'padding':
        batch['attention_mask'][0, 3] = 0
    else:
        batch['labels'][:] = -100
    with pytest.raises(ValueError):
        supervised_forward(TinyLM(), batch)


@pytest.fixture
def checkpoint(tmp_path):
    root = tmp_path / 'model'
    root.mkdir()
    (root / 'config.json').write_bytes(encoded(dict(
        model_type='qwen3_5', architectures=['Qwen3_5ForConditionalGeneration'],
        text_config=dict(hidden_size=1024))))
    for name in ('tokenizer.json', 'tokenizer_config.json', 'preprocessor_config.json', 'chat_template.jinja'):
        (root / name).write_text('{}')
    weight = root / 'model.safetensors'
    weight.write_bytes(b'not loaded in metadata validation')
    target = dict(model='Qwen/Qwen3.5-0.8B', local_checkpoint=str(root),
        config_sha256=file_sha256(root / 'config.json'),
        processor_files_sha256={p.name: file_sha256(p) for p in root.iterdir() if p != weight},
        weights=[dict(file=weight.name, bytes=weight.stat().st_size, sha256=file_sha256(weight))])
    path = tmp_path / 'target.json'
    path.write_bytes(encoded(target))
    return path, file_sha256(path), root


def test_owner_pinned_local_checkpoint_inventory(checkpoint):
    path, sha, _ = checkpoint
    assert local_target(path, sha)['model'] == 'Qwen/Qwen3.5-0.8B'


@pytest.mark.parametrize('fault', ['target', 'weights', 'processor', 'unlisted_shard', 'index', 'symlink'])
def test_local_checkpoint_tampering_is_rejected_before_weight_load(checkpoint, fault):
    path, sha, root = checkpoint
    if fault == 'target':
        path.write_text('{}')
    elif fault == 'weights':
        (root / 'model.safetensors').write_bytes(b'replaced')
    elif fault == 'processor':
        (root / 'chat_template.jinja').write_text('changed')
    elif fault == 'unlisted_shard':
        (root / 'model-00001-of-00001.safetensors').write_bytes(b'extra')
    elif fault == 'index':
        (root / 'model.safetensors.index.json').write_text('{"weight_map":{"x":"../other"}}')
    else:
        p = root / 'tokenizer.json'
        data = p.read_bytes()
        p.unlink()
        other = root.parent / 'other'
        other.write_bytes(data)
        p.symlink_to(other)
    with pytest.raises(ValueError):
        local_target(path, sha)


@pytest.mark.parametrize('plan', [dict(rank=0), dict(indices=[]), dict(indices=[True]),
                                  dict(learning_rate=float('nan')), dict(unknown=True)])
def test_training_plan_is_strict_and_bounded(plan):
    with pytest.raises(ValueError):
        AdapterPlan.model_validate(plan)


def test_heldout_prompts_cannot_enter_training(checkpoint, tmp_path):
    from tests.test_vlm_batches import make_export
    path, sha, _ = checkpoint
    root, manifest = make_export(tmp_path, kind='prompts', split='dev')
    with pytest.raises(ValueError, match='only an owner-pinned train'):
        train_adapters(path, sha, root, manifest, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()
