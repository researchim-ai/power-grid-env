import json
from benchmarks.engineering_staged_agent import run,catalog_control,decide,operation_request,argument_request
from benchmarks.engineering_scenarios import generate_scenarios
from benchmarks.engineering_catalog_challenges import generate
from benchmarks.engineering_replacement_challenges import ReplacementEpisode


def sample():
    c=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair');return generate(c,0)[0]


def test_public_two_stage_control_completes_real_v2_workflow():
    r=run(sample(),catalog_control)
    assert r['success'] and r['protocol_errors']==0 and r['candidate_completions']==r['calls']+1
    assert r['events'][-1]['card']['changed_routes']


def test_argument_request_has_schema_and_measurements_but_no_teacher_directive():
    c,_=ReplacementEpisode(sample()).observe();q=argument_request(c,'set_line_catalog')
    data=json.loads(q['messages'][-1]['content'])
    assert data['given_operation']=='set_line_catalog'
    assert 'directive' not in data and 'answer' not in data and 'arguments' not in data
    assert 'equipment_id' in data['argument_schema']['required']
    assert data['scene']==c['scene'] and data['study']==c['study']


def test_malformed_operation_consumes_real_budget_without_teacher_recovery():
    r=run(sample(),lambda q:'```json\n{"operation":"validate"}\n```')
    assert not r['success'] and r['calls']==24 and r['protocol_errors']==24
    assert all(e['decision']['action'] is None for e in r['events'])


def test_parameter_arguments_come_from_candidate_not_reference():
    c,_=ReplacementEpisode(sample()).observe();seen=[]
    def policy(q):
        seen.append(q['stage'])
        return '{"operation":"set_line_catalog"}' if q['stage']=='operation' else '{"equipment_id":"L999","catalog_id":"wrong"}'
    d=decide(c,policy)
    assert d['action']['arguments']=={'equipment_id':'L999','catalog_id':'wrong'} and seen==['operation','arguments']
    assert d['error'] is None # schema/physical grader,not teacher,will reject action


def test_previous_failure_is_public_on_next_attempt_and_goal_is_immutable():
    c=sample();n=[0];feedback=[]
    def policy(q):
        data=json.loads(q['messages'][-1]['content']);feedback.append(data.get('previous_feedback'))
        n[0]+=1;q['messages'][-1]['content']='corrupted copy'
        return '{"operation":"unknown"}'
    r=run(c,policy)
    assert n[0]==24 and feedback[0] is None and feedback[1]['ok'] is False
    assert all(e['card']['goal']['maximum_changed_routes']==1 for e in r['events'])


def test_empty_argument_schema_uses_one_model_completion_and_early_submit_fails():
    calls=[]
    def submit(q):calls.append(q['stage']);return '{"operation":"submit"}'
    r=run(sample(),submit)
    assert calls==['operation'] and not r['success'] and r['calls']==1
