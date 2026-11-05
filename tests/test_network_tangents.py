"""Direct network elimination and exact matrix-free continuous products."""
from types import SimpleNamespace
import numpy as np
import pytest
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import splu
from power_grid_env.network_sensitivity import directional_response
from power_grid_env.jacobian_product import product
from power_grid_env.sparse_jacobian import assemble
from tests.test_sparse_jacobian import profile

PROFILES=['motor','classical','round','pss','esst','thermal','oel','batch']


@pytest.mark.parametrize('width',[1,7,19])
def test_duplicate_bus_injections_against_independent_dense_solve(width):
    rng=np.random.default_rng(24);a=rng.normal(size=(13,13))+1j*rng.normal(size=(13,13));a=a@a.conj().T+np.eye(13)
    lu=splu(csc_matrix(a));sizes=[]
    def solve(rhs):sizes.append(rhs.shape);return lu.solve(rhs)
    m=SimpleNamespace(bus_names=list(range(13)),lu=SimpleNamespace(solve=solve));b=np.array([0,4,4,4,12]);y=np.array([1.,0.,2j,-1j,3.]);u=rng.normal(size=(5,width))+1j*rng.normal(size=(5,width));out=np.array([4,0,4,12])
    rhs=np.zeros((13,width),complex)
    for i,bus in enumerate(b):rhs[bus]+=y[i]*u[i]
    assert np.allclose(directional_response(m,b,y,out,u),np.linalg.solve(a,rhs)[out],rtol=2e-13,atol=2e-13)
    assert max(s[1] for s in sizes)<=8


@pytest.mark.parametrize('name',PROFILES)
@pytest.mark.parametrize('width',[1,11])
def test_all_profile_actions_equal_dense_and_preserve_inputs(name,width):
    m,x=profile(name);cb=m.integration_jacobian();rng=np.random.default_rng(81);u=rng.normal(size=(len(x),width));before=x.copy();saved=u.copy();dense=cb(0.,x)
    actual=product(cb,0.,x,u)
    assert np.allclose(actual,dense@u,rtol=2e-11,atol=2e-9),np.max(abs(actual-dense@u))
    assert np.array_equal(x,before) and np.array_equal(u,saved)
    assert np.allclose(product(cb,0.,x,u[:,0]),actual[:,0],rtol=2e-11,atol=2e-9)


@pytest.mark.parametrize('name',PROFILES)
def test_direct_columns_and_actions_never_build_port_square(name,monkeypatch):
    m,x=profile(name);expected=m.integration_jacobian()(0.,x)
    m._motor_current_response=None;m._round_voltage_response=None;m._classical_voltage_response=None
    m.settings.jacobian_format='csc';m.settings.jacobian_network='direct_solve';cb=m.integration_jacobian()
    import power_grid_env.network_sensitivity as sensitivity
    def forbidden(*a,**kw):pytest.fail('port-square network response requested')
    monkeypatch.setattr(sensitivity,'port_response',forbidden)
    eye=np.eye
    def checked_eye(n,*a,**kw):
        assert n!=len(x),'state-square identity requested';return eye(n,*a,**kw)
    monkeypatch.setattr(np,'eye',checked_eye)
    assert np.allclose(assemble(cb,0.,x).toarray(),expected,rtol=2e-11,atol=2e-9)
    u=np.random.default_rng(9).normal(size=(len(x),3));assert np.allclose(product(cb,0.,x,u),expected@u,rtol=2e-11,atol=2e-9)
    assert m._motor_current_response is None and m._round_voltage_response is None and m._classical_voltage_response is None


@pytest.mark.parametrize('outage',['motor','generator','fault'])
def test_direct_path_tracks_current_topology_and_action_context(outage):
    m,x=profile('esst');cb=m.integration_jacobian();u=np.random.default_rng(4).normal(size=len(x));product(cb,0.,x,u)
    if outage=='motor':m.connected[0]=False
    elif outage=='generator':m.gen_connected[0]=False
    else:m.fault_y[m.gen_buses[0]]=.1-.2j
    m.factorize()
    assert np.allclose(product(cb,0.,x,u),cb(0.,x)@u,rtol=2e-11,atol=2e-9)


def test_matrix_free_motor_product_against_centered_physical_rhs():
    m,x=profile('motor');m.connected[:]=True;m.factorize();x[0::3][:32]=.2
    u=np.random.default_rng(41).normal(size=len(x));eps=1e-6
    fd=(m.rhs(0.,x+eps*u)-m.rhs(0.,x-eps*u))/(2*eps)
    assert np.allclose(product(m.integration_jacobian(),0.,x,u),fd,rtol=2e-7,atol=2e-6)


@pytest.mark.parametrize('bad',[np.nan,np.inf,1j])
def test_bad_time_rejected(bad):
    m,x=profile('motor')
    with pytest.raises(ValueError):product(m.integration_jacobian(),bad,x,np.ones(len(x)))


@pytest.mark.parametrize('width',[0,-1,True,9,1.5])
def test_invalid_product_block_width(width):
    m,x=profile('motor')
    with pytest.raises(ValueError):product(m.integration_jacobian(),0.,x,np.ones(len(x)),block_columns=width)


@pytest.mark.parametrize('bad',['length','complex','nan','empty','rank'])
def test_invalid_directions(bad):
    m,x=profile('motor');u=np.ones(len(x))
    if bad=='length':u=u[:-1]
    elif bad=='complex':u=u.astype(complex)
    elif bad=='nan':u[0]=np.nan
    elif bad=='empty':u=np.empty((len(x),0))
    else:u=u[None,None,:]
    with pytest.raises(ValueError):product(m.integration_jacobian(),0.,x,u)


def test_no_unadvertised_dense_fallback():
    with pytest.raises(ValueError,match='callback required'):product(lambda t,x:np.eye(len(x)),0.,np.ones(3),np.ones(3))


def test_default_case_hashes_and_explicit_direct_mode_identity():
    from benchmarks.eight_machine_validation import case,SOURCE_CASE_HASHES
    from power_grid_env.model import canonical_hash
    from power_grid_env.study_spec import StudySpec
    from pydantic import ValidationError
    for name,expected in SOURCE_CASE_HASHES.items():
        s=case(name);assert canonical_hash(s)==expected
        s.coupled.jacobian_network='direct_solve';assert canonical_hash(s)!=expected
        assert StudySpec.model_validate(s.model_dump(mode='json')).coupled.jacobian_network=='direct_solve'
    s.coupled.jacobian_format='dense'
    with pytest.raises(ValidationError):StudySpec.model_validate(s.model_dump(mode='json'))


@pytest.mark.parametrize('what',['bus','admittance','directions','width'])
def test_invalid_network_tangent_rejected(what):
    m=SimpleNamespace(bus_names=range(2),lu=SimpleNamespace(solve=lambda rhs:rhs));b=np.array([0]);y=np.array([1.]);u=np.ones((1,1));width=8
    if what=='bus':b=np.array([2])
    elif what=='admittance':y[0]=np.inf
    elif what=='directions':u[0]=np.nan
    else:width=9
    with pytest.raises(ValueError):directional_response(m,b,y,np.array([0]),u,block_columns=width)


@pytest.mark.parametrize('callback_kind',['none','dense','tagged_dense'])
def test_direct_request_never_silently_selects_finite_differences(monkeypatch,callback_kind):
    from benchmarks.rms_scalability import example
    from power_grid_env.induction_network import InductionNetwork
    from power_grid_env.study_spec import run_study
    s=example(8);s.induction.jacobian_format='csc';s.induction.jacobian_network='direct_solve'
    cb=None if callback_kind=='none' else lambda t,x:np.zeros((len(x),len(x)))
    if callback_kind=='tagged_dense':cb.jacobian_network_response='direct_solve'
    monkeypatch.setattr(InductionNetwork,'integration_jacobian',lambda self:cb)
    a=run_study(s);assert not a['completed'] and a['reason']=='direct_network_tangent_requires_analytic_profile'


@pytest.mark.parametrize('name',['max_genrou_8_32','max_esst_8_32','max_genrou_long_8_32','motor_trip_latch'])
def test_direct_native_inputs_preserve_physics_and_reference_scope(name):
    from benchmarks.network_tangent_native_validation import case
    from benchmarks.eight_machine_validation import case as baseline
    from power_grid_env.model import canonical_hash
    from benchmarks.eight_machine_circuit import PROFILE
    from benchmarks.esst1a_network import scope as esst_scope
    from benchmarks.oel2c_model_exchange import scope as oel_scope
    s=case(name);assert s.coupled.jacobian_network=='direct_solve'
    (esst_scope if s.coupled.esst1a_exciters else oel_scope)(s,profile=PROFILE)
    s.coupled.jacobian_network='cached_ports';assert canonical_hash(s)==canonical_hash(baseline(name))


def test_loaded_motor_stability_and_reclose_do_not_build_port_square(monkeypatch):
    from benchmarks.network_tangent_validation import case
    from power_grid_env.study_spec import run_study
    import power_grid_env.network_sensitivity as sensitivity
    s=case('motor_coast');s.induction.jacobian_network='direct_solve'
    def forbidden(*a,**kw):pytest.fail('loaded stability used a port-square response')
    monkeypatch.setattr(sensitivity,'port_response',forbidden)
    r=run_study(s);assert r['completed'],r
    assert r['integration']['jacobian_network_response']=='direct_solve'
    assert r['operating_point']['connected_machine_spectral_abscissa_per_s']< -1e-7
    assert [e['kind'] for e in r['event_log'] if e['kind'] in ['motor_open','motor_close']]==['motor_open','motor_close']
