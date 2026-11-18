import inspect
import pytest
from benchmarks import engineering_agent as env
from benchmarks.engineering_public_policy import PublicPolicy,plan_from_project
from benchmarks.engineering_rollout import rollout


@pytest.mark.parametrize('task',env.registry(),ids=lambda t:t['id'])
def test_public_policy_solves_all_authored_tasks(task):
    result=rollout(task['id'],PublicPolicy('compact-pf-v1'),max_turns=12,repeat_limit=2)
    assert result['success'] and result['reward']==1
    assert not any('protocol_error' in t for t in result['turns'])


def test_public_policy_has_no_evaluator_dependency():
    import benchmarks.engineering_public_policy as module
    source=inspect.getsource(module)
    assert 'engineering_agent' not in source and 'registry(' not in source and "['bug']" not in source


def test_ambiguous_public_diagnosis_rejected():
    task=next(t for t in env.registry() if t['kind']=='diagnose')
    project=env.initial_project(task)
    project['lines']=[]
    with pytest.raises(ValueError):plan_from_project(env.public_task(task),project)
