from pathlib import Path
import copy
import subprocess
import sys

import pytest

from power_grid_env.stator_resistance_anchor import AnchoredDynamicIdentification,StatorResistanceMeasurement,resistance_interval,anchored_audit,run_anchored_identification
from power_grid_env.study_artifacts import read_json,encoded,verify,export_zip,extract_zip

ROOT=Path(__file__).resolve().parents[1]


def request():return read_json(ROOT/'cases/anchored-identification/locked-dc-anchor.json')


def test_deterministic_interval_is_intersection_not_statistical_confidence():
    p=request()['stator_resistance_measurement'];r=resistance_interval(p)
    expected_low=(5.55-.00001)/(1.5+.000001)
    expected_high=(5.55+.00001)/(1.5-.000001)
    assert r['lower_ohm']==pytest.approx(expected_low,abs=1e-14)
    assert r['upper_ohm']==pytest.approx(expected_high,abs=1e-14)
    assert r['lower_ohm']<3.7<r['upper_ohm']
    assert not r['statistical_confidence_claimed'] and not r['calibration_authenticated']


def test_wye_line_to_line_is_two_phase_resistances():
    p=request()['stator_resistance_measurement'];p['connection']='wye_line_to_line'
    for point in p['points']:point['voltage_v']*=2
    r=resistance_interval(p)
    assert r['connection_factor']==2 and r['nominal_ohm']==pytest.approx(3.7,abs=1e-10)


@pytest.mark.parametrize('defect',['unresolved_voltage','unresolved_current','duplicate_levels','inconsistent','too_wide','temperature','unknown_connection','leads','not_settled','blank_origin','measured_synthetic_reference','overflow'])
def test_unsupported_or_unresolved_dc_data_fail_before_work(defect):
    p=request()['stator_resistance_measurement']
    if defect=='unresolved_voltage':p['points'][0]['voltage_absolute_error_v']=2.
    elif defect=='unresolved_current':p['points'][0]['current_absolute_error_a']=1.
    elif defect=='duplicate_levels':p['points']=[p['points'][0]]*2
    elif defect=='inconsistent':p['points'][0]['voltage_v']+=.1
    elif defect=='too_wide':
        for point in p['points']:point['voltage_absolute_error_v']=.01;point['current_absolute_error_a']=.001
    elif defect=='temperature':p['trace_temperature_c']=21.
    elif defect=='unknown_connection':p['connection']='delta_unknown'
    elif defect=='leads':p['voltage_is_winding_only']=False
    elif defect=='not_settled':p['settled_dc_confirmed']=False
    elif defect=='blank_origin':p['provenance']=' '
    elif defect=='measured_synthetic_reference':p['origin']='measured'
    else:
        for point,current in zip(p['points'],[5e-324,1e-323,1.5e-323]):point.update(voltage_v=1e6,current_a=current,voltage_absolute_error_v=0,current_absolute_error_a=0)
    with pytest.raises(ValueError):resistance_interval(p)


def test_anchor_copy_preserves_every_observed_channel_and_holdout():
    p=AnchoredDynamicIdentification.model_validate(request());before=encoded(p.model_dump(mode='json'))
    after=anchored_audit(p,3.7)
    for split in ('fitting','validation'):
        assert encoded([t.model_dump(mode='json') for t in getattr(p.audit.identification,split)])==encoded([t.model_dump(mode='json') for t in getattr(after.identification,split)])
    assert 'stator_r_ohm' not in after.identification.electrical_parameters
    assert p.audit.identification.initial.stator_r_ohm!=after.identification.initial.stator_r_ohm
    assert encoded(p.model_dump(mode='json'))==before


def test_missing_anchor_does_not_fall_back_to_true_or_seed_resistance(tmp_path):
    p=request();del p['stator_resistance_measurement']
    with pytest.raises(ValueError):run_anchored_identification(p,tmp_path/'run')
    assert not (tmp_path/'run').exists()


def test_dc_anchor_cannot_remove_all_electrical_unknowns():
    p=request();p['audit']['identification']['electrical_parameters']=['stator_r_ohm']
    with pytest.raises(ValueError,match='at least one'):AnchoredDynamicIdentification.model_validate(p)


def test_parent_rhs_limit_is_shared_by_all_bracket_audits(tmp_path):
    p=request();p['maximum_total_rhs_evaluations']=1
    result=run_anchored_identification(p,tmp_path/'run')
    assert not result['completed'] and not result['passed'] and result['rhs_evaluations']<=1
    assert len(result['cases'])==1 and not (tmp_path/'run/passport.json').exists()
    assert not list((tmp_path/'run').glob('*/passport.json'))
    verify(tmp_path/'run')


def test_parent_wall_limit_stops_before_any_fit(tmp_path):
    p=request();p['maximum_total_wall_s']=1e-9
    result=run_anchored_identification(p,tmp_path/'run')
    assert not result['passed'] and result['cases']==[] and result['rhs_evaluations']==0


@pytest.fixture(scope='module')
def accepted(tmp_path_factory):
    root=tmp_path_factory.mktemp('dc-anchor')/'run'
    return run_anchored_identification(request(),root),root


def test_all_three_conditional_raw_audits_are_required(accepted):
    r,path=accepted
    assert r['completed'] and r['passed'] and r['identifiable'],r
    assert {row['id'] for row in r['cases']}=={'nominal','lower','upper'}
    assert all(row['passed'] for row in r['cases']) and r['rhs_evaluations']==sum(row['rhs_evaluations'] for row in r['cases'])
    assert not r['continuous_interval_verified']
    assert (path/'passport.json').exists() and not list(path.glob('*/passport.json'))


def test_approved_anchor_artifact_transports_exactly(accepted,tmp_path):
    r,path=accepted;verify(path);export_zip(path,tmp_path/'anchor.zip');extract_zip(tmp_path/'anchor.zip',tmp_path/'restored');verify(tmp_path/'restored')
    assert read_json(path/'passport.json')==read_json(tmp_path/'restored/passport.json')


def test_completed_counterexample_refuses_contract_without_running_other_points(accepted,tmp_path,monkeypatch):
    import power_grid_env.stator_resistance_anchor as module
    _,source=accepted;calls=[]
    rejected=read_json(source/'nominal/result.json');rejected['passed']=False;rejected['reason']='conditional_current_fit_rejected'
    def reject(audit):calls.append(audit);return copy.deepcopy(rejected)
    monkeypatch.setattr(module,'audit_identification',reject)
    result=run_anchored_identification(request(),tmp_path/'rejected')
    assert result['completed'] and not result['passed'] and not result['bracket_completed']
    assert result['skipped_cases']==['lower','upper'] and len(calls)==1
    assert result['reason']=='anchor_bracket_raw_replay_rejected' and not (tmp_path/'rejected/passport.json').exists()


@pytest.mark.parametrize('defect',['missing_point','duplicate_point','bad_shape','unhashable_id','forged_budget','wrong_anchor_value','sum_mismatch','incomplete_bracket','skipped_points','unidentifiable_point'])
def test_campaign_rejects_contradictory_bracket_results(defect):
    from power_grid_env.campaign import CampaignCase,assessment
    p=request();anchor=resistance_interval(p['stator_resistance_measurement'])
    result=dict(passed=True,completed=True,identifiable=True,total_work_budget_satisfied=True,bracket_completed=True,skipped_cases=[],anchor=anchor,rhs_evaluations=3,
                cases=[dict(id=id,passed=True,completed=True,identifiable=True,rhs_evaluations=1,resistance_ohm=anchor[id+'_ohm']) for id in ('nominal','lower','upper')])
    case=CampaignCase(id='dc',kind='anchored_dynamic_identification',study=p)
    assert assessment(case,result)['passed']
    if defect=='missing_point':result['cases'].pop()
    elif defect=='duplicate_point':result['cases'][2]=dict(result['cases'][0])
    elif defect=='bad_shape':result['cases']='xyz'
    elif defect=='unhashable_id':result['cases'][0]['id']=[]
    elif defect=='forged_budget':result['rhs_evaluations']=p.get('maximum_total_rhs_evaluations',10000000)+1
    elif defect=='wrong_anchor_value':result['cases'][0]['resistance_ohm']+=.01
    elif defect=='sum_mismatch':result['cases'][0]['rhs_evaluations']=2
    elif defect=='incomplete_bracket':result['bracket_completed']=False
    elif defect=='skipped_points':result['skipped_cases']=['upper']
    else:result['cases'][0]['identifiable']=False
    assert not assessment(case,result)['passed']


def test_campaign_worker_honors_incomplete_anchor_budget(tmp_path):
    from power_grid_env.campaign import CampaignCase,CampaignPlan,run_campaign,verify_campaign
    p=request();p['maximum_total_rhs_evaluations']=1
    plan=CampaignPlan(id='dc-budget',cases=[CampaignCase(id='dc',kind='anchored_dynamic_identification',study=p,worker_wall_s=30.)])
    r=run_campaign(plan.model_dump(mode='json'),tmp_path/'campaign')
    assert not r['passed'] and not (tmp_path/'campaign/attempts/dc/passport.json').exists()
    verify_campaign(tmp_path/'campaign')


def test_cli_schema_and_html_escape(tmp_path):
    from power_grid_env.schema_export import export_schemas
    p=request();p['stator_resistance_measurement']['provenance']='<script>bad</script>';p['maximum_total_rhs_evaluations']=1
    (tmp_path/'input.json').write_bytes(encoded(p))
    result=subprocess.run([sys.executable,'-m','power_grid_env.cli','anchor-dynamic-motor','--input',str(tmp_path/'input.json'),'--out',str(tmp_path/'run')],capture_output=True,text=True)
    assert result.returncode==2 and not (tmp_path/'run/passport.json').exists()
    export_schemas(tmp_path/'schemas')
    assert (tmp_path/'schemas/anchored-dynamic-identification.schema.json').exists()
    html=(tmp_path/'run/report.html').read_text()
    assert '<script>bad' not in html and '&lt;script&gt;bad' in html
