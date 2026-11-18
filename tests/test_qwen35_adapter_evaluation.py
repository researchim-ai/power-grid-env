import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from safetensors.torch import save_file

import power_grid_env.qwen35_adapter_evaluation as api
from power_grid_env.design_multimodal import export
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded, read_json, seal, verify
from power_grid_env.qwen35_training import attach_adapters, adapter_state, frozen_fingerprint
from tests.test_design_multimodal import corpus
from tests.test_qwen35_training import SmallDecoder


def reseal(root):
    (root/'manifest.json').unlink()
    return seal(root)


@pytest.fixture
def checkpoint(corpus, tmp_path):
    private, private_sha = corpus
    train = tmp_path/'train'
    export(private, train, private_sha)
    train_sha = verify(train)
    task_id = __import__('json').loads((train/'messages.jsonl').read_text())['metadata']['task_id']
    model = SmallDecoder()
    base = copy.deepcopy(model)
    names = attach_adapters(model, {})
    root = tmp_path/'checkpoint'
    root.mkdir()
    save_file(adapter_state(model), str(root/'adapter.safetensors'))
    target = dict(model='Qwen/Qwen3.5-0.8B', config_sha256='a'*64, weights=[])
    report = dict(schema='power-grid-env.qwen35-adapter-training/v1', passed=True,
        code_sha256='b'*64, model=target['model'], target_metadata_sha256='c'*64,
        config_sha256=target['config_sha256'], weights=[],
        export_manifest_sha256=train_sha, split='train', mode='image', device='cpu', dtype='float32',
        frozen_backbone_unchanged=True, checkpoint_logits_bitwise_exact=True, forward_pass=True,
        backward_pass=True, full_model_tuned=False,
        plan=dict(indices=[0]), optimizer_steps=1,
        rows=[dict(step=0, task_id=task_id, split='train', kind='sft', mode='image',
                   assistant_tokens=2, loss=1., gradient_norm=1.)],
        adapter_sha256=api.file_sha256(root/'adapter.safetensors'), trainable_modules=names,
        trainable_parameters=sum(p.numel() for p in model.parameters() if p.requires_grad),
        frozen_backbone_sha256=frozen_fingerprint(model))
    (root/'receipt.json').write_bytes(encoded(report))
    (root/'target.json').write_bytes(encoded(target))
    return dict(training=root, training_manifest=seal(root), target=target, target_sha256='c'*64,
        train_export=train, train_export_manifest=train_sha, corpus_root=private,
        corpus_manifest=private_sha, expected_producer='b'*64), base


def test_historical_training_requires_explicit_pin_and_native_weights_are_rechecked(checkpoint, monkeypatch):
    args, base = checkpoint
    report, plan = api.training_checkpoint(**args)
    without = dict(args, expected_producer=None)
    with pytest.raises(ValueError, match='revision'):
        api.training_checkpoint(**without)
    monkeypatch.setattr(api, 'load_native', lambda *a: (object(), copy.deepcopy(base)))
    _, model = api.load_adapter_model(args['target'], report, plan, args['training'], 4)
    assert frozen_fingerprint(model) == report['frozen_backbone_sha256']
    report['frozen_backbone_sha256'] = 'd'*64
    with pytest.raises(ValueError, match='backbone'):
        api.load_adapter_model(args['target'], report, plan, args['training'], 4)


@pytest.mark.parametrize('fault', ['producer', 'target', 'weights', 'config', 'split', 'steps',
                                  'foreign_task', 'nan_gradient', 'mode', 'backward', 'adapter_hash'])
def test_resealed_training_corruption_never_reaches_model_loading(checkpoint, fault):
    args, _ = checkpoint
    root = args['training']
    report = read_json(root/'receipt.json')
    if fault == 'producer': report['code_sha256'] = 'f'*64
    elif fault == 'target': report['target_metadata_sha256'] = 'f'*64
    elif fault == 'weights': report['weights'] = [dict(file='other')]
    elif fault == 'config': report['config_sha256'] = 'f'*64
    elif fault == 'split': report['split'] = 'dev'
    elif fault == 'steps': report['optimizer_steps'] = 2
    elif fault == 'foreign_task': report['rows'][0]['task_id'] = 'private-test'
    elif fault == 'nan_gradient': report['rows'][0]['gradient_norm'] = 0.
    elif fault == 'mode': report['rows'][0]['mode'] = 'text'
    elif fault == 'backward': report['backward_pass'] = False
    else: report['adapter_sha256'] = 'f'*64
    (root/'receipt.json').write_bytes(encoded(report))
    args['training_manifest'] = reseal(root)
    with pytest.raises(ValueError): api.training_checkpoint(**args)


@pytest.mark.parametrize('text', ['```json\n{}\n```', '{} extra', '{"motors":{},"motors":{},"insufficient_observation":true}',
    '{"motors":{},"insufficient_observation":1}', '{"motors":{"m":"connected"},"insufficient_observation":false}',
    'NaN', '[]', '\ud800', 'x'*32769])
def test_strict_prediction_parser_never_repairs_outputs(text):
    assert api.parse_decision(text) is None


class Processor:
    def __init__(self, reply):
        self.reply = reply
        self.tokenizer = SimpleNamespace(pad_token_id=0, eos_token_id=9,
            decode=lambda ids, skip_special_tokens: reply)


class Model:
    def __init__(self): self.calls = []
    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return torch.cat((kwargs['input_ids'], torch.tensor([[7, 9]])), dim=1)


class Dataset:
    kind = 'prompts'
    def __init__(self, mode): self.mode = mode
    def __len__(self): return 1
    def pack(self, processor, index):
        return dict(input_ids=torch.tensor([[1, 2, 3]]), attention_mask=torch.ones((1, 3), dtype=torch.long)), dict(
            task_id='id', tokens=3, prompt_tokens=3, images=0 if self.mode == 'text' else 2 if self.mode == 'schematic' else 3,
            assistant_tokens=0, kind='prompts', split='dev', mode=self.mode)


class Grader:
    def __init__(self, *a): pass
    def reward_batch(self, replies, ids, mode): return [1.]*len(replies)
    def score(self, task_id, answer, mode): return dict(valid=True)


def test_native_generation_kwargs_and_rows_match_unchanged_base_runner(tmp_path, monkeypatch):
    import power_grid_env.qwen35_evaluation as old
    reply = '{"motors":{},"insufficient_observation":true}'
    processor, model = Processor(reply), Model()
    datasets = [Dataset(m) for m in ('image', 'text', 'combined', 'schematic')]
    monkeypatch.setattr(old, 'local_target', lambda *a: dict(model='Qwen/Qwen3.5-0.8B'))
    monkeypatch.setattr(old, 'load_native', lambda *a: (processor, model))
    monkeypatch.setattr(old, 'heldout_exports', lambda *a: datasets)
    monkeypatch.setattr(old, 'IndicationGrader', Grader)
    monkeypatch.setattr(old, 'verify', lambda p: str(p))
    old.evaluate_base('target', 'sha', 'collection', 'collection', 'corpus', 'corpus', tmp_path/'old')
    rows = [api.read_json_line(x) for x in (tmp_path/'old/predictions.jsonl').read_text().splitlines()]
    calls = model.calls[:]
    model.calls.clear()
    for index, data in enumerate(datasets):
        row = api.generate_prediction(model, processor, data, 0, old.GenerationPlan(), Grader())
        assert {k: v for k, v in row.items() if k != 'elapsed_s'} == {
            k: v for k, v in rows[index].items() if k != 'elapsed_s'}
        assert calls[index].keys() == model.calls[-1].keys()
        for key in calls[index]:
            x, y = calls[index][key], model.calls[-1][key]
            assert torch.equal(x, y) if torch.is_tensor(x) else x == y


@pytest.fixture
def predictions(corpus, tmp_path):
    private, sha = corpus
    target = next(r for r in read_json(private/'corpus.json')['rows'] if r['split'] == 'dev')
    roots = []
    for adapted in (False, True):
        root = tmp_path/('adapter' if adapted else 'base')
        root.mkdir()
        rows = []
        for mode in ('image', 'text', 'combined', 'schematic'):
            expected = dict(motors={}, insufficient_observation=True) if mode == 'schematic' else target['expected']
            rows.append(dict(mode=mode, task_id=target['id'], completion=encoded(expected).decode() if adapted else 'invalid',
                valid=adapted, reward=float(adapted), generated_tokens=2, token_budget_exhausted=False,
                tokens=10, prompt_tokens=10, images=0 if mode == 'text' else 2 if mode == 'schematic' else 3,
                kind='prompts', split='dev', assistant_tokens=0))
        report = dict(schema='power-grid-env.qwen35-adapter-evaluation/v1' if adapted else 'power-grid-env.qwen35-base-evaluation/v1',
            passed=True, code_sha256=package_fingerprint(), corpus_manifest_sha256=sha,
            labels_passed_to_model=False, split='dev', plan=dict(maximum_new_tokens=96, threads=4),
            predictions=4, metrics=api.summarize(rows, 1), model='Qwen/Qwen3.5-0.8B',
            target_metadata_sha256='a'*64, collection_manifest_sha256='b'*64,
            device='cpu', dtype='float32', decoding='greedy', adapters_loaded=adapted)
        (root/'receipt.json').write_bytes(encoded(report))
        (root/'predictions.jsonl').write_bytes(b''.join(encoded(r)+b'\n' for r in rows))
        roots.append((root, seal(root)))
    return roots, private, sha


def test_complete_paired_comparison_includes_every_observation(predictions, tmp_path):
    roots, corpus, sha = predictions
    result = api.compare_evaluations(*roots[0], *roots[1], corpus, sha, tmp_path/'pair')
    assert result['observations'] == 4 and result['base_correct'] == 0 and result['adapter_correct'] == 4
    assert all(r['paired_wins'] == 1 and r['paired_regressions'] == 0 for r in result['metrics'].values())


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'foreign', 'reward', 'validity', 'budget', 'target',
                                  'prompt', 'images', 'labels', 'producer', 'role', 'metrics'])
def test_selective_or_incomparable_predictions_cannot_produce_a_pair(predictions, tmp_path, fault):
    roots, corpus, sha = predictions
    root, _ = roots[1]
    rows = [api.read_json_line(x) for x in (root/'predictions.jsonl').read_text().splitlines()]
    report = read_json(root/'receipt.json')
    if fault == 'missing': rows.pop()
    elif fault == 'duplicate': rows[1] = copy.deepcopy(rows[0])
    elif fault == 'foreign': rows[0]['task_id'] = 'foreign'
    elif fault == 'reward': rows[0]['reward'] = 0.
    elif fault == 'validity': rows[0]['valid'] = False
    elif fault == 'budget': report['plan']['maximum_new_tokens'] = 128
    elif fault == 'target': report['target_metadata_sha256'] = 'c'*64
    elif fault == 'prompt': rows[0]['prompt_tokens'] += 1
    elif fault == 'images': rows[0]['images'] = 0
    elif fault == 'labels': rows[0]['assistant_tokens'] = 1
    elif fault == 'producer': report['code_sha256'] = 'f'*64
    elif fault == 'role': report['adapters_loaded'] = False
    else: report['metrics']['image']['correct'] = 0
    (root/'receipt.json').write_bytes(encoded(report))
    (root/'predictions.jsonl').write_bytes(b''.join(encoded(r)+b'\n' for r in rows))
    roots[1] = root, reseal(root)
    with pytest.raises(ValueError):
        api.compare_evaluations(*roots[0], *roots[1], corpus, sha, tmp_path/'pair')
    assert not (tmp_path/'pair').exists()
