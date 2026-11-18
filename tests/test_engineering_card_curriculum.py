import pytest
from benchmarks.engineering_scenarios import generate_scenarios
from benchmarks.engineering_card_curriculum import CardEpisode,collect,binding_directive


@pytest.mark.parametrize('kind',['assemble','repair','diagnose'])
@pytest.mark.parametrize('variant',['clean','measure_first'])
def test_public_card_expert_completes_with_actual_raw_physics_grader(kind,variant):
    case=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']==kind)
    r=collect(case,variant)
    assert r['success'] and r['events'][-1]['response']['result']['success']
    assert all(t['source']=='public_expert' for t in r['events'])
    assert r['events'][-1]['action']['name']=='submit'
    assert not any('truth' in e['card'] for e in r['events'])
    if kind!='diagnose' and variant=='measure_first':
        assert r['events'][0]['card']['has_fresh_measurements']
        assert r['events'][0]['readiness']==dict(solution_ready=False)


def test_card_does_not_need_an_executed_inspection_and_does_not_spend_budget():
    case=generate_scenarios(420,1,(1,1,1))[0];e=CardEpisode(case)
    assert e.engine.state['calls']==0
    card,_=e.observe();assert e.engine.state['calls']==0 and card['profile']=='editor-card/v1'
    assert 'public_raw_alias_map' not in card and 'truth' not in card and card['study'] is None


def test_bad_alias_is_rejected_and_costs_a_real_call():
    e=CardEpisode(generate_scenarios(420,1,(1,1,1))[0]);r,raw=e.apply(dict(name='add_load',arguments=dict(load_id='D999')))
    assert not r['ok'] and raw is None and e.engine.state['calls']==1


def test_binding_is_a_conditional_task_with_an_explicit_operation_directive():
    assert binding_directive(dict(name='add_line',arguments=dict(route_id='R2',catalog_id='copper-1')))=='Connect required route R2 using catalog copper-1.'
    assert 'L3' in binding_directive(dict(name='set_line_catalog',arguments=dict(equipment_id='L3',catalog_id='copper-2')))
