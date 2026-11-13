import copy
from pathlib import Path

import pytest

from power_grid_env.design_study import FeederDesignTask, compile_design, run_design
from power_grid_env.study_artifacts import read_json, verify


def task():
    study = read_json(Path(__file__).resolve().parents[1] / 'cases/studies/induction_start.json')
    study['induction'].update(duration_s=.1, step_s=.005)
    case = dict(id='start', study=study, parameter_provenance='Authored synthetic design exercise',
                motors=[dict(motor_id='m', minimum_final_speed_pu=0, maximum_sampled_current_pu=100)],
                voltage_windows=[dict(node_id='motor', from_s=0, to_s=.1, minimum_voltage_pu=0, maximum_voltage_pu=2)])
    second = copy.deepcopy(case)
    second['id'] = 'strict-start'
    second['motors'][0]['minimum_final_speed_pu'] = 2
    return dict(id='feeder-task', line_id='line',
                options=[dict(id='reference', resistance_ohm=.1, reactance_ohm=.4,
                              provenance='Authored whole-branch impedance; not a cable passport')],
                mandatory_cases=[case, second])


def test_compiler_preserves_owner_data_and_all_scenarios():
    data = task(); before = copy.deepcopy(data)
    _, _, pack = compile_design(data, {'option_id': 'reference'})
    assert data == before
    assert [c.id for c in pack.cases] == ['start', 'strict-start']
    assert pack.cases[1].motors[0].minimum_final_speed_pu == 2
    assert pack.cases[0].study.network.lines[0].r_ohm[0][0] == .1


def test_selected_option_changes_physics_not_acceptance():
    data = task()
    data['options'].append(dict(id='weak', resistance_ohm=20, reactance_ohm=40, provenance='Authored weak feeder'))
    _, _, pack = compile_design(data, {'option_id': 'weak'})
    for original, case in zip(data['mandatory_cases'], pack.cases):
        assert case.study.network.lines[0].r_ohm[0][0] == 20
        assert case.study.network.lines[0].x_ohm[0][0] == 40
        assert case.motors[0].minimum_final_speed_pu == original['motors'][0]['minimum_final_speed_pu']


@pytest.mark.parametrize('proposal', [dict(option_id='unknown'), dict(option_id='reference', mandatory_cases=[]),
                                      dict(option_id='reference', minimum_voltage_pu=0)])
def test_agent_cannot_edit_requirements_or_invent_catalog(proposal):
    with pytest.raises(ValueError):
        compile_design(task(), proposal)


@pytest.mark.parametrize('defect', ['duplicate_option', 'duplicate_case', 'changed_network', 'missing_line',
                                    'expected_violation', 'nan', 'blank_provenance', 'long_run'])
def test_invalid_owner_contract_rejected(defect):
    data = task()
    if defect == 'duplicate_option': data['options'] *= 2
    elif defect == 'duplicate_case': data['mandatory_cases'][1]['id'] = 'start'
    elif defect == 'changed_network': data['mandatory_cases'][1]['study']['network']['name'] = 'other'
    elif defect == 'missing_line': data['line_id'] = 'absent'
    elif defect == 'expected_violation': data['mandatory_cases'][1]['expectation'] = 'violates_limits'
    elif defect == 'nan': data['options'][0]['resistance_ohm'] = float('nan')
    elif defect == 'blank_provenance': data['options'][0]['provenance'] = ' '
    elif defect == 'long_run': data['mandatory_cases'][0]['study']['induction']['duration_s'] = 11
    with pytest.raises(ValueError): FeederDesignTask.model_validate(data)


def test_real_simulation_one_failed_requirement_rejects_whole_design(tmp_path):
    result = run_design(task(), {'option_id': 'reference'}, tmp_path / 'design')
    assert result['completed']
    assert result['cases'][0]['engineering_acceptable']
    assert not result['cases'][1]['engineering_acceptable']
    assert not result['meets_declared_dynamic_requirements']
    assert not result['qualified_electrical_design']
    assert len(result['cases']) == 2


def test_real_simulation_feasible_requirements_pass(tmp_path):
    data = task()
    data['mandatory_cases'][1]['motors'][0]['minimum_final_speed_pu'] = 0
    result = run_design(data, {'option_id': 'reference'}, tmp_path / 'design')
    assert result['completed'] and result['meets_declared_dynamic_requirements']
    assert not result['qualified_electrical_design']
    assert verify(tmp_path / 'design')
    (tmp_path / 'design' / 'proposal.json').write_text('{"option_id":"forged"}')
    with pytest.raises(ValueError): verify(tmp_path / 'design')
