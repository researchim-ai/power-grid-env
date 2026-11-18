import pytest
from benchmarks.engineering_scenarios import generate_scenarios
from benchmarks.engineering_catalog_challenges import generate,run
from benchmarks.engineering_card_curriculum import collect


def base():return generate_scenarios(420,1,(1,1,1))[0]


def test_catalog_membership_alone_does_not_prove_feasibility():
    case,evidence=generate(base(),0)
    old=collect(case)
    assert not old['success']
    new=run(case);assert new['success'] and new['edits']==1
    assert new['terminal']['physics_success'] is True


def test_upgrade_every_line_is_physically_feasible_but_violates_public_edit_budget():
    case,_=generate(base(),0);r=run(case,'upgrade_all')
    assert not r['success'] and r['terminal']['physics_success'] is True
    assert 'maximum_catalog_edits' in r['terminal']['violations']
    assert r['events'][0]['card']['goal']['maximum_catalog_edits']==1


def test_same_physical_network_catalog_name_swap_requires_other_action():
    a,ae=generate(base(),0);b,be=generate(base(),1)
    assert a['id']!=b['id'] and a['base_project_id']==b['base_project_id']
    assert a['project']==b['project'] and ae['safe_catalog']!=be['safe_catalog']
    assert list(a['requirements']['catalog'])==list(b['requirements']['catalog'])==['copper-1','copper-2']
    assert run(a)['success'] and run(b)['success']
