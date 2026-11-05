"""Engineering verdicts remain separate from regression expectations."""
import json,hashlib
import numpy as np
import pytest
from pydantic import ValidationError
from benchmarks.rms_scalability import example
from power_grid_env.industrial_dol import IndustrialDOLPack,IndustrialDOLCase,MotorDOLCriteria,DOLVoltageWindow,assessment,run_pack
from power_grid_env.study_spec import run_study


def case():
    s=example(8)
    return IndustrialDOLCase(id='smoke',study=s,parameter_provenance='Authored synthetic regression, not OEM data',motors=[MotorDOLCriteria(motor_id='m',minimum_final_speed_pu=0.,maximum_sampled_current_pu=100.)],voltage_windows=[DOLVoltageWindow(node_id='n7',from_s=0.,to_s=.02,minimum_voltage_pu=0.,maximum_voltage_pu=2.)])


def test_assessment_covers_all_channels_and_preserves_real_violations():
    c=case();r=run_study(c.study);assert assessment(c,r)['within_limits']
    c.motors[0].minimum_final_speed_pu=2.;assert not assessment(c,r)['within_limits']
    r['channels'].pop('m.current_pu')
    with pytest.raises(ValueError,match='channel'):assessment(c,r)


def test_missing_samples_and_nonfinite_balance_are_rejected():
    c=case();r=run_study(c.study);r['maximum_kcl_residual_pu']=float('nan')
    with pytest.raises(ValueError,match='balance'):assessment(c,r)
    r=run_study(c.study);r['time_s']=r['time_s'][:-1]
    with pytest.raises(ValueError,match='grid'):assessment(c,r)


def test_all_motor_criteria_and_explicit_regression_purpose_are_required():
    c=case();d=c.model_dump(mode='json');d['motors']=[]
    with pytest.raises(ValidationError,match='every motor'):IndustrialDOLCase.model_validate(d)
    c.expectation='violates_limits'
    with pytest.raises(ValidationError,match='regression purpose'):IndustrialDOLPack(id='pack',cases=[c])


def test_real_isolated_pack_and_manifest(tmp_path):
    c=case();p=IndustrialDOLPack(id='engineering',cases=[c]);out=tmp_path/'pack';r=run_pack(p,out)
    assert r['completed'] and r['engineering_acceptable'] and r['validation_passed'],r
    m=json.loads((out/'manifest.json').read_text())
    for name,h in m.items():assert hashlib.sha256((out/name).read_bytes()).hexdigest()==h
    with pytest.raises(FileExistsError):run_pack(p,out)


def test_expected_violation_is_not_an_engineering_pass(tmp_path):
    c=case();c.motors[0].minimum_final_speed_pu=2.;c.expectation='violates_limits';p=IndustrialDOLPack(id='negative',purpose='regression',cases=[c]);r=run_pack(p,tmp_path/'pack')
    assert r['completed'] and r['validation_passed'] and not r['engineering_acceptable']


def test_cancellation_never_reports_unexecuted_cases_as_passed(tmp_path):
    r=run_pack(IndustrialDOLPack(id='cancel',cases=[case()]),tmp_path/'pack',cancelled=lambda:True)
    assert not r['completed'] and not r['validation_passed'] and r['cases'][0]['status']=='not_run'


def test_artifact_budget_preflight(tmp_path):
    p=IndustrialDOLPack(id='budget',cases=[case()],maximum_output_bytes=1000000)
    p.cases*=32
    with pytest.raises(ValidationError):run_pack(p,tmp_path/'pack')


def test_voltage_recovery_window_uses_declared_event_side():
    c=case();r=run_study(c.study);take=[0,1,2,2,3,4];r['time_s']=[0.,.005,.01,.01,.015,.02];r['sample_side']=['initial','regular','pre_event','post_event','regular','regular']
    for key in r['channels']:r['channels'][key]=[r['channels'][key][i] for i in take]
    from power_grid_env.topology import compile_topology
    key=compile_topology(c.study.network).node_to_bus['n7']+'.v';r['channels'][key]=[1.,1.,.4,1.,1.,1.]
    c.voltage_windows[0].from_s=.01;c.voltage_windows[0].minimum_voltage_pu=.9
    assert assessment(c,r)['within_limits']
    c.voltage_windows[0].start_side='both';assert not assessment(c,r)['within_limits']


def test_artifact_limit_does_not_publish_partial_success(tmp_path,monkeypatch):
    import power_grid_env.rms_process as process
    c=case();report=run_study(c.study);report['oversized']='x'*1000000
    monkeypatch.setattr(process,'run_isolated_rms',lambda *a,**kw:report)
    out=tmp_path/'pack';r=run_pack(IndustrialDOLPack(id='bytes',cases=[c],maximum_output_bytes=1000000),out)
    assert not r['completed'] and not r['engineering_acceptable']
    assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file())<=1000000
