import copy,json
from benchmarks.engineering_operation_probe import messages
from benchmarks.engineering_card_curriculum import CardEpisode
from benchmarks.engineering_scenarios import generate_scenarios


def test_operation_probe_transmits_public_state_and_names_without_menu_or_gold():
    c=generate_scenarios(420,1,(1,1,1))[0];card,_=CardEpisode(c).observe();before=copy.deepcopy(card)
    m=messages(card);public=json.loads(m[1]['content'])
    assert 'tools' not in public and 'choices' not in public and 'answer' not in public
    assert public['allowed_operations']==sorted(t['function']['name'] for t in card['tools'])
    assert public['scene']==card['scene'] and public['study']==card['study'] and card==before


def test_operation_probe_does_not_instruct_a_particular_next_operation():
    c=generate_scenarios(420,1,(1,1,1))[0];card,_=CardEpisode(c).observe();m=messages(card)
    assert 'set_line_catalog' not in m[0]['content']
    assert '{"operation":"one allowed name"}' in m[0]['content']
