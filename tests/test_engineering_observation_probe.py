import json,copy
import pytest
from benchmarks.engineering_observation_probe import annotated


def request():
    public=dict(has_fresh_measurements=True,goal=dict(minimum_voltage_pu=.9,maximum_branch_loading_pct=100),
                study=dict(converged=True,nodes=[dict(energized=True,voltage_pu_min=1.)],branches=[dict(loading_pct=110)]))
    return dict(stage='operation',messages=[dict(role='system',content='Choose an operation'),dict(role='user',content=json.dumps(public))],maximum_completion_tokens=128)


def test_annotation_uses_only_public_measurements_and_preserves_goal_and_raw_values():
    q=request();before=copy.deepcopy(q);result=annotated(q);p=json.loads(result['messages'][-1]['content'])
    assert q==before and p['public_constraint_status']['measurement_feasible'] is False
    assert p['public_constraint_status']['overloaded_branch_count']==1
    assert p['goal']==json.loads(q['messages'][-1]['content'])['goal']
    assert 'operation' not in p['public_constraint_status'] and 'answer' not in p
    assert result['maximum_completion_tokens']==128


def test_annotation_honors_user_limits_and_does_not_leak_measurement_labels_into_their_task():
    q=request();p=json.loads(q['messages'][-1]['content']);p['goal']['maximum_branch_loading_pct']=120
    q['messages'][-1]['content']=json.dumps(p)
    assert json.loads(annotated(q)['messages'][-1]['content'])['public_constraint_status']['measurement_feasible'] is True
    q['stage']='measurement'
    with pytest.raises(ValueError):annotated(q)
