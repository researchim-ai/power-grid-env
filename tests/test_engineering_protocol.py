import copy
import json
import pytest
from benchmarks.engineering_protocol import RunContract,native_completion,parse_native,render_history
from benchmarks.engineering_recovery import collect,episode_messages


def test_contract_is_strict_frozen_and_identity_covers_profile():
    c=RunContract()
    assert c.fingerprint()!=RunContract(protocol='qwen-native-v1').fingerprint()
    for kwargs in [dict(context_tokens=True),dict(unknown=1),dict(enable_thinking=True)]:
        with pytest.raises(ValueError):RunContract(**kwargs)
    with pytest.raises(ValueError):c.action_tokens=128


@pytest.mark.parametrize('action',[
 dict(name='simulate',arguments={}),dict(name='add_line',arguments=dict(route_id='route1',equipment_id='line_a',catalog_id='copper-1')),
 dict(name='submit',arguments=dict(cause='impedance',location='line1',explanation='Измерение U/I подтверждено'))])
def test_native_target_exact_roundtrip(action):
    assert parse_native(native_completion(action),[action['name']])==action


@pytest.mark.parametrize('text',[
 '<tool_call><function=simulate></function></tool_call> trailing',
 '<tool_call><function=simulate></function></tool_call><tool_call><function=simulate></function></tool_call>',
 '<tool_call><function=submit><parameter=cause>a</parameter><parameter=cause>b</parameter></function></tool_call>',
 '<tool_call><function=simulate>ignored</function></tool_call>',
 '<tool_call><function=export></function></tool_call>',
])
def test_malformed_native_is_not_repaired(text):
    with pytest.raises(ValueError):parse_native(text,['simulate','submit','export'])


def test_native_history_uses_tools_and_actual_tool_roles():
    from benchmarks import engineering_agent as env
    episode=collect(env.registry()[0]['id'],'simulate_first')
    messages,_=list(episode_messages(episode))[2]
    before=copy.deepcopy(messages)
    rendered,kwargs=render_history(messages,RunContract(protocol='qwen-native-v1'))
    assert messages==before and kwargs['tools']
    assert 'tool_schemas' not in json.loads(rendered[1]['content'])['task']
    assert rendered[2]['tool_calls'][0]['function']['name']=='simulate'
    assert rendered[3]['role']=='tool' and rendered[3]['tool_call_id']==rendered[2]['tool_calls'][0]['id']
    broken=copy.deepcopy(messages);broken[2]['content']='not an action'
    rendered,_=render_history(broken,RunContract(protocol='qwen-native-v1'))
    assert rendered[2]['content']=='not an action' and rendered[3]['role']=='user'


def test_native_json_fallback_cannot_bypass_protocol_parser():
    from benchmarks.engineering_protocol_policy import adapt_completion
    from benchmarks.engineering_rollout import parse_action
    raw='{"name":"simulate","arguments":{}}'
    text=adapt_completion(raw,RunContract(protocol='qwen-native-v1'),['simulate'])
    with pytest.raises(ValueError):parse_action(text,['simulate'])
    assert adapt_completion(raw,RunContract(),['simulate'])==raw


def test_exposed_tool_mask_matches_dispatch_no_export():
    from benchmarks import engineering_agent as env
    from benchmarks.engineering_recovery import collect
    episode=collect(env.registry()[0]['id'],'clean')
    for protocol in ('json-action-v1','qwen-native-v1'):
        rendered,kwargs=render_history(episode['initial_messages'],RunContract(protocol=protocol))
        task=json.loads(rendered[1]['content'])['task']
        assert 'export' not in task['tools']
        schemas=kwargs.get('tools',task.get('tool_schemas',[]))
        assert 'export' not in [s['function']['name'] for s in schemas]


def test_strict_native_profile_explicitly_matches_one_call_parser():
    from benchmarks import engineering_agent as env
    from benchmarks.engineering_recovery import collect
    messages=collect(env.registry()[0]['id'],'clean')['initial_messages']
    legacy,_=render_history(messages,RunContract(protocol='qwen-native-v1'))
    strict,_=render_history(messages,RunContract(protocol='qwen-native-strict-v1'))
    assert 'No text before or after' in strict[0]['content']
    assert legacy[0]['content']!=strict[0]['content']
    assert RunContract(protocol='qwen-native-v1').fingerprint()!=RunContract(protocol='qwen-native-strict-v1').fingerprint()
