import json,copy
import pytest
from benchmarks.engineering_staged_data import targets
from benchmarks.engineering_replacement_data import from_case
from benchmarks.engineering_replacement_challenges import ReplacementEpisode,rebuild_all_control
from benchmarks.engineering_choice_episode import run,catalog_control
from benchmarks.engineering_catalog_challenges import generate
from benchmarks.engineering_scenarios import generate_scenarios


@pytest.fixture(scope='module')
def record():
    c=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair');c=generate(c,0)[0]
    return dict(case=c,expert=run(c,catalog_control,episode_factory=ReplacementEpisode),
                rebuild_all=run(c,rebuild_all_control,episode_factory=ReplacementEpisode))


def test_conditional_arguments_have_no_exact_teacher_directive(record):
    rows=targets(record);args=[r for r in rows if r['skill']=='conditional_arguments']
    assert args and all(r['source']=='expert' for r in args)
    for r in args:
        prompt=json.loads(r['messages'][-1]['content'])
        assert prompt['given_operation']=='set_line_catalog'
        assert not {'answer','directive','expected','arguments'}&set(prompt)
        assert 'argument_schema' in prompt and 'scene' in prompt and 'study' in prompt


def test_negative_controller_supplies_only_measurement_assessments(record):
    rows=targets(record);bad=[r for r in rows if r['source']=='rebuild_all']
    assert bad and all(r['skill']=='measurement' for r in bad)
    assert any(r['answer']['measurement_feasible'] is True and r['answer']['equipment_budget_ok'] is False for r in bad)


def test_changed_public_measurements_cannot_become_qualified_labels(record):
    r=copy.deepcopy(record);event=next(e for e in r['expert']['events'] if e['card']['has_fresh_measurements'])
    event['card']['study']['branches'][0]['loading_pct']=0.
    with pytest.raises(ValueError):targets(r)
