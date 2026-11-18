import copy
import hashlib
import json
import pytest
from benchmarks.engineering_visual_data import public_snapshot,render
from benchmarks.engineering_scenarios import generate_scenarios
from benchmarks.engineering_factory import collect_scenario
from benchmarks.engineering_recovery import episode_messages


def test_render_has_only_the_inspected_public_project():
    scenario=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair')
    episode=collect_scenario(scenario,'simulate_first');messages,event=list(episode_messages(episode))[2]
    snapshot=public_snapshot(messages)
    assert set(snapshot)=={'project','requirements','scope'} and 'truth' not in json.dumps(snapshot)
    before=copy.deepcopy(snapshot);a=render(snapshot);b=render(snapshot)
    assert a==b and a.startswith(b'\x89PNG') and snapshot==before
    altered=copy.deepcopy(snapshot);altered['project']['lines'][0]['r_ohm_km']+=1
    assert hashlib.sha256(render(altered)).digest()!=hashlib.sha256(a).digest()
    with pytest.raises(ValueError):public_snapshot(messages[:2])


def test_line_passports_are_visibly_bound_to_their_terminals():
    scenario=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair')
    episode=collect_scenario(scenario,'clean');messages,_=list(episode_messages(episode))[1]
    scene=public_snapshot(messages);other=copy.deepcopy(scene)
    line=other['project']['lines'][0];line['from_node'],line['to_node']=line['to_node'],line['from_node']
    assert render(scene)!=render(other)
