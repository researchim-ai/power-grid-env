import copy
import json
import pytest
from benchmarks.engineering_scenarios import ScenarioEngine,generate_scenarios
from benchmarks.engineering_factory import collect_scenario
from benchmarks.engineering_recovery import episode_messages
from benchmarks.engineering_grounding import scene_from_messages,render_scene,focus_line,question_records,counterfactual,resolve_action
from benchmarks.engineering_grounding_runtime import messages_for,score


def sample(kind='repair'):
    case=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']==kind)
    episode=collect_scenario(case,'clean');messages,event=next((m,e) for m,e in episode_messages(episode) if e['turn']==1)
    return case,messages


def test_public_scene_contains_all_routes_and_required_loads_without_private_truth():
    case,m=sample();scene,aliases=scene_from_messages(m)
    assert len(scene['routes'])==len(case['requirements']['routes']) and len(scene['loads'])==len(case['requirements']['loads'])
    assert 'truth' not in scene and 'cause' not in scene and 'voltage_pu' not in json.dumps(scene)
    case['truth']={'cause':'changed_private_value'};assert scene_from_messages(m)[0]==scene
    assert render_scene(scene)==render_scene(copy.deepcopy(scene))
    assert all(r['installed_line']=='absent' or r['installed_line'] in {l['id'] for l in scene['lines']} for r in scene['routes'])


def test_assembly_scene_displays_missing_loads_and_required_routes():
    case,m=sample('assemble');scene,aliases=scene_from_messages(m)
    assert not scene['lines'] and all(r['installed_line']=='absent' for r in scene['routes'])
    assert all(l['installed_node']=='absent' for l in scene['loads'])
    assert len(list(question_records(scene)))==len(scene['routes'])+len(scene['loads'])


def test_stale_images_are_rejected_after_successful_edit():
    case,m=sample();changed=copy.deepcopy(m)
    changed.extend([dict(role='assistant',content=json.dumps(dict(name='set_line_catalog',arguments={}))),dict(role='user',content=json.dumps(dict(ok=True,result=dict(updated=True),observation=dict(revision=1))))])
    with pytest.raises(ValueError):scene_from_messages(changed)
    with pytest.raises(ValueError):scene_from_messages(m[:2])


def test_counterfactual_changes_only_visible_rating_and_preserves_question():
    _,m=sample();scene,_=scene_from_messages(m);line=scene['lines'][0]['id'];other=counterfactual(scene,line)
    assert scene['lines'][0]['rating_a']!=other['lines'][0]['rating_a']
    reset=copy.deepcopy(other);reset['lines'][0]['rating_a']=scene['lines'][0]['rating_a'];assert reset==scene
    q=next(q for q in question_records(scene) if q['kind']=='line_rating' and q['subject']==line)
    r=next(q for q in question_records(other) if q['kind']=='line_rating' and q['subject']==line)
    assert q['question']==r['question'] and q['answer']!=r['answer']
    assert focus_line(scene,line)!=focus_line(other,line) and render_scene(scene)!=render_scene(other)


def test_handle_resolver_maps_public_entities_and_preserves_required_semantics():
    case,m=sample('assemble');scene,aliases=scene_from_messages(m)
    action=resolve_action(scene,aliases,dict(name='add_line',arguments=dict(route_id='R1',catalog_id='copper-1')),0)
    assert action['arguments']['route_id'] in aliases['routes'] and action['arguments']['equipment_id'].startswith('ln_')
    engine=ScenarioEngine(case);assert engine.apply(action['name'],action['arguments'])['ok']
    with pytest.raises(ValueError):resolve_action(scene,aliases,dict(name='add_load',arguments=dict(load_id='N1')),0)
    with pytest.raises(ValueError):resolve_action(scene,aliases,dict(name='add_line',arguments=dict(route_id='R1',catalog_id='copper-1')),1)
    with pytest.raises(ValueError):resolve_action(scene,aliases,dict(name='inspect_equipment',arguments=dict(equipment_id='secret')),0)


def test_image_only_prompt_has_no_answer_or_scene_table_and_missing_control_has_no_image(tmp_path):
    from PIL import Image
    p=tmp_path/'p.png';Image.new('RGB',(10,10),'white').save(p)
    row=dict(question='Read L1 current',answer=dict(value='182.45'),_scene=dict(lines=[dict(id='L1',rating_a='182.45')]))
    m=messages_for(row,p,'image');text=' '.join(c.get('text','') for c in m[1]['content'])
    assert '182.45' not in text and 'PUBLIC SCENE' not in text
    assert any(c['type']=='image' for c in m[1]['content'])
    assert all(c['type']=='text' for c in messages_for(row,p,'none')[1]['content'])
    assert '182.45' in messages_for(row,p,'text')[1]['content'][-1]['text']


@pytest.mark.parametrize('raw,expected',[('{"value":"1.0"}',True),('{"value":1}',True),('{"value":true}',False),('```json\n{"value":"1.00"}\n```',False),('{"value":"2.00"}',False)])
def test_grounding_semantics_distinguishes_decimal_representation_from_wrong_value(raw,expected):
    assert score(raw,dict(value='1.00'))['semantic']==expected
