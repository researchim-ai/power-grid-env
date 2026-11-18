import copy,math
import pytest
from benchmarks.engineering_constraint_tasks import assess


def card():
    return dict(has_fresh_measurements=True,study=dict(converged=True,nodes=[dict(energized=True,voltage_pu_min=.9)],branches=[dict(loading_pct=100.)]),
                goal=dict(minimum_voltage_pu=.9,maximum_branch_loading_pct=100.,maximum_changed_routes=1,maximum_catalog_edits=1),changed_routes=['R1'],catalog_edits_used=1)


def test_exact_limits_are_feasible_but_one_ulp_violation_is_not():
    c=card();r=assess(c)
    assert r['measurement_feasible'] is True and r['overloaded_branch_count']==0 and r['undervoltage_node_count']==0
    c['study']['nodes'][0]['voltage_pu_min']=math.nextafter(.9,0.)
    c['study']['branches'][0]['loading_pct']=math.nextafter(100.,math.inf)
    r=assess(c);assert r['measurement_feasible'] is False and r['overloaded_branch_count']==r['undervoltage_node_count']==1


def test_missing_or_unconverged_study_is_unknown_not_a_feasible_measurement_label():
    c=card();c.update(has_fresh_measurements=False,study=None);r=assess(c)
    assert not r['study_ready'] and r['measurement_feasible'] is None and r['overloaded_branch_count'] is None
    c=card();c['study']['converged']=False;assert assess(c)['measurement_feasible'] is None


def test_physical_feasibility_does_not_hide_violated_equipment_budget():
    c=card();c['changed_routes']=['R1','R2'];r=assess(c)
    assert r['measurement_feasible'] is True and r['equipment_budget_ok'] is False
    assert 'solution_ready' not in r # no claim about complete passports/structure


def test_deenergized_nodes_are_counted_separately_from_energized_undervoltage():
    c=card();c['study']['nodes'][0].update(energized=False,voltage_pu_min=0.)
    r=assess(c);assert r['deenergized_node_count']==1 and r['undervoltage_node_count']==0 and not r['measurement_feasible']


@pytest.mark.parametrize('mutation',['nan','stale','missing_fresh','duplicate_routes','missing_counter','boolean_number'])
def test_inconsistent_public_evidence_is_rejected_not_silently_labeled(mutation):
    c=card()
    if mutation=='nan':c['study']['branches'][0]['loading_pct']=float('nan')
    if mutation=='stale':c['has_fresh_measurements']=False
    if mutation=='missing_fresh':c['study']=None
    if mutation=='duplicate_routes':c['changed_routes']=['R1','R1']
    if mutation=='missing_counter':c.pop('catalog_edits_used')
    if mutation=='boolean_number':c['study']['nodes'][0]['voltage_pu_min']=True
    with pytest.raises((ValueError,KeyError)):assess(c)
