import json
import pytest
from benchmarks.engineering_learning_gate import rollout,strict_contract
from benchmarks.engineering_student_recovery import PublicCorrector,correction,expert_messages,export
from benchmarks.engineering_scenarios import generate_scenarios


@pytest.mark.parametrize('bad',['not-json',json.dumps(dict(name='inspect_project',arguments=dict(unexpected=True))),json.dumps(dict(name='simulate',arguments={}))])
def test_actual_bad_student_prefix_is_context_and_public_correction_succeeds(bad):
    case=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair')
    row=rollout(case,lambda m:bad,strict_contract().model_copy(update={'maximum_actions':1}))
    ep=correction(case,row['turns'],1)
    assert ep['success'] and ep['events'][0]['source']=='student'
    targets=list(expert_messages(ep));assert len(targets)==ep['expert_targets']
    assert all(action!=dict(name='__invalid_action__',arguments={}) for _,action in targets)
    assert any(messages[2]['content']==bad for messages,action in targets)
    assert targets[-1][1]['name']=='submit'


def test_terminal_or_exhausted_prefix_cannot_become_positive_label():
    case=generate_scenarios(420,1,(1,1,1))[0]
    raw=json.dumps(dict(name='submit',arguments={}))
    row=rollout(case,lambda m:raw,strict_contract())
    assert correction(case,row['turns'],1) is None
    row=rollout(case,lambda m:'bad',strict_contract())
    assert correction(case,row['turns'],24) is None
    ep=correction(case,row['turns'],23)
    assert not ep['success'] and ep['expert_targets']==0
    with pytest.raises(ValueError):list(expert_messages(ep))


def test_recovery_rejects_heldout_and_changed_public_response():
    case=generate_scenarios(420,1,(1,1,1))[0];row=rollout(case,lambda m:'bad',strict_contract().model_copy(update={'maximum_actions':1}))
    row['turns'][0]['response']['ok']=True
    with pytest.raises(ValueError):correction(case,row['turns'],1)
    case['split']='dev'
    with pytest.raises(ValueError):correction(case,row['turns'],1)


def test_candidate_corrections_are_admitted_only_from_audited_train_model_rollouts(tmp_path,monkeypatch):
    import types
    import benchmarks.engineering_student_recovery as module
    import benchmarks.engineering_learning_gate as gate
    from benchmarks.engineering_factory import write_json
    from power_grid_env.study_artifacts import verify,read_json
    case=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair')
    row=rollout(case,lambda m:'bad',strict_contract().model_copy(update={'maximum_actions':1}))
    data=types.SimpleNamespace(scenarios={case['id']:case})
    monkeypatch.setattr(module,'ProceduralDataset',lambda *a:data)
    monkeypatch.setattr(gate,'load_suite',lambda *a:dict(task_ids=[case['id']]))
    report=dict(source=dict(role='tiny_train',corpus_manifest_sha256='a'*64,suite_manifest_sha256='b'*64),candidate=dict(kind='native_model'))
    monkeypatch.setattr(module,'audit',lambda *a:(report,[row]))
    r=export('data','a'*64,'suite','b'*64,'eval','c'*64,tmp_path/'ok')
    assert r['visited_prefixes']==r['successful_corrections']==1 and r['expert_targets']>0 and verify(tmp_path/'ok')
    assert read_json(tmp_path/'ok'/'summary.json')['source_candidate']['kind']=='native_model'
    report['candidate']['kind']='public_deterministic_control'
    with pytest.raises(ValueError):export('data','a'*64,'suite','b'*64,'eval','c'*64,tmp_path/'control')
    assert not (tmp_path/'control').exists()
    report['candidate']['kind']='native_model';report['source']['role']='dev'
    with pytest.raises(ValueError):export('data','a'*64,'suite','b'*64,'eval','c'*64,tmp_path/'heldout')


def test_transport_failure_stays_in_collection_denominator(tmp_path,monkeypatch):
    import types
    import benchmarks.engineering_student_recovery as module
    import benchmarks.engineering_learning_gate as gate
    case=generate_scenarios(420,1,(1,1,1))[0]
    def failed(messages):raise RuntimeError('no response')
    row=rollout(case,failed,strict_contract())
    monkeypatch.setattr(module,'ProceduralDataset',lambda *a:types.SimpleNamespace(scenarios={case['id']:case}))
    monkeypatch.setattr(gate,'load_suite',lambda *a:dict(task_ids=[case['id']]))
    report=dict(source=dict(role='tiny_train',corpus_manifest_sha256='a'*64,suite_manifest_sha256='b'*64),candidate=dict(kind='native_model'))
    monkeypatch.setattr(module,'audit',lambda *a:(report,[row]))
    r=export('data','a'*64,'suite','b'*64,'eval','c'*64,tmp_path/'out')
    assert r['tasks']==r['source_turns']==1 and r['visited_prefixes']==r['expert_targets']==0
    assert r['attempts'][0]['status']=='transport_failure_no_action'
