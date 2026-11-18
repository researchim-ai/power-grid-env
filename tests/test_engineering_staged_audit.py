import copy,json
import pytest
from benchmarks.engineering_staged_audit import replay
from benchmarks.engineering_staged_agent import run,catalog_control
from benchmarks.engineering_catalog_challenges import generate
from benchmarks.engineering_scenarios import generate_scenarios


@pytest.fixture(scope='module')
def source():
    c=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair');c=generate(c,0)[0]
    return c,run(c,catalog_control)


def test_full_staged_control_replays_raw_outputs_and_independent_measurements(source):
    c,r=source;summary=replay(c,r,require_native=False)
    assert summary['success'] and summary['accepted_edits']==1 and summary['filled_required_slots']==2
    assert summary['numerical_studies_checked']==2


@pytest.mark.parametrize('field',['completion','action','goal','terminal','feedback'])
def test_forged_trace_cannot_promote_decision_or_terminal(source,field):
    c,r=source;r=copy.deepcopy(r)
    if field=='completion':r['events'][0]['decision']['completions'][0]='{"operation":"submit"}'
    if field=='action':r['events'][0]['decision']['action']['name']='submit'
    if field=='goal':r['events'][0]['card']['goal']['maximum_changed_routes']=999
    if field=='terminal':r['terminal']['success']=False
    if field=='feedback':r['events'][1]['previous_feedback']['ok']=False
    with pytest.raises(ValueError):replay(c,r,require_native=False)


def test_invalid_output_failure_and_early_submit_remain_failures():
    c=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair');c=generate(c,0)[0]
    for policy in (lambda q:'bad json',lambda q:'{"operation":"submit"}'):
        r=run(c,policy);summary=replay(c,r,require_native=False)
        assert not summary['success']


def test_missing_or_fabricated_native_counters_are_rejected(source):
    c,r=source
    with pytest.raises(ValueError):replay(c,r)
    r=copy.deepcopy(r);r['native_calls']=[dict(stage='operation',prompt_tokens=100,generated_tokens=129)]*r['candidate_completions']
    with pytest.raises(ValueError):replay(c,r)


def test_native_generation_failure_has_no_invented_completion_or_token_record():
    c=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair');c=generate(c,0)[0]
    def failed(q):raise ValueError('native generation failed')
    r=run(c,failed);r['native_calls']=[]
    summary=replay(c,r)
    assert not summary['success'] and summary['protocol_errors']==24 and r['candidate_completions']==0
