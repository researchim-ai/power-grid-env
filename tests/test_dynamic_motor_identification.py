from pathlib import Path
import os,subprocess,sys
import numpy as np
import pytest
from power_grid_env.dynamic_motor_identification import DynamicMotorIdentification,DynamicMotorTrace,identify_dynamic
from power_grid_env.motor_catalog import catalog_entry
from power_grid_env.advanced_studies import run_dynamic_identification_artifact
from power_grid_env.campaign import CampaignCase,CampaignPlan,run_campaign
from power_grid_env.study_artifacts import verify,read_json


def unexcited():
    p=catalog_entry('aalto-linear-fan')['motor']
    def trace(id,frequency):return DynamicMotorTrace(id=id,provenance='Authored zero-input identifiability negative fixture',frequency_hz=frequency,time_s=np.linspace(0,.032,33).tolist(),voltage_alpha_v=[0.]*33,voltage_beta_v=[0.]*33,current_alpha_a=[0.]*33,current_beta_a=[0.]*33,speed_rad_s=[0.]*33)
    return DynamicMotorIdentification(initial=p,mechanical_load='fixed',fitting=[trace('train',50.)],validation=[trace('holdout',45.)])


@pytest.fixture(scope='module')
def native():
    from benchmarks.extended_equipment_validation import verify_native
    from benchmarks.dynamic_motor_data import example
    wheel=os.environ.get('PGE_MOTULATOR_WHEEL')
    if not wheel:pytest.skip('explicit pinned motulator wheel required')
    verify_native(Path(wheel));spec,truth=example('saturated');return spec,truth,identify_dynamic(spec)


def test_recovers_dynamic_electrical_saturation_and_mechanics(native):
    spec,p,r=native;assert r['completed'] and r['passed'] and r['identifiable'],r
    q=r['passport']
    for field in ('stator_r_ohm','unsaturated_h','saturation_beta_per_vs'):assert q[field]==pytest.approx(getattr(p,field),rel=5e-5)
    assert q['cages'][0]['resistance_ohm']==pytest.approx(p.cages[0].resistance_ohm,rel=5e-5)
    assert q['inertia_kg_m2']==pytest.approx(p.inertia_kg_m2,rel=5e-5)
    assert q['fan_nm_per_rad_s2']==pytest.approx(p.fan_nm_per_rad_s2,rel=5e-5)
    assert len(r['assessments'])==3 and all(row['passed'] for row in r['assessments'])
    assert not r['field_validated'] and not r['statistical_confidence_claimed']


def test_unexcited_data_never_creates_an_approved_passport(tmp_path):
    r=run_dynamic_identification_artifact(unexcited().model_dump(mode='json'),tmp_path/'run')
    assert r['completed'] and not r['passed'] and not r['identifiable']
    assert r['electrical']['rank']==0 and not (tmp_path/'run/passport.json').exists();verify(tmp_path/'run')


def test_holdout_is_not_used_for_fitting(native):
    spec,truth,baseline=native;s=spec.model_copy(deep=True);s.validation[0].current_alpha_a=[v+.1 for v in s.validation[0].current_alpha_a]
    r=identify_dynamic(s);assert r['completed'] and not r['passed'] and r['reason']=='free_replay_rejected'
    for key in ('stator_r_ohm','unsaturated_h','inertia_kg_m2','fan_nm_per_rad_s2'):assert r['passport'][key]==baseline['passport'][key]
    assert r['assessments'][-1]['current_error_sigma']>5.


def test_conditional_current_fit_cannot_approve_incorrect_fixed_mechanics(native):
    spec,truth,_=native;s=spec.model_copy(deep=True);s.mechanical_load='fixed'
    r=identify_dynamic(s);assert r['completed'] and r['electrical']['identifiable'] and not r['passed']
    assert max(a['speed_error_sigma'] for a in r['assessments'])>s.tolerance_sigma


@pytest.mark.parametrize('defect',['duplicate','renamed','misaligned','unsorted','thermal','multicage','core_loss','rms','zero_saturation_seed'])
def test_invalid_units_geometry_and_leakage_fail_before_solving(defect):
    spec=unexcited().model_dump(mode='json')
    if defect in ('duplicate','renamed'):
        spec['validation'][0]=dict(spec['fitting'][0])
        if defect=='renamed':spec['validation'][0]['id']='different';spec['validation'][0]['current_alpha_a']=[.01]*33
    elif defect=='misaligned':spec['fitting'][0]['voltage_alpha_v']=[0.]
    elif defect=='unsorted':spec['fitting'][0]['time_s'][4]=0.
    elif defect=='thermal':spec['initial']['thermal']=dict(stator_capacity_j_per_k=100,rotor_capacity_j_per_k=100)
    elif defect=='multicage':spec['initial']['cages']*=2
    elif defect=='core_loss':spec['initial']['core_loss_s']=.001;spec['initial']['provenance']['core_loss']='authored'
    elif defect=='rms':spec['coordinates']='RMS'
    else:spec['electrical_parameters']=['saturation_beta_per_vs']
    with pytest.raises(ValueError):DynamicMotorIdentification.model_validate(spec)


@pytest.mark.parametrize('field',['maximum_evaluations','maximum_rhs_evaluations'])
def test_all_optimizer_work_is_bounded(field):
    s=unexcited();setattr(s,field,1);r=identify_dynamic(s)
    assert not r['completed'] and not r['passed'] and r['status']=='runtime_limit_exceeded'
    assert r['evaluations']<=s.maximum_evaluations and r['rhs_evaluations']<=s.maximum_rhs_evaluations


def test_campaign_cannot_approve_nonidentifiable_result(tmp_path):
    case=CampaignCase(id='identification',kind='dynamic_motor_identification',study=unexcited().model_dump(mode='json'),worker_wall_s=20.)
    r=run_campaign(CampaignPlan(id='negative',cases=[case]).model_dump(mode='json'),tmp_path/'run')
    assert not r['passed'] and r['completed'] and not r['cases'][0]['assessment']['available']
    assert not (tmp_path/'run/attempts/identification/passport.json').exists()


def test_cli_negative_result_is_sealed_and_has_no_approved_passport(tmp_path):
    source=tmp_path/'input.json';source.write_text(unexcited().model_dump_json())
    command=[sys.executable,'-m','power_grid_env.cli','identify-dynamic-motor','--input',str(source),'--out',str(tmp_path/'run')]
    result=subprocess.run(command,capture_output=True,text=True)
    assert result.returncode==2,result.stderr;verify(tmp_path/'run')
    assert not (tmp_path/'run/passport.json').exists()
    assert (tmp_path/'run/report.html').exists()


def test_dataset_identity_does_not_depend_on_seed_or_runtime_policy():
    a=unexcited();b=a.model_copy(deep=True);b.initial.stator_r_ohm*=1.1;b.maximum_wall_s=90.
    first=identify_dynamic(a);second=identify_dynamic(b)
    assert first['dataset_sha256']==second['dataset_sha256']
    assert first['protocol_sha256']!=second['protocol_sha256']


def test_report_escapes_trace_labels_and_retains_raw_arrays(tmp_path):
    s=unexcited();s.fitting[0].id='<script>x</script>'
    r=run_dynamic_identification_artifact(s.model_dump(mode='json'),tmp_path/'run')
    text=(tmp_path/'run/report.html').read_text()
    assert '<script>x' not in text and '&lt;script&gt;x' in text and '<svg ' in text
    assert len(r['trajectories'][s.fitting[0].id]['time_s'])==33


def test_subresolution_speed_does_not_identify_inertia_or_load():
    from power_grid_env.dynamic_motor_identification import _mechanics,WorkBudget
    s=unexcited();s.mechanical_load='fan'
    for trace in s.fitting:trace.speed_rad_s=np.linspace(0,1e-5,len(trace.time_s)).tolist()
    _,diagnostic=_mechanics(s.initial,s.fitting,WorkBudget(s),'fan',s.maximum_condition)
    assert not diagnostic['identifiable'] and diagnostic['reason']=='speed_variation_below_resolution'


def test_tiny_original_rhs_observations_are_not_promoted_by_relative_rank():
    from benchmarks.dynamic_motor_data import trace
    wheel=os.environ.get('PGE_MOTULATOR_WHEEL')
    if not wheel:pytest.skip('pinned native wheel required')
    from benchmarks.extended_equipment_validation import verify_native
    verify_native(Path(wheel));p=catalog_entry('aalto-linear-fan')['motor']
    s=DynamicMotorIdentification(initial=p,mechanical_load='fixed',fitting=[trace(p,'tiny-fit',50.,.00001)],validation=[trace(p,'tiny-holdout',45.,.000015)])
    r=identify_dynamic(s)
    assert r['completed'] and not r['passed'] and not r['electrical']['resolved_at_declared_scale']
