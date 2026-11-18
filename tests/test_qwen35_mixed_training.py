import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from power_grid_env import qwen35_mixed_training as mixed
from power_grid_env.study_artifacts import encoded, read_json, seal, verify
from power_grid_env.qwen35_training import adapter_state, supervised_forward


class TinyLM(nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding = nn.Embedding(12, 4)
        self.embedding.requires_grad_(False)
        self.head = nn.Linear(4, 12)

    def forward(self, input_ids, attention_mask, use_cache, logits_to_keep):
        assert not use_cache
        return SimpleNamespace(logits=self.head(self.embedding(input_ids))[:, logits_to_keep])


def example(index=0, mode='text'):
    ids = torch.tensor([[1, 2, 3, 4, 5, 6]])
    labels = ids.clone()
    labels[:, :2+index] = -100
    batch = dict(input_ids=ids, attention_mask=torch.ones_like(ids), labels=labels)
    meta = dict(index=index, task_id='t'+str(index), tokens=6, prompt_tokens=2+index,
                assistant_tokens=4-index, images=0 if mode == 'text' else 2,
                kind='sft', split='train', mode=mode)
    return batch, meta


def opt(model, lr=.001):
    return torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr,
                              weight_decay=0., foreach=False)


def test_accumulation_matches_global_token_mean_including_unequal_lengths():
    torch.manual_seed(9)
    model = TinyLM()
    reference = copy.deepcopy(model)
    optimizer, other = opt(model), opt(reference)
    batches = [example(0), example(2)]
    losses = [supervised_forward(reference, b)[0]*m['assistant_tokens'] for b, m in batches]
    expected = sum(losses)/6
    expected.backward()
    expected_grad = torch.nn.utils.clip_grad_norm_([p for p in reference.parameters() if p.requires_grad], .1)
    other.step()
    row = mixed.update(model, optimizer, iter(batches), .1)
    assert row['assistant_tokens'] == 6
    assert row['loss'] == pytest.approx(float(expected.detach()), abs=1e-6)
    assert row['gradient_norm'] == pytest.approx(float(expected_grad), abs=1e-6)
    for p, q in zip(model.parameters(), reference.parameters(), strict=True):
        torch.testing.assert_close(p, q, rtol=1e-6, atol=1e-8)
    assert all(p.grad is None for p in model.parameters())


def test_resume_recovers_adam_moments_rng_and_next_update_exactly(tmp_path):
    from safetensors.torch import load_file
    torch.manual_seed(99)
    model = TinyLM()
    optimizer = opt(model)
    row = mixed.update(model, optimizer, [example()], 1.)
    header = dict(schema=mixed.SCHEMA, observation_ids=['t0', 't1'])
    root = tmp_path/'checkpoint'
    anchor = mixed.checkpoint(root, model, optimizer, header, [dict(step=0, **row)], 1)
    expected_random = torch.rand(8)
    mixed.update(model, optimizer, [example(1)], 1.)
    expected = adapter_state(model)
    expected_opt = mixed.optimizer_state(model, optimizer)
    recovered = TinyLM()
    # In production the frozen backbone is independently verified before resume.
    recovered.embedding.load_state_dict(model.embedding.state_dict())
    from power_grid_env.qwen35_training import restore_adapters
    restore_adapters(recovered, load_file(str(root/'adapter.safetensors')))
    restored_opt = opt(recovered)
    mixed.restore_optimizer(recovered, restored_opt, load_file(str(root/'optimizer.safetensors')), 1)
    torch.set_rng_state(load_file(str(root/'rng.safetensors'))['cpu_rng'])
    assert torch.equal(torch.rand(8), expected_random)
    mixed.update(recovered, restored_opt, [example(1)], 1.)
    assert all(torch.equal(v, expected[k]) for k, v in adapter_state(recovered).items())
    assert all(torch.equal(v, expected_opt[k]) for k, v in mixed.optimizer_state(recovered, restored_opt).items())
    assert verify(root) == anchor


@pytest.mark.parametrize('fault', ['keys', 'shape', 'dtype', 'nan', 'negative', 'step'])
def test_optimizer_restore_rejects_invalid_state_without_mutation(fault):
    model = TinyLM()
    optimizer = opt(model)
    mixed.update(model, optimizer, [example()], 1.)
    before = mixed.optimizer_state(model, optimizer)
    bad = copy.deepcopy(before)
    key = next(k for k in bad if k.endswith('::exp_avg_sq'))
    if fault == 'keys': bad.pop(key)
    elif fault == 'shape': bad[key] = torch.zeros(1)
    elif fault == 'dtype': bad[key] = bad[key].double()
    elif fault == 'nan': bad[key].flatten()[0] = float('nan')
    elif fault == 'negative': bad[key].flatten()[0] = -1
    else: bad[next(k for k in bad if k.endswith('::step'))] += 1
    with pytest.raises(ValueError):
        mixed.restore_optimizer(model, optimizer, bad, 1)
    assert all(torch.equal(v, before[k]) for k, v in mixed.optimizer_state(model, optimizer).items())


@pytest.mark.parametrize('field,value', [('epochs', 0), ('epochs', True), ('accumulation', 0),
    ('accumulation', 33), ('learning_rate', float('nan')), ('seed', -1), ('extra', 1)])
def test_plan_rejects_invalid_recipe(field, value):
    with pytest.raises(ValueError):
        mixed.MixedPlan.model_validate({field: value})


def fake_data():
    data = object.__new__(mixed.MixedData)
    data.items = [(mode, i) for i in range(2) for mode in mixed.MODES]
    return data


def test_schedule_is_complete_reproducible_and_independent_of_global_rng():
    data = fake_data()
    plan = mixed.MixedPlan(epochs=2)
    a = data.schedule(plan)
    torch.manual_seed(300)
    torch.rand(91)
    assert a == data.schedule(plan)
    assert a != data.schedule(plan.model_copy(update={'seed': 43}))
    assert sorted(a[:8]) == sorted(data.items) == sorted(a[8:])
    assert len(a) == 16


def history_checkpoint(tmp_path):
    model = TinyLM()
    optimizer = opt(model)
    row = mixed.update(model, optimizer, [example(0)], 1.)
    header = dict(schema=mixed.SCHEMA, observation_ids=['t0', 't1'])
    root = tmp_path/'checkpoint'
    mixed.checkpoint(root, model, optimizer, header, [dict(step=0, **row)], 1)
    return root, header


@pytest.mark.parametrize('fault', ['cursor', 'step', 'task', 'mode', 'index', 'tokens', 'loss', 'grad', 'header', 'bytes'])
def test_resume_history_tampering_rejected_even_when_resealed(tmp_path, fault):
    root, header = history_checkpoint(tmp_path)
    report = read_json(root/'receipt.json')
    if fault == 'cursor': report['cursor'] = 2
    elif fault == 'step': report['rows'][0]['step'] = 1
    elif fault == 'task': report['rows'][0]['examples'][0]['task_id'] = 'dev-observation'
    elif fault == 'mode': report['rows'][0]['examples'][0]['mode'] = 'schematic'
    elif fault == 'index': report['rows'][0]['examples'][0]['index'] = 1
    elif fault == 'tokens': report['rows'][0]['assistant_tokens'] += 1
    elif fault == 'loss': report['rows'][0]['loss'] = 'bad'
    elif fault == 'grad': report['rows'][0]['gradient_norm'] = 0
    elif fault == 'header': report['schema'] = 'other'
    else: (root/'adapter.safetensors').write_bytes(b'wrong')
    (root/'receipt.json').write_bytes(encoded(report))
    (root/'manifest.json').unlink()
    anchor = seal(root)
    with pytest.raises((ValueError, TypeError)):
        mixed.read_checkpoint(root, anchor, header, [('text', 0), ('text', 1)], mixed.MixedPlan(accumulation=1))


def test_valid_resume_history_and_manifest_anchor(tmp_path):
    root, header = history_checkpoint(tmp_path)
    report = mixed.read_checkpoint(root, verify(root), header, [('text', 0), ('text', 1)],
                                   mixed.MixedPlan(accumulation=1))
    assert report['cursor'] == 1
    with pytest.raises(ValueError, match='owner'):
        mixed.read_checkpoint(root, '0'*64, header, [('text', 0)], mixed.MixedPlan(accumulation=1))


def test_empty_accumulation_is_rejected():
    model = TinyLM()
    with pytest.raises(ValueError, match='empty'):
        mixed.update(model, opt(model), [], 1.)


class ToyExport:
    def __init__(self, mode): self.mode = mode
    def pack(self, processor, index):
        batch, meta = example(index, self.mode)
        meta.pop('index')
        return batch, meta


@pytest.fixture
def toy_runtime(monkeypatch):
    class Data(mixed.MixedData):
        def __init__(self, *args):
            self.ids = ['t0', 't1']
            self.items = [(m, i) for i in range(2) for m in mixed.MODES]
            self.datasets = {m: ToyExport(m) for m in mixed.MODES}
            self.anchors = {m: m+'-anchor' for m in mixed.MODES}
            self.corpus_manifest = 'corpus-anchor'
        def check_unchanged(self): pass
    monkeypatch.setattr(mixed, 'MixedData', Data)
    monkeypatch.setattr(mixed, 'local_target', lambda *a: {'model': 'tiny-fixture'})
    monkeypatch.setattr(mixed, 'load_native', lambda *a: (None, TinyLM()))
    monkeypatch.setattr(mixed, 'attach_adapters', lambda *a: ['head'])
    return dict(target_path='owner', target_sha256='target', collection_root='data',
                collection_manifest='curriculum', plan={'accumulation': 3})


def test_complete_training_resume_and_short_final_group_bitwise_equal(toy_runtime, tmp_path):
    from safetensors.torch import load_file
    before = torch.are_deterministic_algorithms_enabled()
    try:
        direct = mixed.train_mixed(**toy_runtime, out=tmp_path/'direct')
        interrupted = mixed.train_mixed(**toy_runtime, out=tmp_path/'interrupted', stop_after_updates=2)
        ck = tmp_path/'interrupted'/interrupted['last_checkpoint']
        resumed = mixed.train_mixed(**toy_runtime, out=tmp_path/'resumed',
            resume=ck, resume_manifest=interrupted['last_checkpoint_manifest'])
        assert direct['complete'] and resumed['complete'] and not interrupted['complete']
        assert direct['completed_updates'] == 3 and direct['consumed_examples'] == 8
        a = tmp_path/'direct'/direct['last_checkpoint']
        b = tmp_path/'resumed'/resumed['last_checkpoint']
        assert read_json(a/'receipt.json') == read_json(b/'receipt.json')
        assert direct['last_checkpoint_manifest'] == resumed['last_checkpoint_manifest']
        for name in ('adapter', 'optimizer', 'rng'):
            left, right = load_file(str(a/(name+'.safetensors'))), load_file(str(b/(name+'.safetensors')))
            assert all(torch.equal(v, right[k]) for k, v in left.items())
        assert len(read_json(a/'receipt.json')['rows'][-1]['examples']) == 2
        assert read_json(a/'receipt.json')['rows'][-1]['assistant_tokens'] == 6
        assert verify(tmp_path/'direct') and verify(tmp_path/'resumed')
    finally:
        torch.use_deterministic_algorithms(before)


@pytest.mark.parametrize('args', [{'resume': 'x'}, {'resume_manifest': 'a'},
    {'stop_after_updates': True}, {'stop_after_updates': 0}, {'stop_after_updates': 4}])
def test_bad_training_controls_rejected_before_weights(toy_runtime, tmp_path, args, monkeypatch):
    def forbidden(*args): raise AssertionError('weights must not load')
    monkeypatch.setattr(mixed, 'load_native', forbidden)
    with pytest.raises(ValueError):
        mixed.train_mixed(**toy_runtime, out=tmp_path/'out', **args)


def test_fresh_output_and_resume_recipe_drift_rejected(toy_runtime, tmp_path):
    partial = mixed.train_mixed(**toy_runtime, out=tmp_path/'partial', stop_after_updates=1)
    with pytest.raises(ValueError, match='fresh'):
        mixed.train_mixed(**toy_runtime, out=tmp_path/'partial')
    ck = tmp_path/'partial'/partial['last_checkpoint']
    changed = dict(toy_runtime, plan={'accumulation': 3, 'learning_rate': .001})
    with pytest.raises(ValueError, match='recipe'):
        mixed.train_mixed(**changed, out=tmp_path/'changed', resume=ck,
                          resume_manifest=partial['last_checkpoint_manifest'])


def test_real_mixed_collection_requires_four_matching_train_exports(tmp_path, corpus):
    from power_grid_env.design_multimodal import export
    from power_grid_env.design_response_schema import enrich_export
    private, private_sha = corpus
    root = tmp_path/'curriculum'
    root.mkdir()
    exports = []
    for mode in mixed.MODES:
        source = tmp_path/('raw-'+mode)
        export(private, source, private_sha, split='train', kind='sft', mode=mode)
        dest = root/('train-'+mode)
        receipt = enrich_export(private, private_sha, source, verify(source), dest)
        exports.append(dict(path=dest.name, manifest_sha256=verify(dest), rows=receipt['rows'],
                            split='train', mode=mode))
    receipt = dict(schema='power-grid-env.response-schema-collection/v1', passed=True,
        response_protocol=mixed.PROTOCOL, test_exported=False, dataset_manifest_sha256=private_sha,
        exports=exports)
    (root/'receipt.json').write_bytes(encoded(receipt))
    anchor = seal(root)
    data = mixed.MixedData(root, anchor)
    assert len(data.items) == len(data.ids)*4
    assert set(data.datasets) == set(mixed.MODES)
    data.check_unchanged()
    receipt['exports'].append(receipt['exports'][0])
    (root/'receipt.json').write_bytes(encoded(receipt))
    with pytest.raises(ValueError, match='changed'):
        data.check_unchanged()
    (root/'manifest.json').unlink()
    bad = seal(root)
    with pytest.raises(ValueError, match='one train'):
        mixed.MixedData(root, bad)


from tests.test_design_multimodal import corpus


@pytest.mark.parametrize('fault', ['incomplete', 'producer', 'target', 'corpus', 'collection', 'frozen'])
def test_mixed_evaluation_rejects_wrong_training_before_loading_weights(toy_runtime, tmp_path, monkeypatch, fault):
    trained = mixed.train_mixed(**toy_runtime, out=tmp_path/'trained')
    path = tmp_path/'trained'
    report = read_json(path/'receipt.json')
    field, value = {'incomplete': ('complete', False), 'producer': ('code_sha256', '0'*64),
        'target': ('target_metadata_sha256', 'wrong'), 'corpus': ('corpus_manifest_sha256', 'wrong'),
        'collection': ('collection_manifest_sha256', 'wrong'), 'frozen': ('frozen_backbone_unchanged', False)}[fault]
    report[field] = value
    (path/'receipt.json').write_bytes(encoded(report))
    (path/'manifest.json').unlink()
    anchor = seal(path)
    def forbidden(*args): raise AssertionError('weights must not load')
    monkeypatch.setattr(mixed, 'load_native', forbidden)
    with pytest.raises(ValueError, match='completed'):
        mixed.evaluate_mixed(target_path='owner', target_sha256='target', collection_root='data',
            collection_manifest='curriculum', corpus_root='private', corpus_manifest='corpus-anchor',
            training=path, training_manifest=anchor, out=tmp_path/'eval')


def test_no_pickle_loading_or_saving_in_mixed_runtime():
    import ast
    tree = ast.parse(Path(mixed.__file__).read_text())
    calls = [n.func for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert not any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and
                   n.value.id in ('torch', 'pickle') and n.attr in ('load', 'save', 'loads', 'dumps') for n in calls)


def test_complete_mixed_evaluator_retains_all_dev_modes_without_labels(toy_runtime, tmp_path, monkeypatch):
    from power_grid_env import qwen35_evaluation as evaluation
    from power_grid_env import qwen35_adapter_evaluation as adapter_evaluation
    from power_grid_env import design_multimodal
    private = tmp_path/'private'
    private.mkdir()
    (private/'owner.json').write_text('{}')
    corpus_sha = seal(private)
    original = mixed.MixedData
    class Data(original):
        def __init__(self, *args):
            super().__init__(*args)
            self.corpus_manifest = corpus_sha
    monkeypatch.setattr(mixed, 'MixedData', Data)
    trained = mixed.train_mixed(**toy_runtime, out=tmp_path/'trained')
    class Dev:
        kind = 'prompts'
        split = 'dev'
        def __init__(self, mode): self.mode = mode
        def __len__(self): return 1
    monkeypatch.setattr(evaluation, 'heldout_exports', lambda *args: [Dev(m) for m in mixed.MODES])
    monkeypatch.setattr(design_multimodal, 'IndicationGrader', lambda *args: object())
    calls = []
    def generate(model, processor, dataset, index, plan, grader):
        assert dataset.kind == 'prompts' and dataset.split == 'dev' and index == 0
        calls.append(dataset.mode)
        return dict(task_id='dev0', mode=dataset.mode, kind='prompts', split='dev',
                    tokens=10, prompt_tokens=10, images=0, assistant_tokens=0,
                    completion='invalid', generated_tokens=1, token_budget_exhausted=False,
                    valid=False, reward=0., elapsed_s=0.)
    monkeypatch.setattr(adapter_evaluation, 'generate_prediction', generate)
    result = mixed.evaluate_mixed(target_path='owner', target_sha256='target', collection_root='data',
        collection_manifest='curriculum', corpus_root=private, corpus_manifest=corpus_sha,
        training=tmp_path/'trained', training_manifest=verify(tmp_path/'trained'), out=tmp_path/'evaluated')
    assert calls == list(mixed.MODES)
    assert result['predictions'] == 4 and result['split'] == 'dev'
    assert result['labels_passed_to_model'] is False and result['model_parameters_unchanged']
    assert result['training_kind'] == 'mixed-view-token-normalized-sft'
    assert sum(m['correct'] for m in result['metrics'].values()) == 0
    assert len((tmp_path/'evaluated'/'predictions.jsonl').read_text().splitlines()) == 4
    assert verify(tmp_path/'evaluated')
