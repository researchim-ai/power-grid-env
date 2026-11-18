"""Model-free capacity-arm contracts. No native forwards or optimizer steps."""
import copy
from types import SimpleNamespace
import weakref

import pytest
import torch

from benchmarks import engineering_capacity_campaign as capacity
from benchmarks.engineering_grounding import digest


def name(layer):
    return f'model.language_model.layers.{layer}.mlp.up_proj'


@pytest.fixture
def reference():
    indices = list(range(13)); transfer = [13, 14]
    return dict(
        schema='power-grid-env.physics-curriculum-plan/v1', target_sha256=capacity.physics.TARGET,
        steps=78, seed=423, indices=indices, transfer_indices=transfer,
        schedule=capacity.physics.order(indices), row_ids={str(i): digest(i) for i in range(15)},
        lora=dict(rank=16, alpha=32., last_blocks=2), learning_rate=.0001,
        gradient_clip=1., value_weight=4., context_tokens=8192, completion_tokens=128,
        maximum_tool_calls_per_episode=24, thinking=False, optimizer_run=False,
        optimizer_initialization='fresh_AdamW_for_new_phase_preserve_adapter40_weights',
        gate='all15_generated_answers_and_all3_full_v2_workflows',
        initial=dict(checkpoint='/immutable/parent40', manifest_sha256='a' * 64,
                     plan='/immutable/parent-plan', plan_manifest_sha256='b' * 64),
        sources={k: dict(path='/immutable/' + k, manifest_sha256=digest(k))
                 for k in ('staged', 'paired', 'visual', 'audit', 'catalog')},
        case_ids=['a', 'b'], full_episode_ids=['a', 'b', 'healthy-a'],
        full_episode_base_networks=1, selected_skills={'operation': 4, 'perception': 4,
                                                      'measurement': 3, 'conditional_arguments': 2},
        native_admission=dict(passed=True, checked_targets=15,
                              rows=[dict(index=i, row_id=digest(i)) for i in range(15)]),
        source_sha256={'old.py': 'c' * 64}, scope='original scope')


@pytest.fixture
def anchors():
    return {k: dict(path='/immutable/reference-' + k, manifest_sha256=digest(k))
            for k in ('plan', 'result', 'audit')}


@pytest.fixture
def proof(reference):
    rows = [dict(index=i, row_id=reference['row_ids'][str(i)],
                 input_sha256=digest(('input', i)), logits_sha256=digest(('logits', i)),
                 loss_sha256=digest(('loss', i)), weighted_loss_sha256=digest(('weighted', i)))
            for i in range(15)]
    return dict(schema='power-grid-env.capacity-initialization-proof/v1',
                checked_inputs=15, exact_equal=True, parent40=rows, expanded=copy.deepcopy(rows),
                parent_adapter_names=[name(22), name(23)],
                expanded_adapter_names=[name(i) for i in range(16, 24)],
                parent_adapter_state_sha256='d' * 64, expanded_adapter_state_sha256='e' * 64,
                extra_b_zero=True, parent_tensors_retained=True, scope='fixture')


def states():
    old_names = [name(22), name(23)]; new_names = [name(i) for i in range(16, 24)]
    initialized = {n + suffix: (torch.ones((2, 3)) if suffix == '.a' else torch.zeros((4, 2)))
                   for n in new_names for suffix in ('.a', '.b')}
    parent = {k: torch.full_like(v, float(i + 1))
              for i, (k, v) in enumerate(initialized.items()) if k.rsplit('.', 1)[0] in old_names}
    return initialized, parent, old_names, new_names


def test_partial_merge_preserves_all_old_tensors_and_fresh_extra_a_without_aliases():
    initialized, parent, old_names, new_names = states()
    before = {k: v.clone() for k, v in initialized.items()}
    old = {k: v.clone() for k, v in parent.items()}
    result = capacity.merge_parent(initialized, parent, old_names, new_names)
    assert set(result) == set(initialized)
    for k in result:
        assert torch.equal(result[k], old[k] if k in old else before[k])
        assert result[k].data_ptr() != initialized[k].data_ptr()
        assert torch.equal(initialized[k], before[k])
        if k in parent:
            assert result[k].data_ptr() != parent[k].data_ptr()
            assert torch.equal(parent[k], old[k])
        elif k.endswith('.b'):
            assert torch.count_nonzero(result[k]) == 0
    result[old_names[0] + '.a'].zero_()
    assert torch.equal(parent[old_names[0] + '.a'], old[old_names[0] + '.a'])


@pytest.mark.parametrize('mutation', [
    'missing_old_a', 'missing_old_b', 'unknown_old', 'missing_new', 'unexpected_new',
    'shape', 'dtype', 'nonfinite_old', 'nonfinite_new', 'extra_b_nonzero',
    'duplicate_old_name', 'duplicate_new_name', 'not_expanded', 'unpaired_rank', 'nested_name',
])
def test_partial_merge_fails_closed_before_mutation(mutation):
    initialized, parent, old_names, new_names = states()
    key = old_names[0] + '.a'
    if mutation == 'missing_old_a': parent.pop(key)
    if mutation == 'missing_old_b': parent.pop(old_names[0] + '.b')
    if mutation == 'unknown_old': parent[name(0) + '.a'] = torch.ones(2, 3)
    if mutation == 'missing_new': initialized.pop(new_names[0] + '.b')
    if mutation == 'unexpected_new': initialized['unrelated'] = torch.ones(1)
    if mutation == 'shape': parent[key] = torch.ones(2, 4)
    if mutation == 'dtype': parent[key] = parent[key].double()
    if mutation == 'nonfinite_old': parent[key][0, 0] = float('nan')
    if mutation == 'nonfinite_new': initialized[new_names[0] + '.a'][0, 0] = float('inf')
    if mutation == 'extra_b_nonzero': initialized[new_names[0] + '.b'][0, 0] = 1.
    if mutation == 'duplicate_old_name': old_names.append(old_names[0])
    if mutation == 'duplicate_new_name': new_names.append(new_names[0])
    if mutation == 'not_expanded': old_names = new_names.copy(); parent = copy.deepcopy(initialized)
    if mutation == 'unpaired_rank': initialized[new_names[0] + '.b'] = torch.zeros(4, 3)
    if mutation == 'nested_name': new_names[0] = new_names[0] + '.base.proj'
    before = capacity.state_sha256(initialized)
    with pytest.raises(ValueError):
        capacity.merge_parent(initialized, parent, old_names, new_names)
    assert capacity.state_sha256(initialized) == before


def test_capacity_transform_changes_only_depth_and_never_aliases_reference(reference):
    original = copy.deepcopy(reference)
    a = capacity.capacity_recipe(reference); b = capacity.capacity_recipe(reference)
    assert a == b and reference == original
    assert a['lora'] == dict(rank=16, alpha=32., last_blocks=8)
    a['lora']['last_blocks'] = 2
    assert a == original
    b['schedule'][0] = 999
    b['sources']['staged']['path'] = '/different'
    assert reference == original


@pytest.mark.parametrize('field,value', [
    ('steps', 79), ('seed', 424), ('learning_rate', .0002), ('value_weight', 3.),
    ('gradient_clip', 2.), ('context_tokens', 16384), ('completion_tokens', 256),
    ('maximum_tool_calls_per_episode', 25), ('thinking', True), ('thinking', 0),
    ('optimizer_run', True), ('variant', capacity.VARIANT),
    ('target_sha256', 'f' * 64), ('lora', dict(rank=16, alpha=32., last_blocks=8)),
    ('lora', dict(rank=8, alpha=32., last_blocks=2)),
    ('optimizer_initialization', 'resume'), ('gate', 'skills_only'),
])
def test_transform_refuses_other_arm_or_changed_hyperparameters(reference, field, value):
    reference[field] = value
    with pytest.raises(ValueError): capacity.capacity_recipe(reference)


@pytest.mark.parametrize('mutation', ['schedule', 'duplicate', 'transfer', 'row_ids', 'admission', 'count'])
def test_transform_refuses_changed_schedule_or_denominators(reference, mutation):
    if mutation == 'schedule': reference['schedule'][0] = 999
    if mutation == 'duplicate': reference['indices'][0] = reference['indices'][1]
    if mutation == 'transfer': reference['transfer_indices'].pop()
    if mutation == 'row_ids': reference['row_ids'].pop('0')
    if mutation == 'admission': reference['native_admission']['passed'] = False
    if mutation == 'count': reference['native_admission']['rows'].pop()
    with pytest.raises(ValueError): capacity.capacity_recipe(reference)


def test_attachment_refuses_wrapped_or_unfrozen_model(monkeypatch):
    def forbidden(*args): raise AssertionError('nested attachment attempted')
    monkeypatch.setattr(capacity, 'attach_language_lora', forbidden)
    wrapped = SimpleNamespace(parameters=lambda: [], modules=lambda: [SimpleNamespace(a=1, b=2, base=3)])
    with pytest.raises(ValueError, match='unwrapped'): capacity.attach_fresh(wrapped, None)
    trainable = SimpleNamespace(parameters=lambda: [SimpleNamespace(requires_grad=True)], modules=lambda: [])
    with pytest.raises(ValueError, match='unwrapped'): capacity.attach_fresh(trainable, None)
    base = SimpleNamespace(parameters=lambda: [SimpleNamespace(requires_grad=False)], modules=lambda: [])
    monkeypatch.setattr(capacity, 'attach_language_lora', lambda model, spec: ['fresh'])
    assert capacity.attach_fresh(base, None) == ['fresh']


def test_initialization_proof_loads_one_model_at_a_time(monkeypatch, reference, proof):
    alive = []; order = []
    class Actor: pass
    def parent(*args):
        assert not any(r() is not None for r in alive)
        actor = Actor(); alive.append(weakref.ref(actor)); order.append('parent40')
        return actor
    def expanded(target, p):
        assert p['lora']['last_blocks'] == 8
        assert not any(r() is not None for r in alive)
        actor = Actor(); alive.append(weakref.ref(actor)); order.append('expanded')
        return actor, {k: proof[k] for k in ('parent_adapter_names', 'expanded_adapter_names',
                       'parent_adapter_state_sha256', 'expanded_adapter_state_sha256',
                       'extra_b_zero', 'parent_tensors_retained')}
    monkeypatch.setattr(capacity, 'load', parent)
    monkeypatch.setattr(capacity, '_expanded', expanded)
    monkeypatch.setattr(capacity, 'signatures', lambda *args: copy.deepcopy(proof['parent40']))
    result = capacity.prove_initialization('unused-local-target', None, reference)
    assert order == ['parent40', 'expanded'] and not any(r() is not None for r in alive)
    capacity.validate_proof(result, capacity.capacity_recipe(reference))


def test_initialization_requires_exact_logits_and_loss15(monkeypatch, reference, proof):
    monkeypatch.setattr(capacity, '_parent_signatures', lambda *args: proof['parent40'])
    rows = copy.deepcopy(proof['expanded']); rows[-1]['loss_sha256'] = 'f' * 64
    monkeypatch.setattr(capacity, '_expanded_signatures', lambda *args: (rows, {}))
    with pytest.raises(ValueError, match='bit-exact'):
        capacity.prove_initialization('unused-local-target', None, reference)


@pytest.mark.parametrize('mutation', ['partial', 'loss', 'row', 'hash', 'extra_b', 'retention'])
def test_sealed_proof_contract_rejects_missing_or_drifted_inputs(reference, proof, mutation):
    if mutation == 'partial': proof['checked_inputs'] = 14
    if mutation == 'loss': proof['expanded'][-1]['weighted_loss_sha256'] = 'f' * 64
    if mutation == 'row': proof['parent40'][0]['row_id'] = 'f' * 64; proof['expanded'] = proof['parent40']
    if mutation == 'hash': proof['parent40'][0]['logits_sha256'] = 'not a hash'; proof['expanded'] = proof['parent40']
    if mutation == 'extra_b': proof['extra_b_zero'] = False
    if mutation == 'retention': proof['parent_tensors_retained'] = False
    with pytest.raises(ValueError): capacity.validate_proof(proof, reference)


@pytest.fixture
def plan_metadata(monkeypatch, reference, anchors, proof):
    monkeypatch.setattr(capacity, 'source_inventory', lambda: {'engineering_capacity_campaign.py': 'c' * 64})
    monkeypatch.setattr(capacity, 'baseline_lineage', lambda ref, p, pr: dict(
        reference=copy.deepcopy(ref), reused=True, initialization_proof_sha256=digest(pr)))
    return capacity.make_plan(reference, anchors, proof)


@pytest.mark.parametrize('field', ['schedule', 'initial', 'sources', 'lora', 'value_weight',
                                  'completion_tokens', 'source_sha256', 'extra_metadata'])
def test_own_loader_rejects_any_non_capacity_recipe_drift(monkeypatch, reference, plan_metadata, field):
    p = copy.deepcopy(plan_metadata)
    if field == 'schedule': p[field] = p[field][::-1]
    elif field == 'initial': p[field]['manifest_sha256'] = 'f' * 64
    elif field == 'sources': p[field]['paired']['manifest_sha256'] = 'f' * 64
    elif field == 'lora': p[field]['rank'] = 32
    elif field == 'source_sha256': p[field] = {}
    else: p[field] = 999
    monkeypatch.setattr(capacity, 'verify', lambda root: 'owner')
    monkeypatch.setattr(capacity, 'read_json', lambda path: p)
    monkeypatch.setattr(capacity, 'reference_evidence', lambda ref: (None, reference, {}, []))
    with pytest.raises(ValueError): capacity.load_plan('/unused-capacity-plan', 'owner')


def test_own_loader_accepts_same_schema_depth8_without_old_loader_on_capacity(monkeypatch, reference, plan_metadata):
    parent = {'adapter_names': plan_metadata['initialization_proof']['parent_adapter_names']}
    monkeypatch.setattr(capacity, 'verify', lambda root: 'owner')
    monkeypatch.setattr(capacity, 'read_json', lambda path: parent if str(path).startswith('/immutable/parent40') else plan_metadata)
    monkeypatch.setattr(capacity, 'reference_evidence', lambda ref: ('data', reference, 'result', 'baseline'))
    assert capacity.load_plan('/unused-capacity-plan', 'owner') == ('data', plan_metadata, 'result', 'baseline')


def test_unsealed_reference_aborts_before_loading_model_or_forward(monkeypatch, anchors):
    def unsealed(path): raise ValueError('unsealed reference')
    def forbidden(*args): raise AssertionError('model/forward must not start')
    monkeypatch.setattr(capacity, 'verify', unsealed)
    monkeypatch.setattr(capacity, 'prove_initialization', forbidden)
    with pytest.raises(ValueError, match='unsealed'):
        capacity.plan('/unused-target', '/tmp/capacity-unsealed-never-created', anchors)


def test_fresh_output_cannot_change_any_sealed_input(reference, anchors):
    for path in [v['path'] for v in anchors.values()] + [v['path'] for v in reference['sources'].values()] + ['/immutable/parent40', '/immutable/parent-plan']:
        with pytest.raises(ValueError, match='outside'):
            capacity.fresh_output(path + '/new-output', anchors, reference)


def test_missing_alignment_audit_blocks_run_before_any_model_load(monkeypatch, plan_metadata):
    monkeypatch.setattr(capacity, 'load_plan', lambda *args: (None, plan_metadata, {}, []))
    def forbidden(*args): raise AssertionError('model loading must not start')
    monkeypatch.setattr(capacity, '_expanded', forbidden)
    with pytest.raises(ValueError, match='alignment audit'):
        capacity.train('/unused-plan', 'anchor', '/unused-target', '/tmp/capacity-never-trained')


@pytest.fixture
def alignment(plan_metadata):
    p = plan_metadata
    return dict(schema=capacity.ALIGNMENT_SCHEMA, passed=True,
                optimizer_run=False, frozen_base_unchanged=True,
                plan_manifest_sha256=p['reference']['plan']['manifest_sha256'],
                result_manifest_sha256=p['reference']['result']['manifest_sha256'],
                source_sha256=capacity.identity(capacity.Path(capacity.__file__).with_name('engineering_causal_audit.py')),
                rows=[dict(index=p['indices'][slot], skill=skill, target_position=position,
                           **{k: dict(maximum_absolute_difference=.00001, top_token_equal=True)
                              for k in capacity.ALIGNMENT_CHECKS})
                      for slot, skill, positions in capacity.ALIGNMENT_COVERAGE for position in positions],
                maximum_logit_difference=.00001,
                text_image_text=dict(first_step_logits=dict(maximum_absolute_difference=0., top_token_equal=True),
                                     generated_ids_equal=True))


@pytest.mark.parametrize('field', ['passed', 'schema', 'plan_manifest_sha256', 'result_manifest_sha256',
                                  'source_sha256', 'rows', *capacity.ALIGNMENT_CHECKS,
                                  'top_token', 'state_logits', 'state_top', 'state_ids',
                                  'maximum', 'extra_state', 'duplicate_position', 'state_skill',
                                  'optimizer', 'frozen', 'nonfinite', 'negative', 'empty'])
def test_alignment_gate_requires_all_checks_and_exact_attribution(monkeypatch, plan_metadata, alignment, field):
    if field in capacity.ALIGNMENT_CHECKS: alignment['rows'][0][field]['maximum_absolute_difference'] = .002
    elif field == 'rows': alignment[field] = alignment[field][:-1]
    elif field == 'passed': alignment[field] = False
    elif field == 'top_token': alignment['rows'][-1]['cache_prefill']['top_token_equal'] = False
    elif field == 'state_logits': alignment['text_image_text']['first_step_logits']['maximum_absolute_difference'] = .002
    elif field == 'state_top': alignment['text_image_text']['first_step_logits']['top_token_equal'] = False
    elif field == 'state_ids': alignment['text_image_text']['generated_ids_equal'] = False
    elif field == 'maximum': alignment['maximum_logit_difference'] = 0.
    elif field == 'extra_state': alignment['rows'].append(copy.deepcopy(alignment['rows'][-1]))
    elif field == 'duplicate_position': alignment['rows'][1] = copy.deepcopy(alignment['rows'][0])
    elif field == 'state_skill': alignment['rows'][0]['skill'] = 'measurement'
    elif field == 'optimizer': alignment['optimizer_run'] = True
    elif field == 'frozen': alignment['frozen_base_unchanged'] = False
    elif field == 'nonfinite': alignment['rows'][0]['teacher_vs_prefix']['maximum_absolute_difference'] = float('nan')
    elif field == 'negative': alignment['rows'][0]['teacher_vs_prefix']['maximum_absolute_difference'] = -.001
    elif field == 'empty': alignment['rows'] = []
    else: alignment[field] = 'f' * 64
    monkeypatch.setattr(capacity, 'verify', lambda root: 'owner')
    monkeypatch.setattr(capacity, 'read_json', lambda path: alignment)
    with pytest.raises(ValueError, match='alignment'):
        capacity.alignment_evidence(dict(path='/unused-audit', manifest_sha256='owner'), plan_metadata)


def test_alignment_and_control_do_not_promote_failed_primary_gate(monkeypatch, plan_metadata, alignment):
    monkeypatch.setattr(capacity, 'verify', lambda root: 'owner')
    monkeypatch.setattr(capacity, 'read_json', lambda path: alignment)
    item = dict(path='/unused-audit', manifest_sha256='owner')
    assert capacity.alignment_evidence(item, plan_metadata) == item
    before = [dict(exact=i < 7) for i in range(15)]; workflows = [dict(success=False)] * 3
    result = capacity.score_result(before, before, workflows, workflows)
    assert result['after_skills'] == 7 and result['after_workflows'] == 0
    comparison = capacity.control_comparison(result, result)
    assert comparison['after_skill_delta'] == comparison['after_workflow_delta'] == 0
    assert comparison['reference_score'] == result
    with pytest.raises(ValueError): capacity.score_result(before[:-1], before, workflows, workflows)


def test_tensor_fingerprint_includes_dtype_shape_and_every_byte():
    t = torch.arange(6, dtype=torch.float32).reshape(2, 3)
    assert capacity.tensor_sha256(t) == capacity.tensor_sha256(t.clone())
    assert capacity.tensor_sha256(t) != capacity.tensor_sha256(t.reshape(3, 2))
    assert capacity.tensor_sha256(t) != capacity.tensor_sha256(t.double())
    changed = t.clone(); changed[-1, -1] += 1
    assert capacity.tensor_sha256(t) != capacity.tensor_sha256(changed)


def test_inventory_includes_new_runner_and_shared_training_replay_sources():
    inventory = capacity.source_inventory()
    assert set(inventory) == set(capacity.FILES)
    assert set(capacity.physics.FILES) <= set(inventory)
    assert {'engineering_capacity_campaign.py', 'engineering_staged_audit.py',
            'engineering_scenarios.py', 'engineering_protocol.py', 'grid_editor.py'} <= set(inventory)
    assert all(len(sha) == 64 for sha in inventory.values())


def test_actual_sealed_causal_receipt_matches_reader_without_loading_model():
    root = capacity.Path(capacity.ALIGNMENT_AUDIT)
    if not (root / 'manifest.json').exists():
        pytest.skip('local owner causal audit not distributed with unit tests')
    p = capacity.read_json(capacity.Path(capacity.REFERENCE_PLAN) / 'summary.json')
    p['reference'] = capacity.default_reference()
    item = dict(path=str(root), manifest_sha256=capacity.verify(root))
    assert capacity.alignment_evidence(item, p) == item
    audit = capacity.read_json(root / 'summary.json')
    assert len(audit['rows']) == 8 and len({r['index'] for r in audit['rows']}) == 3
    assert audit['result_manifest_sha256'] == capacity.REFERENCE_RESULT_SHA
    assert audit['result_manifest_sha256'] != p['initial']['manifest_sha256']


@pytest.fixture
def reference_receipts(monkeypatch, reference, anchors):
    # Faithful 15+3/15+3 receipt layout; mechanics have their own replay tests.
    cases = [dict(id=x) for x in reference['full_episode_ids']]
    data = SimpleNamespace(rows=[dict(skill='operation') for _ in range(15)],
                           messages=lambda index: ([], dict(operation='validate')),
                           cases=lambda ids: cases)
    skill_rows = lambda candidate: [dict(
        index=i, candidate=candidate, skill='operation',
        group='train' if i in reference['indices'] else 'untrained_question',
        expected=dict(operation='validate'),
        completion='{"operation":"validate"}' if i < 7 else '{"operation":"simulate"}',
        format_valid=True, exact=i < 7) for i in range(15)]
    traces = {candidate: [dict(task_id=c['id'], candidate=candidate, calls=24, success=False)
                          for c in cases] for candidate in ('adapter40', 'physics78')}
    before, after = skill_rows('adapter40'), skill_rows('physics78')
    scores = capacity.score_result(before, after, traces['adapter40'], traces['physics78'])
    result = dict(before=before, after=after, score=scores, gate_passed=False,
                  initial_manifest_sha256=reference['initial']['manifest_sha256'],
                  workflow_results={c: [dict(task_id=r['task_id'], calls=24, success=False)
                                        for r in rows] for c, rows in traces.items()})
    replayed = []
    def replay(case, trace, processor):
        replayed.append((trace['candidate'], case['id']))
        return dict(task_id=case['id'], calls=24, success=False)
    audit = dict(schema='power-grid-env.staged-campaign-audit/v1', replay_passed=True,
                 native_prompt_budgets_checked=True,
                 source_manifest_sha256=anchors['result']['manifest_sha256'],
                 plan_manifest_sha256=anchors['plan']['manifest_sha256'],
                 rows=[dict(candidate=c, task_id=r['task_id'], calls=24, success=False)
                       for c, rows in traces.items() for r in rows], score=copy.deepcopy(scores), gate_passed=False)
    files = {anchors['audit']['path'] + '/summary.json': audit,
             anchors['result']['path'] + '/before.json': dict(complete=True, skills=before, workflow_successes=0)}
    for c, rows in traces.items():
        for row in rows:
            files[anchors['result']['path'] + '/episodes/' + c + '-' + row['task_id'] + '.json'] = row
    monkeypatch.setattr(capacity, 'verify', lambda path: next(v['manifest_sha256'] for v in anchors.values() if v['path'] == str(path)))
    monkeypatch.setattr(capacity, 'read_json', lambda path: files[str(path)])
    monkeypatch.setattr(capacity.physics, 'load_plan', lambda *args: (data, reference))
    monkeypatch.setattr(capacity, 'checkpoint', lambda *args: (result, reference))
    monkeypatch.setattr(capacity, 'replay', replay)
    return result, audit, files, replayed


def test_reference_requires_all_six_traces_and_preserves_failed_gate(anchors, reference_receipts):
    result, audit, files, replayed = reference_receipts
    _, _, actual, baseline = capacity.reference_evidence(anchors)
    assert actual is result and result['gate_passed'] is False
    assert len(baseline) == 3 and len(replayed) == 6
    assert {c for c, task in replayed} == {'adapter40', 'physics78'}
    assert result['score']['before_skills'] == result['score']['after_skills'] == 7


@pytest.mark.parametrize('mutation', ['replay', 'native', 'reference_result', 'reference_plan',
                                     'missing_trace_row', 'audit_success', 'audit_gate', 'result_gate',
                                     'skill_completion', 'skill_expected', 'skill_candidate',
                                     'skill_missing', 'baseline', 'parent'])
def test_reference_rejects_partial_or_misattributed_audit(anchors, reference_receipts, mutation):
    result, audit, files, _ = reference_receipts
    if mutation == 'replay': audit['replay_passed'] = False
    if mutation == 'native': audit['native_prompt_budgets_checked'] = False
    if mutation == 'reference_result': audit['source_manifest_sha256'] = 'f' * 64
    if mutation == 'reference_plan': audit['plan_manifest_sha256'] = 'f' * 64
    if mutation == 'missing_trace_row': audit['rows'].pop()
    if mutation == 'audit_success': audit['rows'][-1]['success'] = True
    if mutation == 'audit_gate': audit['gate_passed'] = True
    if mutation == 'result_gate': result['gate_passed'] = True
    if mutation == 'skill_completion': result['after'][0]['completion'] = '{"operation":"submit"}'
    if mutation == 'skill_expected': result['after'][0]['expected'] = dict(operation='submit')
    if mutation == 'skill_candidate': result['after'][0]['candidate'] = 'parent40'
    if mutation == 'skill_missing': result['after'].pop()
    if mutation == 'baseline': files[anchors['result']['path'] + '/before.json']['workflow_successes'] = 1
    if mutation == 'parent': result['initial_manifest_sha256'] = 'f' * 64
    with pytest.raises(ValueError): capacity.reference_evidence(anchors)


@pytest.mark.parametrize('mutation', ['state', 'logits'])
def test_run_rechecks_initialization_before_optimizer_or_output(monkeypatch, plan_metadata, mutation):
    import power_grid_env.qwen35_training as training
    metadata = {k: plan_metadata['initialization_proof'][k] for k in (
        'parent_adapter_names', 'expanded_adapter_names', 'parent_adapter_state_sha256',
        'expanded_adapter_state_sha256', 'extra_b_zero', 'parent_tensors_retained')}
    signatures = copy.deepcopy(plan_metadata['initialization_proof']['expanded'])
    if mutation == 'state': metadata['expanded_adapter_state_sha256'] = 'f' * 64
    if mutation == 'logits': signatures[-1]['logits_sha256'] = 'f' * 64
    monkeypatch.setattr(capacity, 'load_plan', lambda *args: (None, plan_metadata, {}, []))
    monkeypatch.setattr(capacity, 'alignment_evidence', lambda *args: dict(path='/immutable/causal-audit'))
    monkeypatch.setattr(capacity, 'fresh_output', lambda *args, **kwargs: None)
    monkeypatch.setattr(capacity, '_expanded', lambda *args: (SimpleNamespace(model=object(), processor=None), metadata))
    monkeypatch.setattr(training, 'frozen_fingerprint', lambda model: 'frozen')
    monkeypatch.setattr(capacity, 'signatures', lambda *args: signatures)
    def forbidden(*args, **kwargs): raise AssertionError('optimizer/output must not start')
    monkeypatch.setattr(torch.optim, 'AdamW', forbidden)
    monkeypatch.setattr(capacity, 'write_json', forbidden)
    with pytest.raises(ValueError, match='proof'):
        capacity.train('/unused-plan', 'anchor', '/unused-target', '/tmp/capacity-proof-fails')
