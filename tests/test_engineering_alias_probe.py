import json,copy
from benchmarks.engineering_alias_probe import renamed,transform


def source():
    card=dict(scene=dict(nodes=['N1','N2','N3'],lines=[dict(id='L1'),dict(id='L2'),dict(id='L3')],routes=[dict(id='R1'),dict(id='R2')],loads=[dict(id='D1'),dict(id='D2')]),
              study={'branches':[{'id':'L1','loading_pct':125.0}]},goal={'maximum_branch_loading_pct':100},given_operation='set_line_catalog')
    return [dict(role='system',content='public schema'),dict(role='user',content=json.dumps(card))],dict(equipment_id='L1',catalog_id='copper-2')


def test_alias_probe_changes_prediction_but_preserves_values_topology_and_goal():
    m,a=source();before=copy.deepcopy(m);changed,expected,mapping=renamed(m,a)
    assert m==before and expected!=a and expected['catalog_id']==a['catalog_id']
    p=json.loads(changed[-1]['content']);original=json.loads(m[-1]['content']);inverse={v:k for k,v in mapping.items()}
    assert transform(p,inverse)==original and transform(expected,inverse)==a
    assert p['study']['branches'][0]['loading_pct']==125.0 and p['goal']==original['goal']
    assert p['study']['branches'][0]['id']==expected['equipment_id']


def test_alias_mapping_is_deterministic_bijective_and_labels_stay_outside_prompt():
    m,a=source();x,y,mapping=renamed(m,a);xx,yy,mm=renamed(m,a)
    assert (x,y,mapping)==(xx,yy,mm) and len(set(mapping.values()))==len(mapping)
    public=json.loads(x[-1]['content'])
    assert not {'answer','expected','directive'}&set(public)
    assert set(mapping)==set(mapping.values())
