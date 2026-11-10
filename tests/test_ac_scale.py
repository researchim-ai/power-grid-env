"""Independent reference accuracy and retained audit guarantees."""
from dataclasses import replace
import copy
import numpy as np
import pytest
from tests.test_ac_dispatch import network,opts
from benchmarks.ac_dispatch_sparse_reference import SparseReference,solve_reference
from benchmarks.ac_dispatch_reference import solve_reference as dense_reference
from benchmarks.ac_scale_acceptance import synthetic,validate_measurement
from power_grid_env.ac_dispatch import DispatchSystem,DispatchOptions,solve_dispatch,audit_dispatch


@pytest.mark.parametrize('bounded',[False,True])
def test_sparse_reference_matches_separate_dense_root(bounded):
    c=network()
    if bounded:c=replace(c,generators=[replace(c.generators[0],pmax=.22),*c.generators[1:]])
    a=solve_reference(c,opts());b=dense_reference(c,opts())
    for key in ['voltage_pu','angle_rad','pg_mw','branch_power_mva']:
        np.testing.assert_allclose(a['result'][key],b['result'][key],atol=1e-8,rtol=0)


def test_reference_jacobian_direction_and_independence(monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('native equation used by reference')
    monkeypatch.setattr(DispatchSystem,'evaluate',forbidden)
    c=network();r=solve_reference(c,opts());model=SparseReference(c,opts());v=r['result']
    x=np.r_[np.array(v['angle_rad'])[model.angles],v['voltage_pu'],v['delta_mw']/c.base_mva]
    _,j=model.evaluate(x,True);direction=np.random.default_rng(4).normal(size=len(x))
    np.testing.assert_allclose(j@direction,(model.evaluate(x+1e-6*direction)-model.evaluate(x-1e-6*direction))/2e-6,atol=1e-7,rtol=1e-7)


def test_large_reference_retains_sparse_storage():
    c=synthetic(2048);o=dict(participants=[dict(generator_index=i,weight=1.) for i in range(len(c.generators))])
    r=solve_reference(c,o)
    assert r['maximum_residual_pu']<1e-10
    assert r['maximum_jacobian_nnz']<40*len(c.buses)


def test_internal_snapshot_still_checks_physics_and_supplied_branches():
    c=network();o=DispatchOptions.model_validate(opts());r=solve_dispatch(c,opts())['result'];snapshot=copy.deepcopy(r);snapshot.pop('branches')
    assert audit_dispatch(c,o,snapshot,branches_required=False)['balance_acceptable']
    with pytest.raises(ValueError):audit_dispatch(c,o,snapshot)
    broken=copy.deepcopy(r);broken['vm_pu'][1]*=.9
    with pytest.raises(ValueError,match='equations not satisfied'):
        audit_dispatch(c,o,broken,branches_required=False)
    broken=copy.deepcopy(r);broken['branches'][0]['p_from_mw']+=1.
    with pytest.raises(ValueError):audit_dispatch(c,o,broken,branches_required=False)


def measurement():
    return dict(repeats=3,physical_hashes=['a']*3,samples=[dict(wall_s=v,cpu_s=v/2,verification_wall_s=.1) for v in [1.,3.,2.]],median_wall_s=2.,median_cpu_s=1.,process_peak_rss_mib=100.,affinity=[0],threads={k:'1' for k in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']},host_load_controlled=False)


@pytest.mark.parametrize('change',['median','nan','repeats','threads','rss'])
def test_resource_receipt_tampering_rejected(change):
    m=measurement();validate_measurement(m)
    if change=='median':m['median_wall_s']=1.
    elif change=='nan':m['samples'][0]['cpu_s']=float('nan')
    elif change=='repeats':m['physical_hashes'].pop()
    elif change=='threads':m['threads']['OMP_NUM_THREADS']='8'
    else:m['process_peak_rss_mib']=-1.
    with pytest.raises(ValueError):validate_measurement(m)


def test_step_comparison_retains_extrapolation_and_rejects_missing_inside_event():
    from benchmarks.ac_scale_acceptance import requested_step_comparison
    def event(lam):return dict(kind='p',equipment=1,before='participating',after='upper',localized=True,lambda_mid=lam)
    a=dict(step=.1,events=[event(.4),event(1.02)]);b=dict(step=.05,events=[event(.4000001)])
    result=requested_step_comparison('test',[a,b]);assert result['passed']
    assert result['extrapolation_events'][0]['events']==[event(1.02)]
    assert not requested_step_comparison('test',[a,dict(step=.05,events=[])])['passed']


def test_full_scaling_verifier_rejects_resealed_false_matrix(tmp_path,monkeypatch):
    import benchmarks.ac_scale_acceptance as bench
    from power_grid_env.balanced_ac import case_document
    from power_grid_env.study_artifacts import encoded,read_json,seal
    data=tmp_path/'data';data.mkdir();c=synthetic(8)
    target=replace(c,buses=[replace(b,pd=b.pd*1.1) for b in c.buses])
    d=dict(start=case_document(c),target=case_document(target),options=dict(enforce_q_limits=True,dispatch=dict(participants=[dict(generator_index=0,weight=1.)])),monitor={})
    (data/'small.json').write_bytes(encoded(d))
    monkeypatch.setattr(bench,'DATA',data);monkeypatch.setattr(bench,'SPECS',(('small','qualification'),));monkeypatch.setattr(bench,'source_hashes',lambda:{'test':'fixed'})
    out=tmp_path/'assessment';assert bench.run(out)['qualified_profiles_passed']
    assert bench.verify_result(out)['passed']
    runs=out/'small/0.1/runs';m=read_json(runs/'measurement.json');m['matrix']['jacobian_bytes']+=8
    (runs/'measurement.json').write_bytes(encoded(m));(runs/'manifest.json').unlink();seal(runs)
    (out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError,match='matrix storage receipt'):bench.verify_result(out)
