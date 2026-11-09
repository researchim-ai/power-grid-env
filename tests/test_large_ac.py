from dataclasses import replace
import numpy as np
import pytest
from power_grid_env.network_opf import Bus,Branch,GeneratorSpec,OpfCase,solve_ac_opf,audit_feasibility
from power_grid_env.balanced_ac import solve_power_flow,validate_case,case_document,case_from_document


def chain(n=600):
    return OpfCase('authored-large-chain',100.,
        [Bus(i,3 if i==0 else 1,0. if i==0 else .0001,0.,0.,0.,1.,0.,.8,1.2) for i in range(n)],
        [GeneratorSpec(0,-1000.,1000.,-1000.,1000.,(0.,),pg=0.,qg=0.)],
        [Branch(i,i+1,.001,.01,0.,1000.,0.,0.,1,-90.,90.) for i in range(n-1)])


def test_large_sparse_solution_and_input_roundtrip_without_dense_fallback(monkeypatch):
    import power_grid_env.balanced_ac as balanced
    import power_grid_env.network_opf as opf
    c=chain();c=case_from_document(case_document(c))
    def forbidden(*a,**k):raise AssertionError('dense builder invoked')
    monkeypatch.setattr(balanced,'_ybus',forbidden);monkeypatch.setattr(opf,'_ybus',forbidden)
    r=solve_power_flow(c)
    assert r['completed'] and r['linear_solver']['used']=='sparse' and r['audit']['feasible']
    assert len(r['bus_ids'])==600 and r['audit']['max_mismatch_pu']<1e-8


@pytest.mark.parametrize('operation',['solve','opf','audit'])
def test_dense_consumers_reject_large_input_before_dense_allocation(monkeypatch,operation):
    import power_grid_env.network_opf as opf
    import power_grid_env.balanced_ac as ac
    c=chain()
    def forbidden(*a,**k):raise AssertionError('dense allocation before guard')
    monkeypatch.setattr(opf,'_ybus',forbidden);monkeypatch.setattr(ac,'_ybus',forbidden)
    with pytest.raises(ValueError,match='512'):
        if operation=='solve':solve_power_flow(c,linear_solver='dense')
        elif operation=='opf':solve_ac_opf(c)
        else:audit_feasibility(c,np.ones(600),np.zeros(600),np.zeros(1),np.zeros(1))


def test_bounded_large_case_limit_and_explicit_islands():
    with pytest.raises(ValueError,match='2048'):validate_case(chain(2049))
    c=chain();c=replace(c,branches=c.branches[:-1])
    r=solve_power_flow(c)
    assert not r['completed'] and r['status']=='islanded' and len(r['islands'])==2


def test_registered_positive_and_negative_initialization_contracts():
    from benchmarks.large_ac_acceptance import SPECS,identifier,row_for
    assert len(SPECS)==7 and len(set(map(identifier,SPECS)))==7
    assert sum(s[2]=='dc_seed' for s in SPECS)==3
    d=dict(id='negative',expected='both_not_converged')
    result=row_for(d,{'primary_sparse':dict(completed=False,status='newton_not_converged'), 'pandapower':dict(completed=False,status='reference_not_converged')})
    assert result['passed'] and not result['solved'] and not result['voltage_collapse_proven']
    result=row_for(d,{'primary_sparse':dict(completed=False,status='execution_failed'), 'pandapower':dict(completed=False,status='reference_not_converged')})
    assert not result['passed']


def test_rte_pf_export_preserves_fixed_sgen_Q_and_matches_reference():
    import pandapower as pp
    from benchmarks.large_ac_acceptance import document
    from benchmarks.ieee_ac_comparison import normalize_reference,normalize_primary,independent_balance,compare_outputs
    d=document(('case1888rte',1.,'dc_seed'));c=case_from_document(d['case'])
    assert d['conversion_mode']=='pf' and d['reactive_limits'] is False
    # The default public exporter forced Q-limit handling and gave +1 here;
    # this declared Q-limits-disabled PF must retain the -1 Mvar injection.
    assert d['case']['buses'][388]['qd']==pytest.approx(-1.)
    a=normalize_primary(d['case'],solve_power_flow(c,tolerance_pu=1e-10,enforce_q_limits=False))
    n=pp.from_json_string(d['pandapower_json'])
    pp.runpp(n,init='results',trafo_model='pi',numba=True,lightsim2grid=False,tolerance_mva=1e-10,max_iteration=40,enforce_q_lims=False,voltage_depend_loads=False)
    b=normalize_reference(d['case'],n)
    assert independent_balance(d['case'],b)<1e-6
    assert compare_outputs(d,dict(result=a),dict(result=b))['passed']


def test_unexpected_failure_is_not_reclassified_as_successful_negative():
    from benchmarks.large_ac_acceptance import row_for
    d=dict(id='expected-positive',expected='converged_match')
    row=row_for(d,{'primary_sparse':dict(completed=False,status='newton_not_converged'), 'pandapower':dict(completed=False,status='reference_not_converged')})
    assert not row['passed'] and row['status']=='paired_nonconvergence'


def test_large_pf_does_not_expand_dense_state_estimation(monkeypatch):
    from power_grid_env.ac_estimation import MeasurementModel
    with pytest.raises(ValueError,match='128'):MeasurementModel(chain(),[])


def test_2048_bus_boundary_has_independent_terminal_current_balance():
    from tests.test_balanced_ac_workflows import independent_injections
    c=chain(2048);r=solve_power_flow(c,enforce_q_limits=False,tolerance_pu=1e-10)
    assert r['completed'] and r['linear_solver']['used']=='sparse'
    v=np.array(r['vm_pu'])*np.exp(1j*np.deg2rad(r['va_deg']))
    measured=independent_injections(c,v)
    specified=-np.array([complex(b.pd,b.qd) for b in c.buses])
    specified[0]+=complex(r['pg_mw'][0],r['qg_mvar'][0])
    assert max(abs(measured-specified))<1e-6
    assert r['linear_solver']['ybus_stored_values']<=3*2048
