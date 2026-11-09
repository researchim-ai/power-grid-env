from dataclasses import replace
import numpy as np
import pytest
from power_grid_env.balanced_sparse import admittance,derivatives
from power_grid_env.balanced_ac import solve_power_flow
from power_grid_env.network_opf import _ybus,_injections,_state_derivatives,audit_feasibility
from tests.test_balanced_ac_workflows import exact_case


def test_sparse_stamps_and_exact_derivatives_match_dense_and_finite_differences():
    c,_=exact_case();idx={b.id:i for i,b in enumerate(c.buses)}
    y=admittance(c,idx);assert y.toarray()==pytest.approx(_ybus(c,idx))
    vm=np.array([1.04,.98,1.01]);a=np.array([0.,-.04,.02]);blocks=derivatives(vm,a,y)
    for x,z in zip(blocks,_state_derivatives(vm,a,_ybus(c,idx))):assert x.toarray()==pytest.approx(z,abs=1e-12)
    eps=1e-6
    for col in range(3):
        shift=np.zeros(3);shift[col]=eps
        pp,qp=_injections(vm,a+shift,y);pm,qm=_injections(vm,a-shift,y)
        assert blocks[0].toarray()[:,col]==pytest.approx((pp-pm)/(2*eps),abs=1e-8)
        assert blocks[1].toarray()[:,col]==pytest.approx((qp-qm)/(2*eps),abs=1e-8)
        pp,qp=_injections(vm+shift,a,y);pm,qm=_injections(vm-shift,a,y)
        assert blocks[2].toarray()[:,col]==pytest.approx((pp-pm)/(2*eps),abs=1e-8)
        assert blocks[3].toarray()[:,col]==pytest.approx((qp-qm)/(2*eps),abs=1e-8)


@pytest.mark.parametrize('tap,radial',[(True,False),(False,True)])
def test_sparse_exact_network_and_audit(tap,radial):
    c,_=exact_case(tap=tap,radial=radial)
    d=solve_power_flow(c,linear_solver='dense');s=solve_power_flow(c,linear_solver='sparse')
    assert d['completed'] and s['completed']
    assert s['vm_pu']==pytest.approx(d['vm_pu'],abs=1e-10)
    assert s['va_deg']==pytest.approx(d['va_deg'],abs=1e-8)
    assert s['audit']['feasible']==d['audit']['feasible']
    for a,b in zip(s['branches'],d['branches']):assert a['p_from_mw']==pytest.approx(b['p_from_mw'],abs=1e-8)


@pytest.mark.parametrize('name',['case14','case30','case118','case300'])
def test_ieee_sparse_dense_agreement_and_storage(name):
    from benchmarks.ieee_ac_comparison import registered_input
    from power_grid_env.balanced_ac import case_from_document
    c=case_from_document(registered_input(name)['case'])
    d=solve_power_flow(c,linear_solver='dense',enforce_q_limits=False,tolerance_pu=1e-10)
    s=solve_power_flow(c,linear_solver='sparse',enforce_q_limits=False,tolerance_pu=1e-10)
    assert d['completed'] and s['completed']
    assert s['vm_pu']==pytest.approx(d['vm_pu'],abs=1e-8)
    assert s['pg_mw']==pytest.approx(d['pg_mw'],abs=1e-6)
    assert s['qg_mvar']==pytest.approx(d['qg_mvar'],abs=1e-6)
    assert s['linear_solver']['maximum_jacobian_bytes']<d['linear_solver']['maximum_jacobian_bytes']
    assert solve_power_flow(c)['linear_solver']['used']==('dense' if len(c.buses)<=128 else 'sparse')


def test_invalid_solver_name_is_not_ignored():
    c,_=exact_case()
    with pytest.raises(ValueError):solve_power_flow(c,linear_solver='unknown')


def test_sparse_pv_to_pq_switch_retains_limits_and_ids():
    from power_grid_env.network_opf import GeneratorSpec
    c,_=exact_case(tap=False)
    c=replace(c,buses=[c.buses[0],replace(c.buses[1],type_code=2,vm=1.1),c.buses[2]],
        generators=[c.generators[0],GeneratorSpec(20,-.01,.01,-500.,500.,(1.,0.),pg=0.,qg=0.)])
    d=solve_power_flow(c,linear_solver='dense');s=solve_power_flow(c,linear_solver='sparse')
    assert d['completed'] and s['completed'] and d['pv_to_pq']==s['pv_to_pq']
    assert abs(s['qg_mvar'][1])==pytest.approx(.01,abs=1e-7)
    assert s['vm_pu']==pytest.approx(d['vm_pu'],abs=1e-9)


def test_sparse_island_is_not_silently_given_another_reference():
    c,_=exact_case(radial=True);c=replace(c,branches=[replace(b,status=0) for b in c.branches])
    s=solve_power_flow(c,linear_solver='sparse')
    assert not s['completed'] and s['status']=='islanded'


def test_parallel_offline_stamps_and_permuted_bus_order():
    c,_=exact_case();c=replace(c,buses=c.buses[::-1],branches=[*c.branches,c.branches[0],replace(c.branches[1],status=0)])
    idx={b.id:i for i,b in enumerate(c.buses)}
    assert admittance(c,idx).toarray()==pytest.approx(_ybus(c,idx),abs=1e-12)
    d=solve_power_flow(c,linear_solver='dense');s=solve_power_flow(c,linear_solver='sparse')
    assert d['completed'] and s['completed'] and d['bus_ids']==s['bus_ids']
    assert s['vm_pu']==pytest.approx(d['vm_pu'],abs=1e-9)


def test_sparse_path_and_audit_do_not_call_dense_matrix_helpers(monkeypatch):
    import power_grid_env.balanced_ac as module
    import power_grid_env.network_opf as opf
    c,_=exact_case()
    def forbidden(*a,**k):raise AssertionError('dense allocation/solver helper invoked')
    monkeypatch.setattr(module,'_ybus',forbidden);monkeypatch.setattr(opf,'_ybus',forbidden)
    monkeypatch.setattr(module,'_state_derivatives',forbidden);monkeypatch.setattr(np.linalg,'solve',forbidden)
    monkeypatch.setattr(np,'block',forbidden)
    assert solve_power_flow(c,linear_solver='sparse')['completed']


def test_sparse_singular_jacobian_is_explicit_without_dense_fallback():
    c,_=exact_case();line=replace(c.branches[0],r=0.,x=1.,b=0.,ratio=0.,angle_deg=0.)
    buses=[replace(b,gs=0.,bs=0.,pd=0. if i==0 else 1.,qd=0.) for i,b in enumerate(c.buses[:2])]
    c=replace(c,buses=buses,branches=[line,replace(line,x=-1.)])
    for mode in ['dense','sparse']:
        result=solve_power_flow(c,linear_solver=mode)
        assert not result['completed'] and result['status']=='singular_jacobian'
