import copy
import json
from benchmarks import engineering_agent as env
from benchmarks.engineering_observations import compact_pf,compact_messages


def result():
    task=next(t for t in env.registry() if t['kind']=='repair' and t['bug']=='impedance')
    state=env.reset(task['id']);_,response=env.apply(state,'simulate',{})
    assert response['ok']
    return response['result'],task


def test_all_acceptance_values_and_diagnostic_passports_preserved():
    full,task=result();before=copy.deepcopy(full);small=compact_pf(full)
    assert env.physical_errors(full,env.requirements(task))==env.physical_errors(small,env.requirements(task))
    assert full==before
    for a,b in zip(full['nodes'],small['nodes']):
        assert a['energized']==b['energized']
        for phase in ('A','B','C'):
            assert a['phases'][phase]['voltage_pu']==b['phases'][phase]['voltage_pu']
            assert a['phases'][phase]['energized']==b['phases'][phase]['energized']
    for a,b in zip(full['branches'],small['branches']):
        assert a['passport']==b['passport'] and a['loading_pct']==b['loading_pct']
        assert a['from_terminal']==b['from_terminal']
    assert small['source']==full['source'] and small['warnings']==full['warnings']
    assert len(json.dumps(small))<len(json.dumps(full))


def test_only_public_pf_tool_results_change():
    full,_=result()
    messages=[dict(role='system',content='rules'),dict(role='user',content=json.dumps({'task':{'requirements':'immutable'}})),
        dict(role='assistant',content='{"name":"simulate","arguments":{}}'),
        dict(role='user',content=json.dumps(dict(ok=True,result=full,observation={'revision':0})))]
    original=copy.deepcopy(messages);small=compact_messages(messages)
    assert small[0]==messages[0] and small[2]==messages[2] and json.loads(small[1]['content'])==json.loads(messages[1]['content']) and messages==original
    assert json.loads(small[-1]['content'])['observation']=={'revision':0}
    assert compact_pf({'profile':'ac_power_flow','unknown':3})=={'profile':'ac_power_flow','unknown':3}


def test_exact_pf_duplicates_reference_prior_message():
    from benchmarks.engineering_observations import referenced_messages
    full,_=result()
    messages=[dict(role='system',content='rules'),dict(role='user',content=json.dumps({'task':{}})),
        dict(role='assistant',content='{}'),dict(role='user',content=json.dumps(dict(result=full,observation={'calls_remaining':31}))),
        dict(role='assistant',content='{}'),dict(role='user',content=json.dumps(dict(result=full,observation={'calls_remaining':30})))]
    reduced=referenced_messages(messages)
    assert json.loads(reduced[5]['content'])['result']['same_public_pf_result_as_message']==3
    assert json.loads(reduced[5]['content'])['observation']['calls_remaining']==30
    changed=copy.deepcopy(messages);value=json.loads(changed[5]['content']);value['result']['nodes'][0]['phases']['A']['voltage_pu']+=.001;changed[5]['content']=json.dumps(value)
    assert 'same_public_pf_result_as_message' not in json.loads(referenced_messages(changed)[5]['content'])['result']


def test_editor_project_with_same_profile_is_not_a_solved_report():
    from benchmarks.engineering_observations import referenced_messages
    project=env.initial_project(env.registry()[0])
    assert project['profile']=='passive_power_flow'
    assert compact_pf(project)==project
    messages=[dict(role='user',content=json.dumps({'result':project}))]*2
    small=referenced_messages(messages)
    assert all(json.loads(m['content'])['result']==project for m in small)


def test_dead_branch_measurements_remain_explicit_none():
    task=next(t for t in env.registry() if t['family']=='chain' and t['kind']=='diagnose' and t['bug']=='missing_connection')
    _,response=env.apply(env.reset(task['id']),'simulate',{})
    full=response['result'];small=compact_pf(full)
    assert env.physical_errors(full,env.requirements(task))==env.physical_errors(small,env.requirements(task))
    for a,b in zip(full['branches'],small['branches']):
        if a['from_terminal'] is None:assert b['from_terminal'] is None
