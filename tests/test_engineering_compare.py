import json
import pytest
from benchmarks import engineering_compare as module
from power_grid_env.study_artifacts import seal,verify


def test_paired_same_budgets_and_complete_denominator(tmp_path,monkeypatch):
    commands=[];budgets=[]
    class Policy:
        def __init__(self,command,timeout):self.command=command;commands.append((command,timeout))
        def close(self):pass
    def evaluate(policy,out,split,turns,repeat_limit):
        budgets.append((split,turns));out.mkdir()
        adapted='--adapter' in policy.command
        for i in range(7):
            row=dict(task_id='task-'+str(i),kind='repair',success=adapted and i==1,stop_reason='submitted')
            (out/('task-'+str(i)+'.json')).write_text(json.dumps(row))
        seal(out)
        return dict(successes=int(adapted))
    monkeypatch.setattr(module,'ProcessPolicy',Policy)
    monkeypatch.setattr(module,'evaluate',evaluate)
    monkeypatch.setattr(module,'adapter_receipt',lambda *args:{})
    summary=module.compare('python','target','a'*64,'adapter','b'*64,tmp_path/'paired')
    assert summary['tasks']==7 and summary['improved']==1 and summary['regressed']==0
    assert budgets==[('dev',12),('dev',12)]
    assert commands[0][0]==commands[1][0][:-4]
    assert commands[0][1]==commands[1][1]
    verify(tmp_path/'paired')


def test_comparison_rejects_inadequate_budget(tmp_path):
    for turns in (2,7,33,True):
        with pytest.raises(ValueError):module.compare('python','target','a'*64,'adapter','b'*64,tmp_path/'bad',turns)
    assert not (tmp_path/'bad').exists()
