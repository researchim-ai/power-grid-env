import copy,json
import pytest
from benchmarks.engineering_scenarios import generate_scenarios
from benchmarks.engineering_catalog_challenges import generate
from benchmarks.engineering_choice_episode import run,request,parse,catalog_control
from benchmarks.engineering_card_curriculum import CardEpisode


def case():
    return next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair')


@pytest.mark.parametrize('variant',[0,1])
def test_real_physics_control_passes_both_catalog_names_and_multiple_menu_seeds(variant):
    c,_=generate(case(),variant)
    for seed in (420,777):
        r=run(c,catalog_control,seed,catalog=True)
        assert r['success'] and r['invalid_decisions']==0 and r['calls']<24
        edits=[e for e in r['events'] if e['action']['name']=='set_line_catalog']
        assert len(edits)==1


def test_invalid_choices_consume_real_call_budget_and_never_receive_teacher_actions():
    r=run(case(),lambda q:'{"choice":"A9999"}')
    assert not r['success'] and r['invalid_decisions']==24 and r['calls']==24
    assert r['terminal']['violations']==['call_budget']
    assert all(e['action'] is None for e in r['events'])


def test_early_submit_is_not_counted_as_success():
    def submit(q):return json.dumps({'choice':next(m['choice'] for m in q['choices'] if m['action']==dict(name='submit',arguments={}))})
    c,_=generate(case(),0);r=run(c,submit,catalog=True)
    assert not r['success'] and r['calls']==1 and r['invalid_decisions']==0


def test_candidate_mutation_cannot_change_owner_goal_or_menu():
    c,_=generate(case(),0);before=copy.deepcopy(c)
    def corrupt(q):
        q['card']['goal']['maximum_catalog_edits']=999
        q['choices'][0]['action']={'name':'submit','arguments':{}}
        return '{"choice":"A9999"}'
    r=run(c,corrupt,catalog=True)
    assert c==before and not r['success']
    assert all(e['card']['goal']['maximum_catalog_edits']==1 for e in r['events'])


def test_candidate_exception_is_retained_in_denominator():
    def crash(q):raise RuntimeError('not a successful action')
    r=run(case(),crash)
    assert not r['success'] and r['invalid_decisions']==24
    assert all(e['error']=='candidate_error:RuntimeError' for e in r['events'])


def test_public_request_excludes_reference_and_supervised_answer():
    e=CardEpisode(case());card,_=e.observe();q=request(card,420)
    assert set(q)=={'profile','card','choices','messages'}
    assert 'truth' not in json.dumps(q) and 'answer' not in q
    with pytest.raises(ValueError):parse('{"choice":"A0","extra":0}',q['choices'])
    with pytest.raises(ValueError):parse('```json\n{"choice":"A0"}\n```',q['choices'])
