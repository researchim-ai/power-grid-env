import json
import pytest
from benchmarks.engineering_audit import case_metrics,audit
from power_grid_env.study_artifacts import seal


def turn(name,revision,ok=True):
    return dict(turn=0,action=dict(name=name,arguments={}),response=dict(ok=ok,observation=dict(revision=revision)))


def test_repeats_are_scoped_to_revision_and_errors_separate():
    row=dict(task_id='task-a',kind='repair',success=False,stop_reason='turn_budget',calls=6,simulations=3,
        turns=[turn('simulate',0),turn('simulate',0),turn('set_line_catalog',1),turn('simulate',1),turn('simulate',1,False),dict(protocol_error='invalid')])
    result=case_metrics(row)
    assert result['consecutive_unchanged_revision_repeats']==2
    assert result['successful_mutations']==1 and result['successful_simulations']==3
    assert result['protocol_errors']==1 and result['tool_errors']==1 and result['submissions']==0


def fixture(root):
    for role in ('base','adapter'):
        d=root/role;d.mkdir(parents=True)
        (d/'task-a.json').write_text(json.dumps(dict(task_id='task-a',split='dev',kind='diagnose',success=False,reward=0.,stop_reason='turn_budget',calls=1,simulations=1,turns=[turn('simulate',0)])))
        (d/'summary.json').write_text(json.dumps(dict(split='dev',max_turns=12,tasks=1)))
        seal(d)
    from power_grid_env.study_artifacts import verify
    (root/'summary.json').write_text(json.dumps(dict(schema='power-grid-env.engineering-paired-evaluation/v1',split='dev',maximum_turns=12,maximum_new_tokens=64,tasks=1,
        base_manifest_sha256=verify(root/'base'),adapter_evaluation_manifest_sha256=verify(root/'adapter'),base_successes=0,adapter_successes=0,pairs=[dict(task_id='task-a',base_success=False,adapter_success=False)])))
    return seal(root)


def test_sealed_audit_preserves_denominator(tmp_path):
    root=tmp_path/'paired';manifest=fixture(root)
    result=audit(root,manifest,tmp_path/'audit')
    assert result['roles']['base']['tasks']==1 and result['roles']['adapter']['successes']==0
    assert result['roles']['adapter']['no_submit']==1
    with pytest.raises(ValueError):audit(root,'a'*64,tmp_path/'bad')
    with pytest.raises(ValueError):audit(root,manifest,tmp_path/'audit')
