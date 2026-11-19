import copy
import pytest

from benchmarks.engineering_catalog_challenges import generate
from benchmarks.engineering_scenarios import generate_scenarios
from benchmarks.engineering_data_observability_audit import counterexamples, audit


def sample():
    base = next(c for c in generate_scenarios(420, 1, (1, 1, 1)) if c['kind'] == 'repair')
    return generate(base, 0)[0]


def test_hidden_precision_and_actual_power_collisions_are_initial_only():
    case = sample()
    original = copy.deepcopy(case)
    result = counterexamples(case)
    assert case == original
    assert {r['kind'] for r in result['collisions']} == {
        'rounded_line_resistance', 'omitted_installed_load_power'}
    assert all(r['initial_public_cards_equal'] and r['altered_structure_errors']
               and not r['baseline_structure_errors'] for r in result['collisions'])


def test_preferred_validate_does_not_exclude_successful_simulate_first():
    result = counterexamples(sample())
    assert result['teacher_preferred_operation'] == 'validate'
    assert result['healthy_simulate_submit_success']


def test_audit_rejects_wrong_source_before_creating_output(tmp_path):
    from power_grid_env.study_artifacts import seal
    source = tmp_path / 'source'
    source.mkdir()
    seal(source)
    output = tmp_path / 'out'
    with pytest.raises(ValueError, match='manifest differs'):
        audit(source, '0' * 64, output)
    assert not output.exists()
