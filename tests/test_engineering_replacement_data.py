import copy
import pytest
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


def test_failed_controller_contributes_only_negative_readiness_labels(record):
    rows=from_case(record);negative=[r for r in rows if r['source']=='rebuild_all']
    assert negative and all(r['task']=='readiness' and r['answer']=={'solution_ready':False} for r in negative)
    assert all(r['card']['has_fresh_measurements'] and len(r['card']['changed_routes'])>1 for r in negative)
    assert any(r['task']=='choice' and r['source']=='expert' for r in rows)


@pytest.mark.parametrize('field',['card','terminal'])
def test_replay_rejects_modified_source_evidence(record,field):
    r=copy.deepcopy(record)
    if field=='card':r['expert']['events'][0]['card']['changed_routes']=['R1','R2']
    else:r['rebuild_all']['terminal']['success']=True
    with pytest.raises(ValueError):from_case(r)
