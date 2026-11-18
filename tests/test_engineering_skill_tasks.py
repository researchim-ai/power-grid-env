import copy
import json
import pytest
from benchmarks.engineering_scenarios import generate_scenarios
from benchmarks.engineering_card_curriculum import CardEpisode
from benchmarks.engineering_catalog_challenges import CatalogEpisode, generate
from benchmarks.engineering_skill_tasks import shuffled_menu, card_messages, score, SkillDataset


def sample():
    return next(c for c in generate_scenarios(420, 1, (1, 1, 1)) if c['kind'] == 'repair')


def test_permutation_changes_ids_but_not_allowed_physical_decisions():
    card, _ = CardEpisode(sample()).observe()
    a, b = shuffled_menu(card, 420), shuffled_menu(card, 777)
    assert a != b
    normalize = lambda m: sorted(json.dumps(x['action'], sort_keys=True) for x in m)
    assert normalize(a) == normalize(b)
    assert [r['choice'] for r in a] == ['A'+str(i) for i in range(len(a))]
    assert any(m['action']['name'] == 'submit' for m in a)


def test_shuffling_is_reproducible_and_does_not_mutate_public_card():
    card, _ = CardEpisode(sample()).observe(); before = copy.deepcopy(card)
    assert shuffled_menu(card, 420) == shuffled_menu(card, 420)
    assert card == before


def test_catalog_pair_requires_different_choices_in_identical_menu_order():
    x, _ = generate(sample(), 0); y, _ = generate(sample(), 1)
    ea, eb = CatalogEpisode(x), CatalogEpisode(y)
    for e in (ea, eb):
        e.apply(dict(name='validate', arguments={})); e.apply(dict(name='simulate', arguments={}))
    ca, _ = ea.observe(); cb, _ = eb.observe()
    assert shuffled_menu(ca, 420) == shuffled_menu(cb, 420)
    ma, aa = card_messages(dict(skill='choice', card=ca, action=ea.teacher()), 420)
    mb, ab = card_messages(dict(skill='choice', card=cb, action=eb.teacher()), 420)
    assert aa != ab and ma != mb
    assert set(aa) == {'choice'}
    assert 'action' not in json.loads(ma[-1]['content'])


def test_changing_supervised_choice_does_not_change_candidate_prompt():
    e = CardEpisode(sample()); card, _ = e.observe(); a = e.teacher()
    row = dict(skill='choice', card=card, action=a)
    messages, _ = card_messages(row, 420)
    other = next(m['action'] for m in shuffled_menu(card, 420) if m['action'] != a)
    assert card_messages(dict(row, action=other), 420)[0] == messages


@pytest.mark.parametrize('raw,expected,valid',[
    ('{"solution_ready":true}', {'solution_ready':True}, True),
    ('{"solution_ready":1}', {'solution_ready':True}, False),
    ('{"choice":"A2"}', {'choice':'A1'}, False),
    ('{"choice":"A1","extra":0}', {'choice':'A1'}, False),
    ('{"choice":"A1","choice":"A2"}', {'choice':'A2'}, False),
    ('```json\n{"choice":"A1"}\n```', {'choice':'A1'}, False),
    ('{"value":null}', {'value':None}, True),
])
def test_strict_score_preserves_types_keys_and_format(raw, expected, valid):
    assert score(raw, expected)['exact'] is valid


def test_exact_source_duplicates_are_counted_not_oversampled():
    d = SkillDataset.__new__(SkillDataset); d.rows=[]; d._row_ids=set(); d.duplicate_rows=0
    row = dict(split='train', skill='binding', answer={'route_id':'R1'})
    d._append(copy.deepcopy(row)); d._append(copy.deepcopy(row))
    assert len(d.rows)==1 and d.duplicate_rows==1
    with pytest.raises(ValueError):d._append(dict(row, split='test'))
