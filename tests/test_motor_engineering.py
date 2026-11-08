import copy
from pathlib import Path

import numpy as np
import pytest

from power_grid_env.motor_engineering import (MotorEngineeringPlan,MotorAuditLimits,audit_motor,
    product_integrals,grid_consensus,run_engineering,verify_engineering)
from power_grid_env.industrial_dol import IndustrialDOLCase
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,export_zip,extract_zip

ROOT=Path(__file__).resolve().parents[1]


def example():
    study=StudySpec.model_validate(read_json(ROOT/'cases/studies/induction_start.json'))
    study.induction.duration_s=.1;study.induction.step_s=.005
    return IndustrialDOLCase(id='start',study=study,parameter_provenance='Authored synthetic engineering audit fixture',
        motors=[dict(motor_id='m',minimum_final_speed_pu=0,maximum_sampled_current_pu=100)],
        voltage_windows=[dict(node_id='motor',from_s=0,to_s=.1,minimum_voltage_pu=0,maximum_voltage_pu=2)])


@pytest.fixture(scope='module')
def trace():
    case=example();return case,run_study(case.study)


@pytest.fixture(scope='module')
def packet(tmp_path_factory):
    out=tmp_path_factory.mktemp('motor-engineering')/'study'
    result=run_engineering(dict(id='unit',cases=[example()]),out)
    assert result['validation_passed']
    return out


def test_event_sided_square_integral_never_smooths_current_jump():
    t=np.array([0.,1.,1.,2.]);i=np.array([1.,1.,3.,3.])
    assert sum(product_integrals(t,i,i))==10
    assert product_integrals(t,i,i)[1]==0
    assert sum(product_integrals(np.array([0.,1.]),np.array([0.,1.]),np.array([1.,2.])))==pytest.approx(5/6)


def test_real_shaft_energy_and_phasor_audit(trace):
    case,result=trace;a=audit_motor(case,result);m=a['motors']['m']['metrics']
    assert a['passed'] and m['i2t_pu2_s']>0 and m['interval_rms_current_a']>0
    assert m['maximum_algebra_error_pu']<1e-12
    assert m['kinetic_change_j']>0
    net=m['electromagnetic_work_j']-m['load_work_j']-m['kinetic_change_j']
    assert abs(net)<=m['maximum_cumulative_shaft_balance_error_j']+1e-12


@pytest.mark.parametrize('channel',['te_pu','load_torque_pu','current_pu','p_w','q_var','e_real_pu'])
def test_self_consistency_forgery_does_not_pass_physical_audit(trace,channel):
    case,result=copy.deepcopy(trace)
    result['channels']['m.'+channel]=[x+.1 for x in result['channels']['m.'+channel]]
    assert not audit_motor(case,result)['passed']


@pytest.mark.parametrize('defect',['missing','nan','negative_current','invalid_connection','wrong_identity','stale_code','missing_sample','bad_side','negative_scale'])
def test_invalid_or_incomplete_trace_is_rejected(trace,defect):
    case,r=copy.deepcopy(trace)
    if defect=='missing':del r['channels']['m.te_pu']
    elif defect=='nan':r['channels']['m.te_pu'][2]=float('nan')
    elif defect=='negative_current':r['channels']['m.current_pu'][2]=-1
    elif defect=='invalid_connection':r['channels']['m.connected'][2]=.5
    elif defect=='wrong_identity':r['study_id']='forged'
    elif defect=='stale_code':r['code_sha256']='0'*64
    elif defect=='bad_side':r['sample_side'][2]='post_event'
    elif defect=='negative_scale':r['channels']['m.load_scale'][2]=-1
    else:
        r['time_s'].pop(2);r['sample_side'].pop(2)
        for v in r['channels'].values():v.pop(2)
    with pytest.raises(ValueError):audit_motor(case,r)


def test_initial_inertia_forgery_breaks_shaft_balance(trace):
    case,r=copy.deepcopy(trace);case.study.induction.motors[0].inertia_s*=2
    r['settings']=case.study.induction.model_dump(mode='json')
    assert not audit_motor(case,r)['motors']['m']['checks']['shaft_energy']


def test_balance_at_every_prefix_detects_cancelling_work_errors(trace):
    case,r=copy.deepcopy(trace);t=np.array(r['time_s']);speed=np.array(r['channels']['m.speed_pu'])
    delta=np.where(t<.05,.2,-.2)
    positive=product_integrals(t,np.maximum(delta,0),speed).sum()
    negative=-product_integrals(t,np.minimum(delta,0),speed).sum()
    delta[delta<0]*=positive/negative
    r['channels']['m.te_pu']=(np.array(r['channels']['m.te_pu'])+delta).tolist()
    a=audit_motor(case,r)['motors']['m']['metrics'];b=audit_motor(*trace)['motors']['m']['metrics']
    assert a['electromagnetic_work_j']==pytest.approx(b['electromagnetic_work_j'],abs=1e-7)
    assert a['maximum_cumulative_shaft_balance_error_j']>b['maximum_cumulative_shaft_balance_error_j']+1


def test_thermal_replica_equation_and_forged_cooling():
    from power_grid_env.motor_thermal import MotorThermalProtection
    case=example();case.study.induction.step_s=.001
    case.study.induction.motors[0].thermal=MotorThermalProtection(time_constant_s=1,trip_level=100,initial_level=.2)
    r=run_study(case.study)
    assert audit_motor(case,r)['passed']
    r['channels']['m.thermal_level'][-1]+=.1
    assert not audit_motor(case,r)['motors']['m']['checks']['thermal_equation']


def test_coarse_thermal_sampling_is_not_approved_by_relaxing_limits():
    from power_grid_env.motor_thermal import MotorThermalProtection
    c=example();c.study.induction.motors[0].thermal=MotorThermalProtection(time_constant_s=1,trip_level=100,initial_level=.2)
    assert not audit_motor(c,run_study(c.study))['motors']['m']['checks']['thermal_equation']
    c.study.induction.step_s=.001
    assert audit_motor(c,run_study(c.study))['motors']['m']['checks']['thermal_equation']


@pytest.mark.parametrize('defect',['initial_heat','latch','initial_speed','terminal','load_schedule'])
def test_initial_conditions_topology_and_declared_load_are_audited(trace,defect):
    from power_grid_env.motor_thermal import MotorThermalProtection
    c,r=copy.deepcopy(trace)
    if defect in {'initial_heat','latch'}:
        c.study.induction.step_s=.001;c.study.induction.motors[0].thermal=MotorThermalProtection(time_constant_s=1,trip_level=100,initial_level=.2)
        r=run_study(c.study)
        if defect=='initial_heat':r['channels']['m.thermal_level']=[v+.1 for v in r['channels']['m.thermal_level']]
        else:r['channels']['m.thermal_latched'][-1]=1
    elif defect=='initial_speed':r['channels']['m.speed_pu'][0]=.01
    elif defect=='terminal':r['channels']['m.terminal_voltage_pu'][-1]+=.1
    else:
        r['channels']['m.load_scale']=[2.]*len(r['time_s']);r['channels']['m.load_torque_pu']=[2*x for x in r['channels']['m.load_torque_pu']]
    assert not audit_motor(c,r)['passed']


def test_unsorted_load_events_follow_chronological_order():
    from power_grid_env.induction import InductionEvent
    c=example();c.study.induction.events=[InductionEvent(time_s=.075,kind='load_torque_scale',equipment_id='m',value=1.2),InductionEvent(time_s=.025,kind='load_torque_scale',equipment_id='m',value=.8)]
    r=run_study(c.study);assert audit_motor(c,r)['motors']['m']['checks']['declared_load_schedule']


def test_switching_trace_requires_both_event_sides():
    from power_grid_env.induction import InductionEvent
    c=example();c.study.induction.events=[InductionEvent(time_s=.05,kind='motor_open',equipment_id='m')]
    r=run_study(c.study);assert audit_motor(c,r)['passed']
    i=next(i for i,(t,s) in enumerate(zip(r['time_s'],r['sample_side'])) if t==.05 and s=='post_event')
    r['sample_side'][i]='regular'
    with pytest.raises(ValueError):audit_motor(c,r)


def test_three_grid_consensus_refuses_incomplete_and_boundary_disagreement(trace):
    a=audit_motor(*trace);limits=MotorAuditLimits()
    with pytest.raises(ValueError):grid_consensus([a,a],limits)
    b=copy.deepcopy(a);b['engineering_assessment']['within_limits']=False
    assert not grid_consensus([a,a,b],limits)['numerically_qualified']
    b=copy.deepcopy(a);b['motors']['m']['metrics']['i2t_pu2_s']*=1.1
    assert not grid_consensus([a,a,b],limits)['numerically_qualified']
    b=copy.deepcopy(a);b['passed']=False
    assert not grid_consensus([a,a,b],limits)['numerically_qualified']


@pytest.mark.parametrize('defect',['duplicate','oversize','unsupported','rejected','trace','sample_budget','expected_failure','blank_origin'])
def test_unsupported_plan_fails_before_worker(defect):
    c=example().model_dump(mode='json');p=dict(id='unit',cases=[c])
    if defect=='duplicate':p['cases'].append(copy.deepcopy(c))
    elif defect=='oversize':c['study']['induction']['duration_s']=11
    elif defect=='unsupported':
        m=c['study']['induction']['motors'][0];m.update(model='double_cage',rotor2_r_pu=.1,rotor2_x_pu=.1)
    elif defect=='rejected':c.update(expectation='rejected',rejection_reason='anything')
    elif defect=='trace':c['study']['induction']['event_trace']='sampled_controls'
    elif defect=='sample_budget':c['study']['induction']['step_s']=.000001
    elif defect=='expected_failure':c['expectation']='violates_limits'
    else:c['parameter_provenance']=' '
    with pytest.raises(ValueError):MotorEngineeringPlan.model_validate(p)


def test_real_sealed_three_grid_packet_transfer(packet,tmp_path):
    checked=verify_engineering(packet);assert checked['verified'] and checked['cases']==1
    export_zip(packet,tmp_path/'study.zip');extract_zip(tmp_path/'study.zip',tmp_path/'transferred')
    assert verify_engineering(tmp_path/'transferred')['manifest_sha256']==checked['manifest_sha256']
    with pytest.raises(ValueError):run_engineering(dict(id='unit',cases=[example()]),packet)


@pytest.mark.parametrize('defect',['verdict','audit','study','trace','summary_origin'])
def test_resealed_tampering_is_recomputed(packet,tmp_path,defect):
    import shutil
    root=tmp_path/'copy';shutil.copytree(packet,root);(root/'manifest.json').unlink()
    if defect=='summary_origin':
        path=root/'summary.json';r=read_json(path);r['field_validated']=True
    elif defect=='verdict':
        path=root/'summary.json';r=read_json(path);r['cases'][0]['engineering_acceptable']=False
    elif defect=='audit':
        path=root/'start/grid-2/audit.json';r=read_json(path);r['motors']['m']['metrics']['i2t_pu2_s']*=2
    elif defect=='study':
        path=root/'start/grid-2/study.json';r=read_json(path);r['induction']['step_s']*=2
    else:
        path=root/'start/grid-2/result.json';r=read_json(path);r['channels']['m.te_pu'][3]+=.5
    path.write_bytes(encoded(r));seal(root)
    with pytest.raises(ValueError):verify_engineering(root)


def test_expected_violation_is_not_an_engineering_pass(tmp_path):
    c=example();c.expectation='violates_limits';c.motors[0].minimum_final_speed_pu=2
    r=run_engineering(dict(id='negative',purpose='regression',cases=[c]),tmp_path/'study')
    assert r['completed'] and r['validation_passed'] and not r['engineering_acceptable']


def test_cancelled_or_failed_runs_never_approve(tmp_path,monkeypatch):
    import power_grid_env.rms_process as worker
    r=run_engineering(dict(id='cancel',cases=[example()]),tmp_path/'cancelled',cancelled=lambda:True)
    assert not r['completed'] and not r['validation_passed'] and not r['engineering_acceptable']
    monkeypatch.setattr(worker,'run_isolated_rms',lambda *a,**kw:dict(completed=False,reason='timeout'))
    r=run_engineering(dict(id='timeout',cases=[example()]),tmp_path/'timeout')
    assert r['cases'][0]['reason']=='timeout' and not r['completed']


def test_host_callback_cannot_mutate_frozen_plan(tmp_path):
    case=example()
    def callback():
        case.study.induction.duration_s=.2
        return False
    out=tmp_path/'study';r=run_engineering(dict(id='immutable',cases=[case]),out,cancelled=callback)
    assert r['validation_passed'] and verify_engineering(out)['verified']
    assert read_json(out/'start/grid-2/study.json')['induction']['duration_s']==.1


def test_cli_and_schema(packet,tmp_path,capsys):
    from power_grid_env.cli import main
    from power_grid_env.schema_export import export_schemas
    assert main(['verify-motor-engineering',str(packet)])==0
    export_schemas(tmp_path/'schema');assert read_json(tmp_path/'schema/motor-engineering.schema.json')['title']=='MotorEngineeringPlan'
    assert 'verified' in capsys.readouterr().out
