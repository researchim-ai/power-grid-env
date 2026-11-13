import copy
from pathlib import Path
import pytest
from power_grid_env.study_artifacts import read_json
from power_grid_env.protection_envelope import effective_plans
from benchmarks.protection_family_revision import revision
ROOT=Path(__file__).resolve().parents[1]/'cases/full-inverse-families'

@pytest.mark.parametrize('method',['radau','network_dae'])
def test_only_backup_TMS_and_label_changed(method):
    a=read_json(ROOT/f'extremely_inverse-original-{method}.json');b=read_json(ROOT/f'extremely_inverse-coordinated-{method}.json')
    r=revision(a,b)
    assert r['requirements_unchanged'] and r['original_backup_TMS']==.08 and r['candidate_backup_TMS']==.06
    originals={n:p for n,p,_ in effective_plans(a)};candidates={n:p for n,p,_ in effective_plans(b)}
    assert len(originals)==len(candidates)==17
    assert originals['corner-0011'].backup.time_multiplier==pytest.approx(.088)
    assert candidates['corner-0011'].backup.time_multiplier==pytest.approx(.066)
    for n in originals:
        assert originals[n].equipment==candidates[n].equipment
        assert originals[n].fault_clear_s==candidates[n].fault_clear_s==.8
        assert originals[n].fault_motors==candidates[n].fault_motors

@pytest.mark.parametrize('change',['speed','fault_window','pickup','time_primary','tolerance','equipment','source','clear_budget','slower'])
def test_requirement_or_unrelated_setting_change_rejected(change):
    a=read_json(ROOT/'extremely_inverse-original-radau.json');b=read_json(ROOT/'extremely_inverse-coordinated-radau.json')
    if change=='speed':b['base']['equipment']['mandatory_cases'][0]['motors'][0]['minimum_final_speed_pu']=.8
    if change=='fault_window':b['base']['fault_clear_s']=.9
    if change=='pickup':b['base']['primary']['pickup_a']=950
    if change=='time_primary':b['base']['primary']['time_multiplier']=.02
    if change=='tolerance':b['base']['backup']['time_tolerance']=.2
    if change=='equipment':b['base']['equipment']['sizing']['demand_kva']=101
    if change=='source':b['base']['backup']['source']['conditions']='unrelated changed provenance'
    if change=='clear_budget':b['base']['equipment']['sizing']['installation']['clearing_s']=.2
    if change=='slower':b['base']['backup']['time_multiplier']=.09
    with pytest.raises(ValueError):revision(a,b)

@pytest.mark.parametrize('family',['very_inverse','extremely_inverse-coordinated'])
@pytest.mark.parametrize('method',['radau','network_dae'])
def test_complete_long_criteria_at_all_corners(family,method):
    e=read_json(ROOT/f'{family}-{method}.json')
    for _,p,_ in effective_plans(e):
        assert [c.study.induction.duration_s for c in p.equipment.mandatory_cases]==[4,4,4]
        assert all(c.motors[0].minimum_final_speed_pu==.9 for c in p.equipment.mandatory_cases)
        assert p.integration_method==method and p.fault_clear_s==.8

def test_same_engineering_requirements_across_all_families():
    from benchmarks.three_family_envelope_acceptance import requirements
    si=read_json(ROOT.parent/'full-protection-envelope/radau.json')
    vi=read_json(ROOT/'very_inverse-radau.json');ei=read_json(ROOT/'extremely_inverse-coordinated-radau.json')
    assert requirements(si)==requirements(vi)==requirements(ei)
    vi['base']['equipment']['mandatory_cases'][0]['motors'][0]['minimum_final_speed_pu']=.8
    assert requirements(si)!=requirements(vi)

@pytest.mark.parametrize('change',['duplicate','missing','extra','unknown'])
def test_acceptance_family_coverage_contract(change):
    from benchmarks.three_family_envelope_acceptance import Sources
    s=read_json(ROOT/'acceptance-layout.json')
    if change=='duplicate':s['families'][2]=copy.deepcopy(s['families'][0])
    if change=='missing':s['families'].pop()
    if change=='extra':s['families'].append(copy.deepcopy(s['families'][0]))
    if change=='unknown':s['families'][0]['family']='invented'
    with pytest.raises(ValueError):Sources.model_validate(s)
