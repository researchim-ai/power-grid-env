from pathlib import Path
import pytest
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,seal,read_json
import benchmarks.engineering_student_campaign as module


@pytest.mark.parametrize('passed',[False,True])
def test_native_rejection_preserves_failures_and_never_plans_training(tmp_path,monkeypatch,passed):
    calls=[]
    monkeypatch.setattr(module,'wait_sealed',lambda *a:'e'*64)
    def export(*args):
        root=args[-1];write_json(root/'summary.json',dict(complete=True));seal(root)
        return dict(tasks=8,source_turns=20,visited_prefixes=20,successful_corrections=10,expert_targets=40)
    def qualify(*args):
        root=args[-1];write_json(root/'summary.json',dict(passed=passed));seal(root)
        return dict(passed=passed,checked_targets=40 if passed else 39,failures=[] if passed else [dict(index=7,error='context_budget')])
    def plan(*args,**kwargs):
        calls.append('plan');write_json(args[2]/'summary.json',dict(optimizer_run=False));seal(args[2])
    monkeypatch.setattr(module,'export',export);monkeypatch.setattr(module,'qualify',qualify);monkeypatch.setattr(module,'plan',plan)
    r=module.finish('train','t'*64,'suite','s'*64,'eval','preflight','p'*64,'target','q'*64,tmp_path/'out')
    assert r['complete'] and r['admission_passed']==passed and r['optimizer_run'] is False
    assert calls==(['plan'] if passed else [])
    assert verify(tmp_path/'out') and r['preflight_failures']==(0 if passed else 1)


def test_wait_failure_is_sealed_incomplete_and_never_exports(tmp_path,monkeypatch):
    def failed(*a):raise TimeoutError('not sealed')
    monkeypatch.setattr(module,'wait_sealed',failed)
    with pytest.raises(TimeoutError):module.finish('train','t'*64,'suite','s'*64,'eval','preflight','p'*64,'target','q'*64,tmp_path/'out')
    assert verify(tmp_path/'out')
    r=read_json(tmp_path/'out'/'failure.json');assert not r['complete'] and not r['admission_passed'] and r['failed_phase']=='wait_for_complete_student_evaluation'
    assert not (tmp_path/'out'/'corrections').exists()
