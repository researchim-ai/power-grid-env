from collections import Counter
from benchmarks.engineering_decision_tasks import DecisionDataset
from benchmarks.engineering_decision_pilot import schedule


def test_fixed_schedule_exposes_each_exemplar_exactly_four_times():
    indices=list(range(10));a=schedule(indices)
    assert a==schedule(indices) and len(a)==40
    assert Counter(a)=={i:4 for i in indices}
    assert all(set(a[i:i+10])==set(indices) for i in range(0,40,10))


def test_named_operation_selection_uses_same_v2_workflow_for_four_stages():
    d=DecisionDataset.__new__(DecisionDataset)
    ops=['validate','simulate','set_line_catalog','submit']
    d.rows=[dict(skill='operation',source_role='replacement',task_id='same',card={'kind':'repair'},answer={'operation':n}) for n in ops]
    d.rows += [dict(skill='operation',source_role='card',card={'kind':'assemble'},answer={'operation':n}) for n in ['add_line','add_load']]
    # Provide a factual/counterfactual pair and both missing-source views.
    q='Read L1';base=dict(skill='perception',kind='line_rating',view='equipment_zoom',question=q,subject='L1',scene_sha256='s')
    d.rows += [dict(base,mode='image',variant='factual',record_id='r1',answer={'value':'1.00'}),
               dict(base,mode='image',variant='counterfactual',record_id='r2',parent_record_id='full',answer={'value':'2.00'}),
               dict(base,mode='image',view='full_scene',variant='factual',record_id='full',answer={'value':'1.00'}),
               dict(base,mode='none',variant='factual',record_id='r1',answer={'value':None}),
               dict(base,mode='blank',variant='factual',record_id='r1',answer={'value':None})]
    indices=d.pilot_indices()
    assert len(indices)==10 and len(set(indices))==10
    assert [d.rows[i]['answer']['operation'] for i in indices[:6]]==ops+['add_line','add_load']
    assert [d.rows[i]['mode'] for i in indices[-4:]]==['image','image','none','blank']
