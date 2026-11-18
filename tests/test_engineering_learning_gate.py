import copy
import json
from pathlib import Path
import pytest
from benchmarks.engineering_learning_gate import (strict_contract,select_suite,create_suite,load_suite,epoch_schedule,rollout,evaluate_cases,load_evaluation,model_command)
from benchmarks.engineering_factory import build,ProceduralDataset,write_json
from benchmarks.engineering_scenarios import generate_scenarios
from benchmarks.engineering_recovery import RecoveryPolicy
from power_grid_env.study_artifacts import verify,seal


@pytest.fixture(scope='module')
def data(tmp_path_factory):
    root=tmp_path_factory.mktemp('tiny-gate')/'data';build(root,420,1,(8,1,1))
    return root,ProceduralDataset(root/'train',verify(root/'train'),strict_contract())


def test_suite_diversity_full_target_coverage_and_frozen_identity(data,tmp_path):
    root,d=data;s=create_suite(root/'train',verify(root/'train'),tmp_path/'suite')
    assert len(s['task_ids'])==len(set(s['task_ids']))==s['base_networks']==8
    assert len(s['families'])==8 and set(s['kinds'])=={'assemble','diagnose','repair'}
    assert load_suite(d,tmp_path/'suite',verify(tmp_path/'suite'))==s
    ids=set(s['episode_ids'])
    assert s['target_indices']==[i for i,(e,t) in enumerate(d.targets) if d.episodes[e]['episode_id'] in ids]
    with pytest.raises(ValueError):select_suite(d,7)
    with pytest.raises(ValueError):select_suite(d,True)
    with pytest.raises(ValueError):load_suite(d,tmp_path/'suite','a'*64)
    s['target_indices'].pop();write_json(tmp_path/'tampered'/'summary.json',s)
    with pytest.raises(ValueError):load_suite(d,tmp_path/'tampered',seal(tmp_path/'tampered'))


def test_epoch_schedule_covers_every_expert_once_per_epoch(data):
    s=select_suite(data[1]);order=epoch_schedule(s,3);n=len(s['target_indices'])
    assert order==epoch_schedule(s,3) and len(order)==3*n
    for i in range(3):assert sorted(order[i*n:(i+1)*n])==s['target_indices']
    with pytest.raises(ValueError):epoch_schedule(s,0)


@pytest.mark.parametrize('kind',['assemble','repair','diagnose'])
def test_standalone_public_control_succeeds_without_owner_state(kind):
    c=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']==kind)
    expert=RecoveryPolicy();seen=[]
    def policy(messages):
        assert all(set(m)=={'role','content'} for m in messages)
        assert 'truth' not in json.loads(messages[1]['content'])
        seen.append(copy.deepcopy(messages));return json.dumps(expert(messages))
    r=rollout(c,policy,strict_contract())
    assert r['success'] and r['stop_reason']=='submitted' and r['simulations']>=1
    assert r['turns'][-1]['action']['name']=='submit'


def test_protocol_and_transport_failures_are_counted_and_repeat_guard_works():
    c=generate_scenarios(420,1,(1,1,1))[0]
    r=rollout(c,lambda messages:'not-json',strict_contract().model_copy(update={'maximum_actions':3}))
    assert not r['success'] and r['calls']==3 and len(r['turns'])==3
    assert all('protocol_error' in t for t in r['turns'])
    def failed(messages):raise RuntimeError('transport failed')
    r=rollout(c,failed,strict_contract());assert not r['success'] and r['stop_reason']=='policy_failure'
    r=rollout(c,lambda m:json.dumps(dict(name='inspect_project',arguments={})),strict_contract())
    assert r['stop_reason']=='repeated_public_observation' and r['calls']==2


def test_evaluation_keeps_all_failures_and_control_never_passes_neural_gate(tmp_path):
    cases=generate_scenarios(420,1,(1,1,1))[:3]
    failed=evaluate_cases(cases,lambda m:'bad',tmp_path/'bad',strict_contract().model_copy(update={'maximum_actions':1}),dict(role='tiny_train'),dict(kind='native_model'))
    assert failed['tasks']==3 and failed['successes']==0 and failed['protocol_errors']==3 and not failed['tiny_learning_gate_passed']
    expert=RecoveryPolicy()
    r=evaluate_cases(cases,lambda m:json.dumps(expert(m)),tmp_path/'ok',strict_contract(),dict(role='tiny_train'),dict(kind='public_deterministic_control'))
    assert r['successes']==3 and not r['tiny_learning_gate_passed']
    assert verify(tmp_path/'ok')


def test_heldout_loader_and_policy_command_match_declared_profile(data):
    root,d=data
    cases=load_evaluation(root/'evaluation',verify(root/'evaluation'),'dev')
    assert len(cases)==7 and all(c['split']=='dev' for c in cases)
    with pytest.raises(ValueError):load_evaluation(root/'train',verify(root/'train'),'dev')
    with pytest.raises(ValueError):load_evaluation(root/'evaluation',verify(root/'evaluation'),'train')
    cmd=model_command('python','target','a'*64,strict_contract())
    assert cmd[cmd.index('--context-tokens')+1]=='24576' and cmd[cmd.index('--maximum-actions')+1]=='24'
    with pytest.raises(ValueError):model_command('python','target','a'*64,strict_contract(),'adapter')
