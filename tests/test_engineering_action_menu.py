from benchmarks.engineering_card_curriculum import CardEpisode
from benchmarks.engineering_catalog_challenges import generate
from benchmarks.engineering_scenarios import generate_scenarios
from benchmarks.engineering_action_menu import candidates,resolve


def test_menu_contains_gold_and_unsafe_actions_without_consulting_labels():
    case=generate_scenarios(420,1,(1,1,1))[0];e=CardEpisode(case);card,_=e.observe();menu=candidates(card);gold=e.teacher()
    assert any(m['action']==gold for m in menu)
    assert any(m['action']==dict(name='submit',arguments={}) for m in menu)
    assert all('equipment_id' not in m['action']['arguments'] for m in menu if m['action']['name']=='add_line')
    assert resolve(card,menu[0]['choice'])==menu[0]['action'] and resolve(card,'A9999') is None


def test_catalog_names_not_safety_determine_menu_order():
    base=generate_scenarios(420,1,(1,1,1))[0];a,_=generate(base,0);b,_=generate(base,1)
    ea=CardEpisode(a);eb=CardEpisode(b)
    ca,_=ea.observe();cb,_=eb.observe()
    assert candidates(ca)==candidates(cb)


def test_menu_enforces_required_arguments_and_known_entities():
    c=generate_scenarios(420,1,(1,1,1))[0];e=CardEpisode(c);card,_=e.observe()
    for m in candidates(card):
        a=m['action']
        if a['name']=='add_line':assert set(a['arguments'])=={'route_id','catalog_id'}
        if a['name']=='add_load':assert a['arguments']['load_id'].startswith('D')
