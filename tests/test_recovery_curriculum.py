import copy
import json
import pytest
from power_grid_env.recovery_curriculum import (diagnose,ResetObservation,ResetDiagnosticSession,teacher_episode,verify_corpus,score,export)
from power_grid_env.study_artifacts import encoded,seal,read_json,verify
from power_grid_env.evidence import package_fingerprint


def observation(**kw):
    return dict(generator_id='g',time_s=.1,connected=False,latched=True,level=.01,reset_level=.02)|kw


@pytest.mark.parametrize('change,reason',[({},'eligible'),({'connected':True},'generator_connected'),
    ({'level':.021},'thermal_level_above_reset'),({'latched':False},'not_latched'),({'reset_level':None},'thermal_reset_disabled')])
def test_diagnosis_matches_current_indication_only(change,reason):
    assert diagnose(observation(**change)).reason==reason


@pytest.mark.parametrize('change',[{'level':float('nan')},{'level':-.1},{'connected':1},{'future_trip':True}])
def test_unphysical_or_privileged_observations_rejected(change):
    with pytest.raises(ValueError):ResetObservation.model_validate(observation(**change))


def test_tools_are_read_only_exact_target_and_submit_once():
    p=observation();s=ResetDiagnosticSession(p);p['level']=100
    assert not s.call('submit_decision',dict(action='request_reset',reason='eligible'))['ok']
    assert not s.call('inspect_generator',dict(generator_id='missing'))['ok']
    assert not s.call('inspect_generator',dict(generator_id='g',future=True))['ok']
    assert s.call('inspect_generator',dict(generator_id='g'))['observation']['level']==.01
    assert s.call('read_reset_policy',dict(generator_id='g'))['policy']['retains_heat']
    r=s.call('submit_decision',dict(action='request_reset',reason='eligible'))
    assert r['ok'] and not r['physical_action_executed']
    assert not s.call('submit_decision',dict(action='request_reset',reason='eligible'))['ok']
    assert not s.call('source_voltage',{})['ok']


def test_teacher_tool_calls_replay_without_future_labels():
    p=observation();messages=teacher_episode(p);s=ResetDiagnosticSession(p)
    assert len(messages)==6 and 'eligible' not in messages[0]['content']
    for a,b in zip(messages[1:5:2],messages[2:5:2]):
        call=a['tool_calls'][0]
        assert b['tool_call_id']==call['id']
        assert json.loads(b['content'])==s.call(call['function']['name'],json.loads(call['function']['arguments']))
        assert not {'expected','reward','future_events'}&json.loads(b['content']).keys()
    assert json.loads(messages[-1]['content'])==diagnose(p).model_dump(mode='json')


def corpus(tmp_path):
    root=tmp_path/'corpus';root.mkdir()
    rows=[dict(id=str(i),family_id='one_family',split='train',observation=observation(level=i*.001),expected=diagnose(observation(level=i*.001)).model_dump(mode='json')) for i in range(18)]
    (root/'corpus.json').write_bytes(encoded(dict(schema='power-grid-env.thermal-reset-curriculum/v1',code_sha256=package_fingerprint(),rows=rows)))
    return root,seal(root)


def test_export_and_scoring_require_owner_anchor_and_strict_decision(tmp_path):
    root,anchor=corpus(tmp_path)
    assert score(root,'0',dict(action='request_reset',reason='eligible'),anchor)['reward']==1.
    assert score(root,'0',dict(action='request_reset',reason='eligible',threshold=1.),anchor)['reward']==0.
    assert score(root,'0',dict(action='wait',reason='thermal_level_above_reset'),anchor)['reward']==0.
    with pytest.raises(ValueError):score(root,'0',{},'wrong_anchor')
    with pytest.raises(ValueError):score(root,'missing',{},anchor)
    out=tmp_path/'sft.jsonl';assert export(root,out,anchor)['rows']==18
    assert all(json.loads(row)['metadata']['split']=='train' for row in out.read_text().splitlines())
    with pytest.raises(FileExistsError):export(root,out,anchor)
    with pytest.raises(ValueError):export(root,root/'leak.jsonl',anchor)


@pytest.mark.parametrize('mutation',['expected','split','runtime'])
def test_resealed_but_wrong_labels_split_or_runtime_rejected(tmp_path,mutation):
    root,_=corpus(tmp_path);data=read_json(root/'corpus.json')
    if mutation=='expected':data['rows'][0]['expected']['action']='wait'
    elif mutation=='split':data['rows'][0]['split']='test'
    else:data['code_sha256']='old_runtime'
    (root/'manifest.json').unlink();(root/'corpus.json').write_bytes(encoded(data));anchor=seal(root)
    with pytest.raises(ValueError):verify_corpus(root,anchor)
