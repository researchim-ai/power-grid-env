import copy
import pytest

from power_grid_env.qwen35_evaluation import GenerationPlan, summarize, heldout_exports
from tests.test_design_multimodal import corpus
from power_grid_env.design_multimodal import export
from power_grid_env.study_artifacts import encoded, read_json, seal, verify


def rows():
    return [dict(mode=m, task_id=str(i), reward=float(i == 0), valid=True,
                 token_budget_exhausted=False)
            for m in ('image', 'text', 'combined', 'schematic') for i in range(3)]


def test_evaluation_counts_invalid_and_exhausted_answers_in_denominator():
    data = rows()
    data[1].update(valid=False, token_budget_exhausted=True)
    summary = summarize(data, 3)
    assert summary['image'] == dict(examples=3, correct=1, valid_json_decisions=2,
                                    exact_match=1/3, token_budget_exhausted=1)


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'extra'])
def test_partial_or_duplicated_evaluation_cannot_publish_accuracy(fault):
    data = rows()
    if fault == 'missing':
        data.pop()
    elif fault == 'duplicate':
        data[1] = copy.deepcopy(data[0])
    else:
        data.append(dict(mode='unrecognized'))
    with pytest.raises(ValueError):
        summarize(data, 3)


@pytest.mark.parametrize('plan', [dict(maximum_new_tokens=0), dict(threads=100), dict(extra=True)])
def test_generation_budget_is_strict(plan):
    with pytest.raises(ValueError):
        GenerationPlan.model_validate(plan)


def test_train_split_is_not_a_heldout_evaluation():
    with pytest.raises(ValueError, match='held-out'):
        heldout_exports('missing', 'a'*64, 'missing', 'b'*64, 'train')


@pytest.fixture
def collection(corpus, tmp_path):
    private, private_sha = corpus
    root = tmp_path / 'collection'
    root.mkdir()
    entries = []
    for mode in ('image', 'text', 'combined', 'schematic'):
        path = 'dev-' + mode
        export(private, root/path, private_sha, split='dev', kind='prompts', mode=mode)
        entries.append(dict(path=path, split='dev', mode=mode,
                            manifest_sha256=verify(root/path)))
    (root/'receipt.json').write_bytes(encoded(dict(
        dataset_manifest_sha256=private_sha, exports=entries)))
    return root, seal(root), private, private_sha


def test_every_modality_contains_same_complete_heldout_set_without_labels(collection):
    datasets = heldout_exports(*collection, 'dev')
    assert len(datasets) == 4
    ids = [{d.record(i)['metadata']['task_id'] for i in range(len(d))} for d in datasets]
    assert all(s == ids[0] for s in ids)
    assert all(d.kind == 'prompts' and d.split == 'dev' for d in datasets)


@pytest.mark.parametrize('fault', ['missing_mode', 'duplicate_mode', 'private_path', 'wrong_corpus'])
def test_resealed_incomplete_or_mixed_collections_cannot_be_evaluated(collection, fault):
    root, _, private, private_sha = collection
    receipt = read_json(root/'receipt.json')
    if fault == 'missing_mode':
        receipt['exports'].pop()
    elif fault == 'duplicate_mode':
        receipt['exports'][1] = copy.deepcopy(receipt['exports'][0])
    elif fault == 'private_path':
        receipt['exports'][0]['path'] = '../private'
    else:
        receipt['dataset_manifest_sha256'] = 'f'*64
    (root/'receipt.json').write_bytes(encoded(receipt))
    (root/'manifest.json').unlink()
    new_sha = seal(root)
    with pytest.raises(ValueError):
        heldout_exports(root, new_sha, private, private_sha, 'dev')
