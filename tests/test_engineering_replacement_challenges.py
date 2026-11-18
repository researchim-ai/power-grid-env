import json
from benchmarks.engineering_catalog_challenges import generate
from benchmarks.engineering_scenarios import generate_scenarios
from benchmarks.engineering_choice_episode import run,catalog_control
from benchmarks.engineering_replacement_challenges import ReplacementEpisode,rebuild_all_control


def sample():
    base=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair')
    return generate(base,0)[0]


def test_remove_add_all_bypasses_old_counter_but_fails_new_public_route_budget():
    c=sample();old=run(c,rebuild_all_control,catalog=True);new=run(c,rebuild_all_control,episode_factory=ReplacementEpisode)
    assert old['success'] and not new['success']
    assert new['terminal']['physics_success'] is True
    assert 'maximum_changed_routes' in new['terminal']['violations']
    assert all(e['action']['name']!='set_line_catalog' for e in new['events'])
    assert len(new['events'][-1]['card']['changed_routes'])>1
    assert new['events'][0]['card']['goal']['maximum_changed_routes']==1


def test_one_catalog_edit_still_completes_physical_study():
    r=run(sample(),catalog_control,episode_factory=ReplacementEpisode)
    assert r['success'] and len(r['events'][-1]['card']['changed_routes'])==1


def test_remove_and_rebuild_one_overloaded_route_counts_once_and_remains_allowed():
    target=[None]
    def policy(q):
        c=q['card'];safe=max(c['scene']['catalog'],key=lambda r:float(r['rating_a']))
        if target[0] is None:
            if not c['validated']:return catalog_control(q)
            if not c['has_fresh_measurements']:return catalog_control(q)
            line=max(c['study']['branches'],key=lambda b:b['loading_pct'])['id']
            route=next(r for r in c['scene']['routes'] if r['installed_line']==line);target[0]=route['id']
            action=dict(name='remove_line',arguments=dict(equipment_id=line))
        else:
            route=next(r for r in c['scene']['routes'] if r['id']==target[0])
            if route['installed_line']=='absent':action=dict(name='add_line',arguments=dict(route_id=route['id'],catalog_id=safe['id']))
            else:return catalog_control(q)
        return json.dumps(dict(choice=next(m['choice'] for m in q['choices'] if m['action']==action)))
    r=run(sample(),policy,episode_factory=ReplacementEpisode)
    assert r['success'] and r['events'][-1]['card']['changed_routes']==[target[0]]


def test_rejected_mutation_does_not_consume_route_change_budget():
    e=ReplacementEpisode(sample());reply,_=e.apply(dict(name='remove_line',arguments=dict(equipment_id='unknown')))
    assert not reply['ok'] and e.observe()[0]['changed_routes']==[]
