import json
from benchmarks.engineering_action_quality import quality
from benchmarks.engineering_learning_gate import rollout,strict_contract
from benchmarks.engineering_scenarios import generate_scenarios


def test_valid_wire_with_missing_required_arguments_is_not_schema_success():
    c=generate_scenarios(420,1,(1,1,1))[0]
    r=rollout(c,lambda m:json.dumps(dict(name='add_line',arguments={})),strict_contract().model_copy(update={'maximum_actions':1}))
    q=quality(c,r)['counts']
    assert q['wire_valid_calls']==q['schema_invalid_calls']==q['tool_errors']==q['empty_required_calls']==1
    assert q['required_slots']==3 and q['present_required_slots']==0 and q['nonempty_required_slots']==0


def test_present_but_empty_argument_is_not_filled_or_valid():
    c=generate_scenarios(420,1,(1,1,1))[0]
    r=rollout(c,lambda m:json.dumps(dict(name='inspect_equipment',arguments=dict(equipment_id=''))),strict_contract().model_copy(update={'maximum_actions':1}))
    q=quality(c,r)['counts'];assert q['required_slots']==q['present_required_slots']==q['schema_invalid_calls']==1
    assert q['nonempty_required_slots']==0


def test_schema_valid_submit_can_still_fail_physical_grader():
    c=generate_scenarios(420,1,(1,1,1))[0]
    r=rollout(c,lambda m:json.dumps(dict(name='submit',arguments={})),strict_contract())
    q=quality(c,r);assert not q['success'] and q['counts']['schema_valid_calls']==q['counts']['submits']==1
    assert q['terminal']['success'] is False


def test_wire_and_transport_errors_are_distinct():
    c=generate_scenarios(420,1,(1,1,1))[0]
    r=rollout(c,lambda m:'bad',strict_contract().model_copy(update={'maximum_actions':1}));assert quality(c,r)['counts']['wire_errors']==1
    def fail(messages):raise RuntimeError('failed')
    r=rollout(c,fail,strict_contract());assert quality(c,r)['counts']['transport_failures']==1
