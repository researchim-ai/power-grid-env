import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks import design_train_early_observations as early
from benchmarks.network_construction_cases import example, proposal
from power_grid_env.design_multimodal import verify_dataset
from power_grid_env.design_multimodal_render import passport_data
from power_grid_env.network_design import NetworkDesignTask, NetworkProposal
from power_grid_env.study_artifacts import encoded, digest, read_json, seal, verify
from tests.test_design_multimodal import corpus, case_trace


@pytest.fixture
def early_sources(corpus, tmp_path, monkeypatch):
    root, old = corpus
    task = NetworkDesignTask.model_validate(example())
    candidate = NetworkProposal.model_validate(proposal())
    case, trace = case_trace()
    trace['code_sha256'] = 'a'*64
    refined = case.model_copy(deep=True)
    refined.study.induction.step_s /= 4
    source = tmp_path/'source'
    accepted = tmp_path/'accepted'
    leaf = source/'train-design'/'studies'/case.id/'grid-2'
    leaf.mkdir(parents=True)
    (leaf/'study.json').write_bytes(encoded(refined.study.model_dump(mode='json')))
    (leaf/'result.json').write_bytes(encoded(trace))
    accepted.mkdir()
    (accepted/'receipt.json').write_text('{}')
    source_sha, accepted_sha = seal(source), seal(accepted)
    data = read_json(root/'corpus.json')
    data['source_manifests'] = {str(source.resolve()): source_sha, str(accepted.resolve()): accepted_sha}
    data['source_reaudit'] = dict(verified_grid_runs=3, auditor_code_sha256='a'*64)
    row = next(r for r in data['rows'] if r['split'] == 'train')
    frame = read_json(root/row['frame'])
    frame['passport'] = passport_data(task, candidate, case)
    frame['source'] = dict(physics_code_sha256='a'*64, design_manifest_sha256='b'*64,
        study_sha256=digest((leaf/'study.json').read_bytes()), trace_sha256=digest((leaf/'result.json').read_bytes()))
    (root/row['frame']).write_bytes(encoded(frame))
    row['frame_sha256'] = digest(encoded(frame))
    (root/'corpus.json').write_bytes(encoded(data))
    (root/'manifest.json').unlink()
    sha = seal(root)
    d = dict(root=source/'train-design', manifest='b'*64, family_id=row['family_id'],
             graph=frame['graph'], plan=SimpleNamespace(cases=[case]), task=task, proposal=candidate)
    calls = []
    def inventory(*args):
        calls.append(args)
        return 'a'*64, [d]
    monkeypatch.setattr(early, 'source_inventory', inventory)
    return dict(corpus=root, corpus_manifest=sha, source=source, source_manifest=source_sha,
                accepted=accepted, accepted_manifest=accepted_sha), calls


def test_early_views_come_from_prefixes_and_leave_every_heldout_byte_unchanged(early_sources, tmp_path):
    args, calls = early_sources
    original = verify_dataset(args['corpus'], args['corpus_manifest'])
    out = tmp_path/'augmented'
    result = early.augment(**args, out=out, cutoffs=[0., .4])
    data = verify_dataset(out, result['manifest_sha256'])
    assert len(calls) == 1 and result['added_train_observations'] == 2
    additions = [r for r in data['rows'] if r not in original['rows']]
    assert len(additions) == 2 and all(r['split'] == 'train' for r in additions)
    assert all(r['expected']['motors']['m2'] == 'disconnected' for r in additions)
    for row in original['rows']:
        assert row in data['rows']
        assert (out/row['frame']).read_bytes() == (args['corpus']/row['frame']).read_bytes()
        for name in row['images'].values():
            assert (out/name).read_bytes() == (args['corpus']/name).read_bytes()
    for row in additions:
        frame = read_json(out/row['frame'])
        assert max(frame['history']['time_s']) <= frame['cutoff_s']
        assert frame['source']['physics_code_sha256'] == 'a'*64
    assert verify(args['corpus']) == args['corpus_manifest']
    assert data['train_augmentation']['labels_edited'] is False
    assert data['train_augmentation']['new_solver_integrations'] == 0


@pytest.mark.parametrize('cutoffs', [[], [.4, 0.], [1.5], [-1.], [float('inf')],
    [float('nan')], [True], [.1, .1]])
def test_bad_times_rejected_before_source_reaudit(early_sources, tmp_path, cutoffs):
    args, calls = early_sources
    with pytest.raises(ValueError):
        early.augment(**args, out=tmp_path/'bad', cutoffs=cutoffs)
    assert not calls and not (tmp_path/'bad').exists()


def test_different_owner_source_cannot_generate_training_labels(early_sources, tmp_path):
    args, calls = early_sources
    args = dict(args, source_manifest='f'*64)
    with pytest.raises(ValueError, match='owner-pinned'):
        early.augment(**args, out=tmp_path/'bad', cutoffs=[0.])
    assert not calls


def test_inconsistent_trace_provenance_cannot_publish_dataset(early_sources, tmp_path, monkeypatch):
    args, calls = early_sources
    source_call = early.source_inventory
    def wrong(*a):
        physics, designs = source_call(*a)
        bad = copy.deepcopy(designs)
        bad[0]['manifest'] = 'c'*64
        return physics, bad
    monkeypatch.setattr(early, 'source_inventory', wrong)
    out = tmp_path/'bad'
    with pytest.raises(ValueError, match='absent'):
        early.augment(**args, out=out, cutoffs=[0.])
    assert not (out/'manifest.json').exists()
