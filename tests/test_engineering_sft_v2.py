import copy
import json

import pytest

from benchmarks.engineering_catalog_challenges import generate
from benchmarks.engineering_scenarios import generate_scenarios
from benchmarks.engineering_sft_contract_v2 import ExactEpisode, feedback, request, structure_assessment
from benchmarks.engineering_sft_release_v2 import merge_rows, non_dominant, sha, trajectory
from benchmarks.engineering_sft_admission_v2 import schedule, validate_rows


@pytest.fixture(scope='module')
def case():
    base = next(c for c in generate_scenarios(420, 1, (1, 1, 1)) if c['kind'] == 'repair')
    return generate(base, 0)[0]


@pytest.fixture(scope='module')
def rows(case):
    first, _ = trajectory(case, True)
    second, _ = trajectory(case, False)
    return merge_rows(first + second)


def reidentify(row):
    row['id'] = sha({k: v for k, v in row.items() if k != 'id'})
    return row


@pytest.mark.parametrize('field', ['line', 'load'])
def test_exact_view_distinguishes_previously_hidden_faults(case, field):
    healthy = copy.deepcopy(case)
    healthy['project'] = copy.deepcopy(case['healthy_project'])
    original = ExactEpisode(healthy).observe()[0]
    if field == 'line':
        healthy['project']['lines'][0]['r_ohm_km'] += 1e-8
    else:
        healthy['project']['loads'][0]['p_kw'] += .25
    changed = ExactEpisode(healthy).observe()[0]
    assert original != changed
    assert structure_assessment(original)['structure_matches']
    assert not structure_assessment(changed)['structure_matches']
    assert all('required' in load and 'installed' in load and 'p_kw' not in load for load in changed['scene']['loads'])


def test_tool_semantics_and_inspection_values_are_retained(case):
    episode = ExactEpisode(case)
    card, aliases = episode.observe()
    query = json.loads(request(card, 'operation')[1]['content'])
    assert query['tools'] and 'May run before validate' in query['tool_effects']['simulate']
    line = card['scene']['lines'][0]
    action = dict(name='inspect_equipment', arguments=dict(equipment_id=line['id']))
    reply, _ = episode.apply(action)
    result = feedback(reply, action, aliases)
    assert result['result'] == line
    assert result['last_executed_operation'] == 'inspect_equipment'
    assert 'operation' not in result


def test_study_feedback_is_declared_projection_with_exact_currents(case):
    episode = ExactEpisode(case)
    _, aliases = episode.observe()
    action = dict(name='simulate', arguments={})
    reply, _ = episode.apply(action)
    public = feedback(reply, action, aliases)['result']
    assert public['view'] == 'balanced-study-summary/v2'
    assert public['branches'][0]['current_a'] == reply['result']['branches'][0]['from_terminal']['current_a']
    assert public['nodes'][0]['voltage_pu_min'] == min(p['voltage_pu'] for p in reply['result']['nodes'][0]['phases'].values())
    assert len(json.dumps(public)) < len(json.dumps(reply['result']))


def test_higher_rating_is_not_universally_feasible_and_names_do_not_reveal_role(case):
    original = copy.deepcopy(case)
    harder, evidence = non_dominant(case)
    assert case == original
    assert 'minimum_voltage' in evidence['higher_rating_violations']
    assert all(name.startswith('part-') for name in harder['requirements']['catalog'])
    examples, trace = trajectory(harder, False)
    assert trace['terminal']['success']
    chosen = next(r['answer']['catalog_id'] for r in examples if r['skill'] == 'arguments')
    catalogue = harder['requirements']['catalog']
    assert catalogue[chosen]['rating_a'] < max(c['rating_a'] for c in catalogue.values())


def test_replayed_alternatives_merge_without_conflicting_fact_labels(rows):
    stats = validate_rows(rows)
    assert stats['records'] == len(rows)
    assert any({a['operation'] for a in r['acceptable_answers']} == {'validate', 'simulate'}
               for r in rows if r['skill'] == 'operation')
    assert len({r['input_sha256'] for r in rows}) == len(rows)


def test_admission_rejects_factual_label_tampering_including_bool_integer(rows):
    altered = copy.deepcopy(rows)
    row = next(r for r in altered if r['skill'] == 'state')
    row['answer']['validated'] = int(row['answer']['validated'])
    row['acceptable_answers'] = [row['answer']]
    reidentify(row)
    with pytest.raises(ValueError, match='factual label'):
        validate_rows(altered)


def test_admission_rejects_family_leakage(rows):
    altered = copy.deepcopy(rows)
    altered[-1]['split'] = 'test'
    reidentify(altered[-1])
    with pytest.raises(ValueError, match='split leakage'):
        validate_rows(altered)


def test_no_counterexample_or_failed_action_can_enter_action_gold(rows):
    altered = copy.deepcopy(rows)
    row = next(r for r in altered if r['skill'] == 'operation')
    row['source_kind'] = 'student_failure'
    reidentify(row)
    with pytest.raises(ValueError, match='nonexpert'):
        validate_rows(altered)


def test_schedule_is_seeded_train_only_and_cycles_valid_alternatives(rows):
    stages = [dict(name='debug', steps=200, skills=['operation', 'arguments'])]
    first = schedule(rows, stages, 42)
    assert first == schedule(rows, stages, 42)
    assert first != schedule(rows, stages, 43)
    assert {rows[e['index']]['skill'] for e in first} == {'operation', 'arguments'}
    assert all(e['row_id'] == rows[e['index']]['id'] and rows[e['index']]['split'] == 'train' for e in first)
    assert any(e['alternative'] == 1 for e in first)
    with pytest.raises(ValueError, match='positive stage'):
        schedule(rows, [dict(name='bad', steps=True, skills=['operation'])], 42)


def test_duplicate_factual_inputs_cannot_receive_different_values(rows):
    row = copy.deepcopy(next(r for r in rows if r['skill'] == 'structure'))
    second = copy.deepcopy(row)
    second['answer']['structure_matches'] = not row['answer']['structure_matches']
    second['acceptable_answers'] = [second['answer']]
    with pytest.raises(ValueError, match='conflicting factual'):
        merge_rows([row, second])


def test_extra_installed_load_is_visible_and_rejected_structurally(case):
    altered = copy.deepcopy(case)
    altered['project'] = copy.deepcopy(case['healthy_project'])
    extra = dict(altered['project']['loads'][0], id='unexpected_load')
    altered['project']['loads'].append(extra)
    card, _ = ExactEpisode(altered).observe()
    assert card['scene']['extra_installed_loads'][0]['id'] == 'unexpected_load'
    assert not structure_assessment(card)['required_loads_match']


def test_new_runtime_executes_public_policy_with_actual_physics(case):
    from benchmarks.engineering_sft_runtime_v2 import run
    harder, _ = non_dominant(case)
    def policy(query):
        card = json.loads(query['messages'][1]['content'])
        if query['stage'] == 'arguments':
            line = max(card['study']['branches'], key=lambda x: x['loading_pct'])['id']
            entry = min(card['scene']['catalog'], key=lambda x: x['r_ohm_km'])['id']
            return json.dumps(dict(equipment_id=line, catalog_id=entry))
        operation = 'simulate' if not card['has_fresh_measurements'] else 'set_line_catalog' if any(
            b['loading_pct'] > card['goal']['maximum_branch_loading_pct'] for b in card['study']['branches']) else 'submit'
        return json.dumps(dict(operation=operation))
    result = run(harder, policy)
    assert result['success'] and len(result['events']) == 4
    assert [e['decision']['action']['name'] for e in result['events']] == ['simulate', 'set_line_catalog', 'simulate', 'submit']


def test_invalid_candidate_spends_budget_without_teacher_repair(case):
    from benchmarks.engineering_sft_runtime_v2 import run
    result = run(case, lambda query: '```json\n{}\n```')
    assert not result['success'] and len(result['events']) == 24
    assert result['terminal']['violations'] == ['call_budget']
    assert all(e['decision']['action'] is None for e in result['events'])


@pytest.fixture
def release(case, tmp_path):
    from benchmarks.engineering_sft_release_v2 import export
    from benchmarks.engineering_factory import write_json
    from power_grid_env.study_artifacts import seal, verify
    source = tmp_path / 'source'
    write_json(source / 'summary.json', dict(schema='power-grid-env.catalog-intervention-corpus/v1'))
    write_json(source / 'cases' / 'one.json', dict(case=case))
    anchor = seal(source)
    output = tmp_path / 'release'
    export(source, anchor, output)
    return output, verify(output)


def test_corpus_reconstructs_all_alternatives_and_factual_targets(release):
    from benchmarks.engineering_sft_admission_v2 import load_release
    rows, summary = load_release(*release)
    assert rows and summary['successful_traces'] == 6
    assert summary['records_with_alternative_answers'] > 0
    assert summary['heldout_available'] is False


def test_resealed_schema_valid_but_wrong_argument_label_is_rejected(release):
    from benchmarks.engineering_sft_admission_v2 import load_release
    from power_grid_env.study_artifacts import seal
    path, _ = release
    data = [json.loads(line) for line in (path / 'records.jsonl').read_text().splitlines()]
    row = next(r for r in data if r['skill'] == 'arguments')
    card = json.loads(row['messages'][1]['content'])
    wrong = next(c['id'] for c in card['scene']['catalog'] if c['id'] != row['answer']['catalog_id'])
    row['answer']['catalog_id'] = wrong
    row['acceptable_answers'] = [row['answer']]
    reidentify(row)
    (path / 'records.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in data))
    (path / 'manifest.json').unlink()
    anchor = seal(path)
    with pytest.raises(ValueError, match='reconstruct'):
        load_release(path, anchor)


def test_failed_admission_cannot_load_dataset_or_start_training(tmp_path):
    from benchmarks.engineering_sft_admission_v2 import AdmittedDataset
    from benchmarks.engineering_sft_train_v2 import train
    from benchmarks.engineering_factory import write_json
    from power_grid_env.study_artifacts import seal
    root = tmp_path / 'failed'
    write_json(root / 'summary.json', dict(schema='power-grid-env.exact-sft-admission/v2', target_sha256='a'*64, passed=False))
    anchor = seal(root)
    with pytest.raises(ValueError, match='passing exact'):
        AdmittedDataset(root, anchor, 'a'*64)
    with pytest.raises(ValueError, match='passing exact'):
        train('not-opened.json', 'a'*64, root, anchor, tmp_path/'weights', 1)
    assert not (tmp_path/'weights').exists()


def test_evaluation_accepts_successful_alternative_without_quality_promotion(rows, case):
    from benchmarks.engineering_sft_eval_v2 import evaluate
    row = next(r for r in rows if r['skill'] == 'operation' and len(r['acceptable_answers']) > 1)
    # Answers the opposite of the single preferred target; deliberately does
    # not solve the full episode. These are distinct evaluation denominators.
    other = next(a for a in row['acceptable_answers'] if a != row['answer'])
    result = evaluate(lambda query: json.dumps(other), [row], [case], 'scripted-alternative-control')
    assert result['answer_metrics']['all']['correct'] == 1
    assert result['workflow_successes'] == 0
    assert result['train_diagnostic_only'] and not result['quality_gate_promoted']
