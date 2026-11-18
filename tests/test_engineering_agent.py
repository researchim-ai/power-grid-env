import copy,json
import pytest
from benchmarks import engineering_agent as e


def tid(family,kind,bug=None):return next(t['id'] for t in e.registry() if t['family']==family and t['kind']==kind and t['bug']==bug)


def test_disjoint_topology_groups_and_train_only_public_reset():
    rows=e.registry();assert len(rows)==21
    groups={split:{t['family_sha256'] for t in rows if t['split']==split} for split in ('train','dev','test')}
    assert not groups['train']&groups['dev'] and not groups['train']&groups['test'] and not groups['dev']&groups['test']
    with pytest.raises(ValueError):e.reset(tid('star','assemble'))
    assert len([t for t in rows if t['split']=='train'])==7


@pytest.mark.parametrize('identifier',[t['id'] for t in e.registry()])
def test_privileged_teacher_completes_only_bounded_owner_tasks(identifier):
    state=e.teacher(identifier,True);assert state['terminal']['success'];assert state['terminal']['reward']==1
    assert state['last_result_revision']==state['revision']


def test_submit_without_fresh_physics_cannot_pass():
    s=e.reset(tid('chain','assemble'));s,r=e.apply(s,'submit',{})
    assert s['finished'] and r['result']['reward']==0
    assert 'fresh_completed_simulation_required' in r['result']['violations']


def test_edits_invalidate_previous_simulation():
    s=e.reset(tid('chain','repair','impedance'));s,_=e.apply(s,'set_line_catalog',dict(equipment_id='line1',catalog_id='copper-1'));s,r=e.apply(s,'simulate',{});assert r['ok']
    s,_=e.apply(s,'set_line_catalog',dict(equipment_id='line1',catalog_id='copper-2'));s,r=e.apply(s,'submit',{})
    assert not r['result']['success'] and 'fresh_completed_simulation_required' in r['result']['violations']


def test_readonly_diagnosis_and_extra_argument_injection_are_rejected():
    s=e.reset(tid('chain','diagnose','impedance'));before=copy.deepcopy(s['project'])
    s,r=e.apply(s,'set_line_catalog',dict(equipment_id='line1',catalog_id='copper-1'));assert not r['ok'];assert s['project']==before
    s,r=e.apply(s,'simulate',{'reward':1,'requirements':{}});assert not r['ok'];assert s['simulations']==0
    assert 'set_line_catalog' not in r['observation']['task']['tools']


def test_wrong_diagnosis_gets_no_expected_answer_in_feedback():
    s=e.reset(tid('chain','diagnose','ampacity'));s,_=e.apply(s,'simulate',{});s,r=e.apply(s,'submit',dict(cause='impedance',location='line1'))
    assert r['result']['reward']==0;assert 'diagnosis_not_supported' in r['result']['violations']
    assert 'expected' not in json.dumps(r) and 'bug' not in r


def test_failed_tool_attempts_consume_budget_and_episode_cannot_be_resubmitted():
    s=e.reset(tid('chain','assemble'))
    for _ in range(33):s,r=e.apply(s,'unknown',{})
    assert s['finished'] and s['terminal']['reward']==0
    with pytest.raises(ValueError):e.apply(s,'submit',{})


def test_simulation_budget_is_enforced():
    s=e.reset(tid('chain','diagnose','impedance'))
    for _ in range(4):s,r=e.apply(s,'simulate',{});assert r['ok']
    s,r=e.apply(s,'simulate',{});assert not r['ok'] and s['simulations']==4


def test_export_contains_public_tool_trace_not_private_checker_or_gold_claim():
    s=e.teacher(tid('chain','diagnose','impedance'));t=e.task(s['task_id']);data=e.export_trace(s,t)
    assert not data['eligible_for_gold_sft'];assert data['messages'][-1]['role']=='assistant'
    raw=json.dumps(data);assert 'private_state' not in raw and 'expected_location' not in raw and 'last_result_revision' not in raw
    assert data['terminal']['success']


def test_missing_route_cannot_be_fixed_by_isolating_loads():
    s=e.reset(tid('chain','repair','missing_connection'));s,_=e.apply(s,'simulate',{});s,r=e.apply(s,'submit',{})
    assert not r['result']['success'] and 'deenergized_node' in r['result']['violations']


def test_owner_revision_mismatch_is_rejected():
    s=e.reset(tid('chain','assemble'));s['task_sha256']='0'*64
    with pytest.raises(ValueError,match='revision'):e.apply(s,'simulate',{})


def test_tool_schemas_and_runtime_reject_type_injection_and_count_it():
    s=e.reset(tid('chain','assemble'));s,r=e.apply(s,'add_line',dict(route_id='route1',equipment_id='line1',catalog_id={'private_state':1}))
    assert not r['ok'] and s['calls']==1 and not s['project']['lines']
    schemas=e.public_task(e.task(s['task_id']))['tool_schemas'];assert all(x['function']['parameters']['additionalProperties'] is False for x in schemas)


def test_identifiers_do_not_encode_diagnosis_class_or_ordinal():
    for t in e.registry():
        assert t['id'].startswith('task-') and len(t['id'])==29
        assert all(name not in t['id'] for name in (*e.BUGS,'chain','star','diagnose'))
