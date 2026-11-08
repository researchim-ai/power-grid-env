import copy
from pathlib import Path

import numpy as np
import pytest

from power_grid_env.comparison import (ComparisonCase,ComparisonPlan,ComparisonTrace,compare_traces,
    summary_for,finalize_comparison,verify_comparison)
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,digest,read_json,seal,export_zip,extract_zip


def case():
    return ComparisonCase(id='fixture',track='rms_motor',provider='andes_motor3',reference_kind='authored_independent_equations',
        reference_backend='fixture oracle',reference_version='1',alignment='exact',sample_scope='event_free_trajectory',
        primary_axis=[0.,.5,1.],reference_axis=[0.,.5,1.],input=dict(synthetic_fixture=True),
        mappings=['Synthetic contract test, not evidence of a third-party physical run'],
        channels=[dict(name='speed',unit='pu',absolute_limit=.01)])


def traces(c=None):
    c=c or case();a=dict(completed=True,case_input_sha256=digest(encoded(c.input)),producer_sha256=package_fingerprint(),
        backend='power-grid-env',backend_version='0.1.0',source_sha256={'fixture.py':'0'*64},axis=c.primary_axis,
        channels={'speed':[0.,.5,1.]},wall_s=.1)
    b=copy.deepcopy(a);b.update(backend=c.reference_backend,backend_version=c.reference_version,axis=c.reference_axis,wall_s=4.)
    return a,b


def packet(root,*,extra=None,failed=False):
    root.mkdir();c=case();cases=[c]+([extra] if extra else [])
    p=ComparisonPlan(id='fixture-suite',cases=cases);(root/'input.json').write_bytes(encoded(p.model_dump(mode='json')))
    folder=root/'cases/fixture';folder.mkdir(parents=True);a,b=traces(c)
    for name,data in [('primary',a),('reference',b)]: (folder/(name+'.json')).write_bytes(encoded(data))
    (folder/'outcome.json').write_bytes(encoded(dict(status='failed',reason='timeout') if failed else dict(status='completed')))
    return finalize_comparison(root)


def exclusion(coverage='not_performed'):
    c=case().model_dump();c.update(id='excluded',coverage=coverage,exclusion_reason='Outside this fixture profile')
    return ComparisonCase.model_validate(c)


def test_complete_comparison_has_physical_errors_but_no_speed_ranking():
    c=case();a,b=traces(c);a['channels']['speed'][1]+=.005
    r=compare_traces(c,a,b)
    assert r['status']=='passed' and r['metrics']['speed']['max_absolute_error']==pytest.approx(.005)
    assert r['metrics']['speed']['rmse']==pytest.approx(.005/np.sqrt(3))
    assert r['primary_wall_s']<r['reference_wall_s'] and not r['timing_comparable'] and not r['speedup_claimed']


def test_crossing_limit_is_a_failed_comparison_not_a_changed_scope():
    c=case();a,b=traces(c);b['channels']['speed'][-1]+=.1
    r=compare_traces(c,a,b);assert r['status']=='failed'
    result=summary_for(ComparisonPlan(id='fixture',cases=[c]),[r])
    assert result['failed_required']==1 and not result['required_comparisons_passed']


@pytest.mark.parametrize('defect',['not_complete','wrong_input','stale_producer','wrong_version','wrong_backend','missing_channel','extra_channel',
    'nan','infinity','short_values','duplicate_axis','reverse_axis','both_truncated','reference_gap','same_backend','bad_hash','fake_pass'])
def test_bad_physics_envelopes_never_qualify(defect):
    c=case();a,b=traces(c)
    if defect=='not_complete':b['completed']=False
    elif defect=='wrong_input':b['case_input_sha256']='1'*64
    elif defect=='stale_producer':a['producer_sha256']='1'*64
    elif defect=='wrong_version':b['backend_version']='2'
    elif defect=='wrong_backend':b['backend']='other oracle'
    elif defect=='missing_channel':b['channels']={}
    elif defect=='extra_channel':a['channels']['hidden']=[0.,0.,0.]
    elif defect=='nan':b['channels']['speed'][1]=float('nan')
    elif defect=='infinity':b['channels']['speed'][1]=float('inf')
    elif defect=='short_values':a['channels']['speed'].pop()
    elif defect=='duplicate_axis':b['axis']=[0.,0.,1.]
    elif defect=='reverse_axis':b['axis']=[0.,1.,.5]
    elif defect=='both_truncated':
        for x in [a,b]:x['axis']=x['axis'][:-1];x['channels']['speed'].pop()
    elif defect=='reference_gap':b['axis']=[0.,1.];b['channels']['speed']=[0.,1.]
    elif defect=='same_backend':a['backend']=b['backend']
    elif defect=='bad_hash':b['source_sha256']={'bad':'not a SHA256'}
    else:b['passed']=True
    with pytest.raises(ValueError):compare_traces(c,a,b)


def test_interpolation_is_explicit_full_horizon_and_event_free():
    c=case().model_dump();c.update(alignment='linear_event_free',reference_axis=[0.,.25,.5,.75,1.])
    c=ComparisonCase.model_validate(c);a,b=traces(c);b['channels']['speed']=[0.,.25,.5,.75,1.]
    assert compare_traces(c,a,b)['status']=='passed'
    c.reference_axis=[0.,.25,.5,.75];b['axis']=c.reference_axis;b['channels']['speed'].pop()
    with pytest.raises(ValueError,match='horizon'):compare_traces(c,a,b)
    data=c.model_dump();data['sample_scope']='right_event_limits'
    with pytest.raises(ValueError,match='jumps'):ComparisonCase.model_validate(data)


def test_shared_upstream_is_not_independent_validation():
    c=case();c.reference_kind='shared_upstream';a,b=traces(c);a['backend']=b['backend']
    result=summary_for(ComparisonPlan(id='fixture',cases=[c]),[compare_traces(c,a,b)])
    assert result['required_comparisons_passed'] and result['independent_passes']==0
    assert not result['competitive_superiority_proven']


@pytest.mark.parametrize('coverage',['not_applicable','not_performed'])
def test_exclusions_are_never_hidden_inside_pass_denominator(tmp_path,coverage):
    r=packet(tmp_path/'packet',extra=exclusion(coverage))
    assert r['required_comparisons_passed'] and r['required_cases']==1 and r['declared_cases']==2
    assert r[coverage]==1 and not r['full_declared_inventory_executed']
    assert r['independent_passes']==1 and not r['competitive_superiority_proven']


def test_failed_worker_is_not_not_applicable(tmp_path):
    r=packet(tmp_path/'failed',failed=True)
    assert not r['required_comparisons_passed'] and r['failed_required']==1 and r['not_applicable']==0
    assert r['cases'][0]['status']=='execution_failed' and verify_comparison(tmp_path/'failed')['verified']


@pytest.mark.parametrize('defect',['no_required','duplicate_case','duplicate_channel','missing_reason','blank_reason','false_exclusion','no_mapping','no_axis','bad_alignment'])
def test_invalid_preregistration_is_rejected(defect):
    c=case().model_dump();document=dict(id='fixture',cases=[c])
    if defect=='no_required':document['cases']=[exclusion().model_dump()]
    elif defect=='duplicate_case':document['cases'].append(copy.deepcopy(c))
    elif defect=='duplicate_channel':c['channels']*=2
    elif defect=='missing_reason':c.update(coverage='not_applicable',exclusion_reason=None)
    elif defect=='blank_reason':c.update(coverage='not_performed',exclusion_reason=' ')
    elif defect=='false_exclusion':c['exclusion_reason']='Pretend it was not supported'
    elif defect=='no_mapping':c['mappings']=[' ']
    elif defect=='no_axis':c['primary_axis']=[]
    else:c['reference_axis']=[0.,.25,.5,1.]
    with pytest.raises(ValueError):ComparisonPlan.model_validate(document)


def test_sealed_bundle_can_be_verified_after_transfer(tmp_path):
    root=tmp_path/'packet';packet(root);checked=verify_comparison(root)
    export_zip(root,tmp_path/'packet.zip');extract_zip(tmp_path/'packet.zip',tmp_path/'transferred')
    assert verify_comparison(tmp_path/'transferred')==checked
    with pytest.raises(ValueError):finalize_comparison(root)


@pytest.mark.parametrize('defect',['fake_superiority','fake_speed','fake_coverage','fake_error','fake_trace','missing_run','illegal_exclusion'])
def test_resealed_summary_and_trace_fabrications_are_recomputed(tmp_path,defect):
    root=tmp_path/'packet';packet(root,extra=exclusion());(root/'manifest.json').unlink()
    path=root/'summary.json';data=read_json(path)
    if defect=='fake_superiority':data['competitive_superiority_proven']=True
    elif defect=='fake_speed':data['cases'][0]['speedup_claimed']=True
    elif defect=='fake_coverage':data['not_performed']=0
    elif defect=='fake_error':data['cases'][0]['metrics']['speed']['max_absolute_error']=999
    elif defect=='fake_trace':path=root/'cases/fixture/reference.json';data=read_json(path);data['channels']['speed'][-1]=5.
    elif defect=='missing_run':(root/'cases/fixture/reference.json').unlink()
    else:
        folder=root/'cases/excluded';folder.mkdir();(folder/'primary.json').write_text('{}')
    path.write_bytes(encoded(data));seal(root)
    with pytest.raises((ValueError,OSError)):verify_comparison(root)


def test_complete_case_coverage_and_portable_cli(tmp_path,capsys):
    from power_grid_env.cli import main
    from power_grid_env.schema_export import export_schemas
    c=case();r=compare_traces(c,*traces(c))
    with pytest.raises(ValueError):summary_for(ComparisonPlan(id='fixture',cases=[c]),[])
    root=tmp_path/'packet';packet(root)
    assert main(['verify-comparison',str(root)])==0
    export_schemas(tmp_path/'schema');assert read_json(tmp_path/'schema/comparison-plan.schema.json')['title']=='ComparisonPlan'
    assert 'verified' in capsys.readouterr().out


def test_registered_inventory_and_backend_versions_are_frozen():
    from benchmarks.comparison_suite import registered_plan
    plan=registered_plan();assert len(plan.cases)==10
    assert sum(c.coverage=='required' for c in plan.cases)==8
    assert {c.reference_version for c in plan.cases if c.coverage=='required'}=={'3.5.6','2.0.0','0.19.0'}
    assert {c.track for c in plan.cases if c.coverage=='required'}=={'steady_state','rms_motor','flux_network'}


@pytest.mark.parametrize('index',[0,1])
def test_real_pandapower_mapping_covers_taps_shunts_pv_and_bus_ids(tmp_path,index):
    from benchmarks.comparison_suite import registered_plan,run_provider
    c=registered_plan().cases[index];run_provider(c,tmp_path,None)
    r=compare_traces(c,read_json(tmp_path/'primary.json'),read_json(tmp_path/'reference.json'))
    assert r['status']=='passed' and r['reference_version']=='3.5.6'
    assert r['metrics']['voltage_pu']['max_absolute_error']<1e-8
    assert r['metrics']['angle_rad']['max_absolute_error']<1e-8
    assert read_json(tmp_path/'reference.json')['axis']==[10.,20.,30.]


def test_expired_suite_budget_records_required_failures(tmp_path):
    from benchmarks.comparison_suite import run_suite
    out=tmp_path/'expired';r=run_suite(out,total_timeout_s=1e-6,timeout_s=1e-6)
    assert not r['required_comparisons_passed'] and r['failed_required']==8
    assert r['not_performed']==1 and r['not_applicable']==1 and r['independent_passes']==0
    assert all(c['reason']=='total_timeout' for c in r['cases'] if c['status']=='execution_failed')
    assert verify_comparison(out)['verified']


def test_registered_profile_cannot_relax_limits_or_hide_a_required_case(tmp_path):
    from benchmarks.comparison_suite import registered_plan,run_suite
    p=registered_plan().model_dump(mode='json');p['cases'][0].update(coverage='not_performed',exclusion_reason='Hide failed baseline')
    with pytest.raises(ValueError,match='registered profile'):run_suite(tmp_path/'bad',document=p)
    assert not (tmp_path/'bad').exists()


def test_integer_and_float_power_flow_inputs_are_equivalent():
    from power_grid_env.network_opf import Bus,Branch,GeneratorSpec,OpfCase
    from power_grid_env.balanced_ac import solve_power_flow,case_document,case_from_document
    # All integer starting voltages/injections used to allocate integer arrays:
    # division failed, and slack dispatch could also silently truncate fractions.
    c=OpfCase('integer-inputs',100,[Bus(1,3,0,0,0,0,1,0,.8,1.2),Bus(2,1,20,8,0,0,1,0,.8,1.2)],
              [GeneratorSpec(1,-1000,1000,-1000,1000,(0,),pg=0,qg=0)],[Branch(1,2,.02,.08,0,1000,0,0,1,-90,90)])
    a=solve_power_flow(c);d=case_document(c)
    for b in d['buses']:
        for k in ['pd','qd','gs','bs','vm','va_deg']:b[k]=float(b[k])
    for g in d['generators']:
        for k in ['pg','qg']:g[k]=float(g[k])
    b=solve_power_flow(case_from_document(d))
    assert a['completed'] and a['within_limits']
    assert a['pg_mw']==pytest.approx(b['pg_mw'],abs=1e-12)
    assert a['pg_mw'][0]>20 and a['pg_mw'][0]!=round(a['pg_mw'][0])
    assert a['qg_mvar']==pytest.approx(b['qg_mvar'],abs=1e-12)
    assert a['vm_pu']==pytest.approx(b['vm_pu'],abs=1e-12)
