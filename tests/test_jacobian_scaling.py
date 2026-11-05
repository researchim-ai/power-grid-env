"""Exact blocked port solves, canonical defaults and CSC Radau delivery."""
from types import SimpleNamespace
import numpy as np
import pytest
from scipy.sparse import diags,isspmatrix_csc
from scipy.sparse.linalg import splu
from power_grid_env.network_sensitivity import port_response
from power_grid_env.jacobian_storage import configure
from tests.test_esst1a_jacobian import ready,difference


@pytest.mark.parametrize('width',[1,3,8,64])
def test_blocked_response_exactly_matches_full_solve(width):
    n=129;lu=splu(diags([np.full(n-1,-.1j),np.full(n,2.+1j),np.full(n-1,-.1j)],[-1,0,1],format='csc'));buses=np.array([2,2,7,13,44,70,92,100,128,0,40]);y=np.arange(len(buses))*.02+.2j;outputs=np.array([128,2,44,0,2]);sizes=[]
    def solve(rhs):sizes.append(rhs.shape);return lu.solve(rhs)
    model=SimpleNamespace(lu=SimpleNamespace(solve=solve),bus_names=list(range(n)))
    rhs=np.zeros((n,len(buses)),complex);rhs[buses,np.arange(len(buses))]=y
    result=port_response(model,buses,y,outputs,block_columns=width)
    assert np.array_equal(result,lu.solve(rhs)[outputs])
    assert max(s[1] for s in sizes)<=width and result.shape==(len(outputs),len(buses))


@pytest.mark.parametrize('width',[0,-1,True,2.5])
def test_bad_block_width_rejected(width):
    with pytest.raises(ValueError):port_response(SimpleNamespace(bus_names=[0]),[0],[1.],[0],block_columns=width)


def test_empty_response_does_not_solve():
    m=SimpleNamespace(bus_names=[0]);assert port_response(m,[],[],[0]).shape==(1,0)


@pytest.mark.parametrize('family',['classical','round','esst','oel','batch'])
def test_CSC_keeps_every_exact_entry_and_numerical_derivative(family):
    if family=='classical':
        from benchmarks.coupled_jacobian_validation import case;s=case('generator_step')
    if family=='round':
        from benchmarks.genrou_jacobian_validation import case;s=case('genrou_hold')
    if family=='esst':
        from benchmarks.generator_esst1a_cases import example;s=example('hold')
    if family=='oel':
        from benchmarks.generator_oel2c_cases import example;s=example('hold')
    if family=='batch':
        from benchmarks.oel2c_batch_validation import case;s=case('cold__two_limiters')
    m,x=ready(s);dense=m.integration_jacobian();wrapper=configure(dense,'csc');matrix=wrapper(0.,x)
    assert isspmatrix_csc(matrix) and matrix.has_sorted_indices
    assert np.max(abs(matrix.toarray()-dense(0.,x)))<2e-10
    assert wrapper.jacobian_kind==dense.jacobian_kind
    # Avoid clip-corner directional conventions; full derivative coverage for
    # those is supplied by the individual component suites.
    if family=='classical':assert np.max(abs(matrix.toarray()-difference(m,x)))<3e-5


def test_CSC_never_drops_tiny_nonzero_entries():
    a=np.array([[1.,1e-200],[0.,-2.]])
    callback=configure(lambda t,x:a,'csc');b=callback(0.,np.ones(2));assert b.nnz==3 and np.array_equal(a,b.toarray())


def test_no_analytic_callback_retains_finite_difference():assert configure(None,'csc') is None


@pytest.mark.parametrize('damage',['nan','shape','complex'])
def test_invalid_matrix_is_not_silently_accepted(damage):
    a=np.eye(2)
    if damage=='nan':a[0,0]=np.nan
    if damage=='shape':a=a[:1]
    if damage=='complex':a=a.astype(complex)+1j
    with pytest.raises(ValueError):configure(lambda t,x:a,'csc')(0.,np.ones(2))


def test_dense_default_preserves_original_case_hashes_and_CSC_is_bound():
    from benchmarks.ex03_cases import scale_case
    from power_grid_env.model import canonical_hash
    s=scale_case(64,4,1);before=s.model_dump(mode='json');assert 'jacobian_format' not in before['coupled']
    h=canonical_hash(s);s.coupled.jacobian_format='csc';assert s.model_dump(mode='json')['coupled']['jacobian_format']=='csc' and canonical_hash(s)!=h
    s.coupled.jacobian_format='dense';assert s.model_dump(mode='json')==before and canonical_hash(s)==h


def test_classical_cache_stores_only_port_rows():
    from benchmarks.coupled_jacobian_validation import case
    m,x=ready(case('scale_512_32_4'));m.integration_jacobian()(0.,x)
    assert m._classical_voltage_response[1].shape==(36,36)


def test_unknown_storage_is_rejected_by_input_schema():
    from benchmarks.ex03_cases import scale_case
    from power_grid_env.study_spec import StudySpec
    s=scale_case(64,4,1).model_dump(mode='json');s['coupled']['jacobian_format']='auto'
    with pytest.raises(ValueError):StudySpec.model_validate(s)


def test_unknown_storage_is_not_silently_dense():
    with pytest.raises(ValueError):configure(lambda t,x:np.eye(len(x)),'auto')


@pytest.mark.parametrize('storage',['dense','csc'])
@pytest.mark.parametrize('coupled',[False,True])
def test_actual_Radau_factorization_uses_selected_backend(monkeypatch,storage,coupled):
    from scipy.integrate._ivp import radau
    from benchmarks.rms_scalability import example
    from power_grid_env.study_spec import run_study
    s=example(16,coupled=coupled);(s.coupled or s.induction).jacobian_format=storage;calls=[];original=radau.splu
    def recorded(*args,**kwargs):calls.append(True);return original(*args,**kwargs)
    monkeypatch.setattr(radau,'splu',recorded);r=run_study(s)
    assert r['completed'] and r['integration']['jacobian_format']==storage
    assert bool(calls)==(storage=='csc')
