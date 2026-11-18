import copy
import json
from pathlib import Path
import shutil
import pytest
from benchmarks.engineering_learning_compare import audit,compare
from benchmarks.engineering_learning_gate import strict_contract,evaluate_cases
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_recovery import RecoveryPolicy
from benchmarks.engineering_scenarios import generate_scenarios
from power_grid_env.study_artifacts import seal,verify,read_json


@pytest.fixture
def paired(tmp_path):
    cases=generate_scenarios(420,1,(1,1,1))[:3];c=strict_contract().model_copy(update={'maximum_actions':20})
    source=dict(role='tiny_train',suite_manifest_sha256='s'*64);public=RecoveryPolicy()
    b=evaluate_cases(cases,lambda m:'bad',tmp_path/'base',c,source,dict(kind='native_model',target_sha256='t'*64,adapter_manifest_sha256=None))
    a=evaluate_cases(cases,lambda m:json.dumps(public(m)),tmp_path/'adapter',c,source,dict(kind='native_model',target_sha256='t'*64,adapter_manifest_sha256='a'*64))
    return tmp_path,cases,c


def test_pair_replays_physics_and_requires_every_episode(paired):
    root,cases,c=paired;r=compare(root/'base',verify(root/'base'),root/'adapter',verify(root/'adapter'),cases,c,root/'comparison')
    assert r['tasks']==r['improved']==3 and r['base_successes']==0 and r['adapter_successes']==3 and r['tiny_learning_gate_passed']
    assert verify(root/'comparison')
    with pytest.raises(ValueError):audit(root/'base',verify(root/'base'),cases[:2],c)
    with pytest.raises(ValueError):audit(root/'base',verify(root/'base'),cases,strict_contract())


@pytest.mark.parametrize('mutation',['summary','response','terminal','inventory','candidate'])
def test_resealed_forgery_does_not_pass_audit_or_pair(paired,mutation):
    root,cases,c=paired;path=root/'bad';shutil.copytree(root/'adapter',path,ignore=shutil.ignore_patterns('manifest.json'))
    if mutation in ('summary','candidate'):
        s=read_json(path/'summary.json')
        if mutation=='summary':s['edits']+=1
        else:s['candidate']['target_sha256']='different'
        write_json(path/'summary.json',s)
    elif mutation=='inventory':(path/(cases[0]['id']+'.json')).unlink()
    else:
        p=path/(cases[0]['id']+'.json');r=read_json(p)
        if mutation=='response':r['turns'][0]['response']['result']['source_r_ohm']*=2
        else:r['success']=False
        write_json(p,r)
    anchor=seal(path)
    with pytest.raises(ValueError):compare(root/'base',verify(root/'base'),path,anchor,cases,c,root/'comparison')


def test_transport_failure_replay_preserves_failure_and_denominator(tmp_path):
    cases=generate_scenarios(420,1,(1,1,1))[:1]
    def failed(messages):raise RuntimeError('bounded failure')
    evaluate_cases(cases,failed,tmp_path/'eval',strict_contract(),dict(role='tiny_train'),dict(kind='native_model'))
    s,rows=audit(tmp_path/'eval',verify(tmp_path/'eval'),cases,strict_contract())
    assert s['tasks']==s['policy_failures']==1 and rows[0]['stop_reason']=='policy_failure'
