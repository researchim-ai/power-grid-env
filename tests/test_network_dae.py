"""Index-1 residual, analytic augmented products and matrix-free integration."""
import numpy as np
import pytest
from power_grid_env.dae import NetworkDAE,residual_product,injection_product
from tests.test_sparse_jacobian import profile
from tests.test_network_tangents import PROFILES


@pytest.mark.parametrize('name',PROFILES)
def test_consistency_and_supplied_voltage_are_not_silently_eliminated(name):
    m,x=profile(name);system=NetworkDAE(m);c=system.consistent(0.,x)
    assert np.array_equal(c.state,x) and c.maximum_kcl_pu<1e-8 and c.maximum_differential_residual==0
    assert np.allclose(c.derivative,m.rhs(0.,x),rtol=1e-13,atol=1e-12)
    v=c.voltage*(.985+.002j);r=system.residual(0.,x,c.derivative,v)
    assert np.max(abs(r[len(x):]))>1e-5
    assert np.allclose(r[:len(x)],c.derivative-m.differential_at_voltage(0.,x,v))


@pytest.mark.parametrize('name',PROFILES)
def test_augmented_residual_product_matches_off_manifold_difference(name):
    m,x=profile(name);system=NetworkDAE(m);c=system.consistent(0.,x);v=c.voltage*(.985+.002j)
    rng=np.random.default_rng(31);dx=np.zeros_like(x);dx[:m.electrical_size]=rng.normal(size=m.electrical_size)*.03
    if hasattr(m,'gen_offset'):dx[m.gen_offset:m.rotor_end]=rng.normal(size=m.rotor_end-m.gen_offset)*.03
    dv=(rng.normal(size=len(v))+1j*rng.normal(size=len(v)))*.02;ddot=rng.normal(size=len(x))*.01;eps=1e-6
    measured=residual_product(system,0.,x,c.derivative,v,dx,ddot,dv)
    fd=(system.residual(0.,x+eps*dx,c.derivative+eps*ddot,v+eps*dv)-system.residual(0.,x-eps*dx,c.derivative-eps*ddot,v-eps*dv))/(2*eps)
    assert np.allclose(measured,fd,rtol=2e-6,atol=2e-5),np.max(abs(measured-fd))


@pytest.mark.parametrize('change',['source','motor','topology','reference'])
def test_stale_dae_context_is_rejected(change):
    m,x=profile('esst');d=NetworkDAE(m)
    if change=='source':m.source_scale=.9
    elif change=='motor':m.connected[0]=not m.connected[0]
    elif change=='topology':m.factorize()
    else:m.controllers.vref[0]+=.01
    with pytest.raises(ValueError,match='changed'):d.project(x)


def example():
    from benchmarks.rms_scalability import example
    from power_grid_env.study_spec import StudySpec
    s=example(16);s.induction.jacobian_format='csc';s.induction.jacobian_network='direct_solve';s.induction.integration_method='network_dae'
    return StudySpec.model_validate(s.model_dump(mode='json'))


def test_new_solver_matches_radau_and_reports_actual_linear_algebra():
    from power_grid_env.study_spec import run_study
    s=example();a=run_study(s);s.induction.integration_method='radau';b=run_study(s)
    assert a['completed'] and b['completed'],a
    assert a['sample_side']==b['sample_side']
    for k in a['channels']:assert np.allclose(a['channels'][k],b['channels'][k],rtol=1e-6,atol=1e-5),k
    info=a['integration'];assert info['jacobian_linear_solver']=='GMRES_block_Jacobi' and info['jacobian_assembly']=='analytic_Schur_products'
    assert info['dae']['accepted_steps']>0 and info['dae']['maximum_consistency_kcl_pu']<1e-8
    assert info['dae']['maximum_linear_residual_ratio']<=1.000001


@pytest.mark.parametrize('resource',['maximum_jvp_evaluations','maximum_segment_retained_scalars'])
def test_dae_work_and_mesh_caps_fail_closed(resource):
    from power_grid_env.study_spec import run_study
    s=example();setattr(s.induction.dae_options,resource,1);r=run_study(s)
    assert not r['completed'] and r['status']=='runtime_limit_exceeded',r
    assert r['reason'].startswith('dae_') and 'dae_diagnostics' in r


def test_no_full_state_matrix_or_port_response_during_dae_integration(monkeypatch):
    from benchmarks.eight_machine_validation import case
    from power_grid_env.study_spec import run_study
    from power_grid_env.coupled_rms import CoupledNetwork
    s=case('max_genrou_8_32');s.coupled.integration_method='network_dae';s.coupled.jacobian_network='direct_solve';s.coupled.duration_s=.002;s.coupled.step_s=.001;s.coupled.events=[]
    m=CoupledNetwork(s.network,s.coupled);n=len(m.initial_state());eye=np.eye;zeros=np.zeros
    def forbid(*a,**kw):pytest.fail('port-square matrix constructed')
    def checked_eye(k,*a,**kw):assert k!=n;return eye(k,*a,**kw)
    def checked_zeros(shape,*a,**kw):assert shape!=(n,n);return zeros(shape,*a,**kw)
    import power_grid_env.network_sensitivity as ns
    monkeypatch.setattr(ns,'port_response',forbid);monkeypatch.setattr(np,'eye',checked_eye);monkeypatch.setattr(np,'zeros',checked_zeros)
    r=run_study(s);assert r['completed'],r


def test_historical_study_hashes_are_preserved():
    from benchmarks.eight_machine_validation import SOURCE_CASE_HASHES,case
    from power_grid_env.model import canonical_hash
    for name,expected in SOURCE_CASE_HASHES.items():assert canonical_hash(case(name))==expected


def test_long_restart_preserves_authored_grid_at_computational_event_boundary():
    from benchmarks.ex03_cases import long_case
    from power_grid_env.study_spec import StudySpec,run_study
    s=long_case('gen_long_restart');p=s.coupled;p.duration_s=12.2
    p.events=[e for e in p.events if e.time_s<=p.duration_s]
    p.jacobian_format='csc';p.jacobian_network='direct_solve';p.event_policy='coincident_roots_v1'
    old=run_study(s);p.integration_method='network_dae';new=run_study(StudySpec.model_validate(s.model_dump(mode='json')))
    assert old['completed'] and new['completed']
    assert old['sample_side']==new['sample_side']
    assert np.allclose(old['time_s'],new['time_s'],rtol=0,atol=1e-7)
    assert [(t,side) for t,side in zip(new['time_s'],new['sample_side']) if abs(t-12.)<1e-10]==[(12.,'pre_event'),(12.,'post_event')]


def test_krylov_budget_rejects_nonlocal_coupling_when_iterations_are_required():
    from power_grid_env.dae_solver import NetworkRadau,ShiftedSystem,statistics
    from power_grid_env.dae_options import DAEOptions
    from power_grid_env.runtime_limits import RuntimeLimitExceeded
    m,x=profile('batch');m.connected[:]=True;m.factorize()
    options=DAEOptions(maximum_linear_iterations=1);stats=statistics()
    solver=NetworkRadau(m.rhs,0.,x,.01,model=m,callback=m.integration_jacobian(),options=options,stats=stats,check_time=lambda:None)
    system=ShiftedSystem(1+2j,solver.J);rhs=np.random.default_rng(57).normal(size=len(x))
    with pytest.raises(RuntimeLimitExceeded,match='dae_linear_iterations'):system.solve(rhs)
    assert stats['linear_iterations']==1
