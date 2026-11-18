import copy,json
from benchmarks.engineering_counterfactual_cases import pair
from benchmarks.engineering_catalog_challenges import generate
from benchmarks.engineering_scenarios import generate_scenarios


def test_physical_pair_has_equal_stage_counters_goals_feedback_but_opposite_feasibility():
    c=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair');c=generate(c,0)[0];before=copy.deepcopy(c)
    p,rows=pair(c)
    assert c==before and len(rows)==4 and all(x['success'] for x in p['controls'])
    a,b=[s['card'] for s in p['snapshots']]
    assert {k:v for k,v in a.items() if k not in ('scene','study')}=={k:v for k,v in b.items() if k not in ('scene','study')}
    assert a['changed_routes']==b['changed_routes']==[]
    assert a['has_fresh_measurements'] and b['has_fresh_measurements']
    ops=[r for r in rows if r['skill']=='operation']
    assert [r['answer'] for r in ops]==[{'operation':'set_line_catalog'},{'operation':'submit'}]
    assert json.loads(ops[0]['messages'][-1]['content'])['previous_feedback']==json.loads(ops[1]['messages'][-1]['content'])['previous_feedback']


def test_counterfactual_labels_are_outside_messages_and_keep_base_family_lineage():
    c=next(c for c in generate_scenarios(420,1,(1,1,1)) if c['kind']=='repair');c=generate(c,1)[0]
    _,rows=pair(c)
    assert {r['base_project_id'] for r in rows}=={c['base_project_id']}
    assert {r['family_sha256'] for r in rows}=={c['family_sha256']}
    for r in rows:
        prompt=json.loads(r['messages'][-1]['content'])
        assert not {'answer','variant','task_id','expected'}&set(prompt)
    assessments=[r['answer'] for r in rows if r['skill']=='measurement']
    assert [r['measurement_feasible'] for r in assessments]==[False,True]
    assert all(r['equipment_budget_ok'] for r in assessments)
