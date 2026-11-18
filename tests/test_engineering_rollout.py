import json
import sys
import pytest
from benchmarks import engineering_agent as env
from benchmarks.engineering_rollout import parse_action, rollout, evaluate, ProcessPolicy


@pytest.mark.parametrize('text',[
 '{"name":"simulate","name":"submit","arguments":{}}',
 '{"name":"simulate","arguments":{},"reward":1}',
 '{"name":"simulate","arguments":[]}',
 '{"name":"export","arguments":{}}',
 '```json\n{}\n```', '{"name":"submit","arguments":{"x":NaN}}', 'x'*8193])
def test_reject_protocol(text):
    with pytest.raises(ValueError):
        parse_action(text,['submit','simulate','export'])


@pytest.mark.parametrize('task',env.registry(),ids=lambda t:t['id'])
def test_teacher_replay_uses_same_public_loop(task):
    actions=iter(env.teacher(task['id'],allow_private=True)['log'])
    def policy(messages):
        assert 'private_state' not in json.dumps(messages)
        action=next(actions)
        return json.dumps(dict(name=action['name'],arguments=action['arguments']))
    result=rollout(task['id'],policy)
    assert result['success'] and result['reward']==1
    assert result['stop_reason']=='submitted'
    assert not result['eligible_for_gold_sft']


def test_invalid_replies_cost_budget():
    result=rollout(env.registry()[0]['id'],lambda m:'invalid',max_turns=3)
    assert result['calls']==3 and result['reward']==0
    assert len(result['turns'])==3
    assert all('protocol_error' in t for t in result['turns'])


def test_exception_is_failure_not_missing_denominator(tmp_path):
    def broken(m):raise TimeoutError()
    summary=evaluate(broken,tmp_path/'eval')
    assert summary['tasks']==7 and summary['failures']==7 and summary['accuracy']==0
    assert (tmp_path/'eval'/'artifact-manifest.json').exists() or len(list((tmp_path/'eval').glob('*manifest*')))>0
    with pytest.raises(FileExistsError):evaluate(broken,tmp_path/'eval')


def test_candidate_mutation_cannot_change_owner():
    def mutate(messages):
        messages.clear()
        return '{"name":"submit","arguments":{}}'
    result=rollout(env.registry()[0]['id'],mutate)
    assert result['reward']==0


def test_process_only_public_messages():
    code='import sys,json; x=json.loads(sys.stdin.readline()); print(json.dumps({"text":json.dumps({"name":"simulate","arguments":{}})}),flush=True)'
    policy=ProcessPolicy([sys.executable,'-c',code],timeout=2)
    try:assert parse_action(policy([dict(role='user',content='public')])['text'],['simulate'])['name']=='simulate'
    finally:policy.close()


def test_process_timeout():
    policy=ProcessPolicy([sys.executable,'-c','import time;time.sleep(10)'],timeout=.02)
    try:
        with pytest.raises(TimeoutError):policy([])
    finally:policy.close()


def test_timeout_poisoned_process_cannot_leak_late_reply():
    policy=ProcessPolicy([sys.executable,'-c','import time;time.sleep(10)'],timeout=.01)
    try:
        with pytest.raises(TimeoutError):policy([])
        with pytest.raises(RuntimeError):policy([])
    finally:policy.close()


def test_pack_never_truncates():
    from benchmarks.engineering_qwen_policy import pack
    class Tensor:
        shape=(1,100)
    class Processor:
        def apply_chat_template(self,*args,**kwargs):
            assert not kwargs.get('truncation',False)
            assert kwargs['enable_thinking'] is False
            return {'input_ids':Tensor()}
    messages=[dict(role='user',content='hello')]
    assert pack(Processor(),messages,200,50)[1]==100
    with pytest.raises(ValueError,match='context_budget'):pack(Processor(),messages,120,50)
    with pytest.raises(ValueError):pack(Processor(),[dict(role='user',content='x',labels='truth')],200,50)


def test_nonreading_candidate_input_has_deadline():
    policy=ProcessPolicy([sys.executable,'-c','import time;time.sleep(10)'],timeout=.02)
    try:
        with pytest.raises(TimeoutError,match='input timeout'):
            policy([dict(role='user',content='x'*500_000)])
    finally:policy.close()


def test_failed_process_can_restart_only_at_new_episode():
    policy=ProcessPolicy([sys.executable,'-c','import sys,json;sys.stdin.readline();print(json.dumps({"text":"ok"}),flush=True)'],timeout=2)
    try:
        assert policy([])['text']=='ok'
        policy.process.wait(timeout=2)
        with pytest.raises(RuntimeError):policy([])
        policy.begin_episode()
        assert policy([])['text']=='ok'
    finally:policy.close()


def test_candidate_context_failure_retained_and_restarted_for_every_case(tmp_path):
    code='import sys,json;sys.stdin.readline();print(json.dumps({"error":"ValueError","detail":"context_budget_exceeded: no truncation"}),flush=True)'
    policy=ProcessPolicy([sys.executable,'-c',code],timeout=2)
    try:
        summary=evaluate(policy,tmp_path/'failures',max_turns=1)
        assert summary['tasks']==7 and summary['failures']==7 and summary['context_failures']==7
    finally:policy.close()


def test_public_repeat_guard_stops_identical_read_only_loops():
    task=next(t for t in env.registry() if t['kind']=='diagnose')
    result=rollout(task['id'],lambda m:'{"name":"simulate","arguments":{}}',max_turns=12,repeat_limit=2)
    assert result['calls']==2 and result['stop_reason']=='repeated_public_observation'
    assert result['reward']==0 and not result['success']
    with pytest.raises(ValueError):rollout(task['id'],lambda m:'',repeat_limit=1)
