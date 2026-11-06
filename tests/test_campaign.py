import json,subprocess,sys
from pathlib import Path
import pytest
from power_grid_env.campaign import CampaignPlan,CampaignCase,CampaignGrid,Rule,expand_grid,run_campaign,assessment
from power_grid_env.study_artifacts import verify,export_zip,extract_zip,read_json
from tests.test_protected_motor import short


def plan(**options):
    case=CampaignCase(id='short',kind='protected_motor',study=short().model_dump(mode='json'),worker_wall_s=10.,rules=[Rule(path='/engineering/classification',operator='eq',value='not_accelerated')])
    return CampaignPlan(id='acceptance',cases=[case],**options)


def test_real_worker_reports_and_portable_continuation(tmp_path):
    p=plan();a=run_campaign(p.model_dump(mode='json'),tmp_path/'first')
    assert a['passed'] and a['completed'];verify(tmp_path/'first')
    assert read_json(tmp_path/'first/attempts/short/worker.json')['isolated_process']
    assert (tmp_path/'first/attempts/short/report.html').is_file()
    first=export_zip(tmp_path/'first',tmp_path/'one.zip');second=export_zip(tmp_path/'first',tmp_path/'two.zip');assert first==second
    extract_zip(tmp_path/'one.zip',tmp_path/'imported')
    b=run_campaign(p.model_dump(mode='json'),tmp_path/'second',resume_from=tmp_path/'imported')
    assert b['passed'] and b['cases'][0]['reused'];verify(tmp_path/'second')
    assert b['cases'][0]['attempt_manifest_sha256']==a['cases'][0]['attempt_manifest_sha256']
    # The new run has no dependency on the old run directory.
    import shutil
    shutil.rmtree(tmp_path/'imported');verify(tmp_path/'second')


def test_cancelled_cases_are_not_successes_and_can_continue(tmp_path):
    p=plan();cancel=tmp_path/'cancel';cancel.touch()
    a=run_campaign(p.model_dump(mode='json'),tmp_path/'cancelled',cancel_file=cancel)
    assert not a['passed'] and not a['completed'];assert a['cases'][0]['status']=='not_run_cancelled';verify(tmp_path/'cancelled')
    cancel.unlink();b=run_campaign(p.model_dump(mode='json'),tmp_path/'resumed',resume_from=tmp_path/'cancelled')
    assert b['passed'] and not b['cases'][0]['reused']


def test_numerical_failure_timeout_and_stop_on_failure(tmp_path):
    p=plan(stop_on_failure=True);p.cases[0].worker_wall_s=.001
    p.cases.append(p.cases[0].model_copy(update={'id':'second'}))
    a=run_campaign(p.model_dump(mode='json'),tmp_path/'timeout')
    assert a['cases'][0]['status']=='worker_timeout' and not a['passed']
    assert a['cases'][1]['status']=='not_run_stop_on_failure';verify(tmp_path/'timeout')
    p=plan();p.cases[0].study['runtime_limits']['maximum_rhs_evaluations']=1
    a=run_campaign(p.model_dump(mode='json'),tmp_path/'failure')
    assert a['cases'][0]['status']=='calculation_failed' and not a['completed'];verify(tmp_path/'failure')


def test_engineering_rejection_is_reused_as_a_finished_calculation(tmp_path):
    p=plan();p.cases[0].rules=[Rule(path='/engineering/engineering_acceptable',operator='eq',value=True)]
    a=run_campaign(p.model_dump(mode='json'),tmp_path/'first');assert a['completed'] and not a['passed']
    b=run_campaign(p.model_dump(mode='json'),tmp_path/'second',resume_from=tmp_path/'first')
    assert b['cases'][0]['reused'] and not b['passed']


def test_corruption_and_changed_plan_are_rejected(tmp_path):
    p=plan();run_campaign(p.model_dump(mode='json'),tmp_path/'first')
    changed=p.model_copy(update={'id':'different'})
    with pytest.raises(ValueError,match='plan or producer'):run_campaign(changed.model_dump(mode='json'),tmp_path/'new',resume_from=tmp_path/'first')
    (tmp_path/'first/attempts/short/result.json').write_text('{}')
    with pytest.raises(ValueError,match='inventory'):run_campaign(p.model_dump(mode='json'),tmp_path/'new',resume_from=tmp_path/'first')
    assert not (tmp_path/'new').exists()


def test_grid_and_physical_validation():
    p=plan();grid=dict(id='grid',template=p.cases[0].model_dump(mode='json'),axes=[dict(path='/relay/trip_level',values=[4.,6.],unit='pu'),dict(path='/relay/time_constant_s',values=[1.,2.],unit='s')])
    expanded=expand_grid(grid);assert len(expanded.cases)==4
    assert [(c.study['relay']['trip_level'],c.study['relay']['time_constant_s']) for c in expanded.cases]==[(4.,1.),(4.,2.),(6.,1.),(6.,2.)]
    grid['axes'][0]['values']=[-1.]
    with pytest.raises(ValueError):expand_grid(grid)


@pytest.mark.parametrize('actual',[True,{},[],None,'1'])
def test_ordered_rules_never_coerce_missing_or_nonnumeric_metrics(actual):
    c=plan().cases[0];c.rules=[Rule(path='/x',operator='le',value=2.)]
    assert not assessment(c,dict(completed=True,x=actual))['passed']


def test_nested_worker_policy_and_unused_protocol_rejected():
    c=plan().cases[0].model_dump(mode='json');c['protocol']={}
    with pytest.raises(ValueError,match='protocol only'):CampaignCase.model_validate(c)


def test_cli_grid_and_error_exit(tmp_path):
    source=tmp_path/'grid.json';source.write_text(json.dumps(dict(id='grid',template=plan().cases[0].model_dump(mode='json'),axes=[dict(path='/relay/trip_level',values=[5.],unit='pu')])))
    result=subprocess.run([sys.executable,'-m','power_grid_env.cli','campaign','grid','--input',str(source),'--out',str(tmp_path/'plan.json')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    CampaignPlan.model_validate(read_json(tmp_path/'plan.json'))
    result=subprocess.run([sys.executable,'-m','power_grid_env.cli','campaign','verify',str(tmp_path/'missing')],capture_output=True,text=True)
    assert result.returncode==2


def test_semantic_verifier_rejects_resealed_false_accounting(tmp_path):
    from power_grid_env.campaign import verify_campaign
    from power_grid_env.study_artifacts import seal
    p=plan();run_campaign(p.model_dump(mode='json'),tmp_path/'first')
    summary=read_json(tmp_path/'first/summary.json');summary['passed']=False
    (tmp_path/'first/summary.json').write_text(json.dumps(summary));(tmp_path/'first/manifest.json').unlink();seal(tmp_path/'first')
    # Hash integrity alone passes; the physical result accounting must not.
    verify(tmp_path/'first')
    with pytest.raises(ValueError,match='success accounting'):verify_campaign(tmp_path/'first')


def test_missing_metrics_nonscalar_and_boolean_equality_fail():
    c=plan().cases[0];c.rules=[Rule(path='/absent',operator='eq',value=None)]
    assert not assessment(c,dict(completed=True))['passed']
    c.rules=[Rule(path='/x',operator='eq',value=1.)]
    assert not assessment(c,dict(completed=True,x=True))['passed']
    assert not assessment(c,dict(completed=False,x=1.))['passed']


def test_report_text_and_csv_are_escaped(tmp_path):
    from power_grid_env.campaign import _report
    p=plan();out=tmp_path/'report';out.mkdir()
    _report(out,p,[dict(id='x',status='failed',passed=False,assessment=dict(rules=[dict(path='/x',operator='eq',limit='<script>x</script>',actual='=1+1')]))])
    assert '<script>x' not in (out/'report.html').read_text()
    assert "'=1+1" in (out/'comparison.csv').read_text()


def test_crash_recovery_uses_only_sealed_finished_attempts(tmp_path):
    import shutil
    p=plan();p.cases.append(p.cases[0].model_copy(update={'id':'second'}))
    run_campaign(p.model_dump(mode='json'),tmp_path/'interrupted')
    # Simulate the parent dying after the first committed attempt and during
    # the second one. An unsealed result alone is never a resumable success.
    (tmp_path/'interrupted/manifest.json').unlink();(tmp_path/'interrupted/summary.json').unlink()
    (tmp_path/'interrupted/attempts/second/manifest.json').unlink()
    b=run_campaign(p.model_dump(mode='json'),tmp_path/'recovered',recover_from=tmp_path/'interrupted')
    assert b['passed'] and b['cases'][0]['reused'] and not b['cases'][1]['reused']
    assert list(b['recovered_attempts'])==['short']
    from power_grid_env.campaign import verify_campaign
    verify_campaign(tmp_path/'recovered')


def test_campaign_parent_cannot_contain_new_output(tmp_path):
    p=plan();run_campaign(p.model_dump(mode='json'),tmp_path/'parent')
    with pytest.raises(ValueError,match='outside'):run_campaign(p.model_dump(mode='json'),tmp_path/'parent/new',resume_from=tmp_path/'parent')
    verify(tmp_path/'parent')


def test_inflight_cancel_terminates_worker_and_seals_run(tmp_path):
    import os,time
    p=plan();p.cases[0].study=__import__('benchmarks.protected_motor_cases',fromlist=['case']).case('aalto-saturated-fan__restart').model_dump(mode='json')
    source=tmp_path/'plan.json';source.write_text(p.model_dump_json());cancel=tmp_path/'cancel'
    process=subprocess.Popen([sys.executable,'-m','power_grid_env.cli','campaign','run','--plan',str(source),'--out',str(tmp_path/'run'),'--cancel-file',str(cancel)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+15
        while not (tmp_path/'run/attempts/short/stdout.log').exists():
            assert process.poll() is None
            assert time.monotonic()<deadline
            time.sleep(.02)
        cancel.touch();_,errors=process.communicate(timeout=15)
        assert process.returncode==2,errors
        result=read_json(tmp_path/'run/summary.json')
        assert not result['passed'] and result['cases'][0]['status']=='cancelled'
        from power_grid_env.campaign import verify_campaign
        verify_campaign(tmp_path/'run')
    finally:
        if process.poll() is None:process.kill();process.wait()
