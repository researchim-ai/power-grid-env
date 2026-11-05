"""Column assembly and all supported analytic profiles without a state square."""
import numpy as np
import pytest
from scipy.sparse import isspmatrix_csc
from power_grid_env.jacobian_storage import configure
from power_grid_env.sparse_jacobian import assemble
from tests.test_esst1a_jacobian import ready


def profile(name):
    if name=='motor':
        from benchmarks.rms_scalability import example
        from power_grid_env.induction_network import InductionNetwork
        s=example(32);m=InductionNetwork(s.network,s.induction);return m,np.zeros(m.motor_state_end)
    if name=='classical':
        from benchmarks.coupled_jacobian_validation import case;s=case('generator_thermal_trip')
    if name=='round':
        from benchmarks.genrou_jacobian_validation import case;s=case('genrou_limits_recovery')
    if name=='pss':
        from benchmarks.pss_droop_jacobian_validation import case;s=case('joint_hold')
    if name=='esst':
        from benchmarks.esst1a_jacobian_validation import case;s=case('no_uel_pi_overload_recovery')
    if name=='thermal':
        from benchmarks.generator_underexcitation_cases import example;s=example('field_trip')
    if name=='oel':
        from benchmarks.generator_oel2c_regulating_cases import example;s=example('voltage_up')
    if name=='batch':
        from benchmarks.oel2c_batch_validation import case;s=case('cold__four_machines')
    return ready(s)


@pytest.mark.parametrize('name',['motor','classical','round','pss','esst','thermal','oel','batch'])
@pytest.mark.parametrize('width',[1,7,32])
def test_all_profile_blocks_match_dense(name,width):
    m,x=profile(name);callback=m.integration_jacobian();dense=callback(0.,x);matrix=assemble(callback,0.,x,block_columns=width)
    assert isspmatrix_csc(matrix) and matrix.has_sorted_indices
    assert np.max(abs(matrix.toarray()-dense))<2e-10
    assert configure(callback,'csc').jacobian_assembly=='column_blocks'


@pytest.mark.parametrize('outage',['motor','generator','fault'])
def test_mixed_machine_sparse_topology_and_heat(outage):
    from benchmarks.esst1a_jacobian_validation import case
    from power_grid_env.motor_thermal import MotorThermalProtection
    s=case('scale_512_32_4');s.coupled.motors=s.coupled.motors[:3]
    m0=s.coupled.motors[0];m0.model='double_cage';m0.rotor2_r_pu=.07;m0.rotor2_x_pu=.12;m0.thermal=MotorThermalProtection(time_constant_s=3.,trip_level=10.)
    m,x=ready(s);m.connected[:]=True;m.factorize()
    # Warm both paths before changing topology, then require a fresh LU.
    before=m.integration_jacobian();assemble(before,0.,x,block_columns=7);before(0.,x);old_lu=m.lu
    if outage=='motor':m.connected[0]=False
    if outage=='generator':m.gen_connected[0]=False
    if outage=='fault':m.fault_y[m.gen_buses[0]]=.1-.2j
    m.factorize();assert m.lu is not old_lu;callback=m.integration_jacobian()
    assert np.max(abs(assemble(callback,0.,x,block_columns=7).toarray()-callback(0.,x)))<2e-10


def test_streamed_callback_never_calls_dense_callback(monkeypatch):
    m,x=profile('esst');callback=m.integration_jacobian()
    def forbidden(*a):pytest.fail('dense callback used')
    class OnlyColumns:
        jacobian_columns=staticmethod(callback.jacobian_columns)
        jacobian_context=staticmethod(callback.jacobian_context)
        __call__=forbidden
    assert np.isfinite(configure(OnlyColumns(),'csc')(0.,x).data).all()


@pytest.mark.parametrize('bad',[[0,0],[-1],[100000],[.5]])
def test_column_inventory_rejected(bad):
    m,x=profile('round');cb=m.integration_jacobian();ctx=cb.jacobian_context(0.,x)
    with pytest.raises(ValueError):cb.jacobian_columns(0.,x,bad,ctx)


@pytest.mark.parametrize('width',[0,-1,True,2.5])
def test_invalid_block_width_rejected(width):
    m,x=profile('round')
    with pytest.raises(ValueError):assemble(m.integration_jacobian(),0.,x,block_columns=width)


def test_no_state_square_or_full_identity_allocated(monkeypatch):
    m,x=profile('esst');cb=m.integration_jacobian();n=len(x);eye=np.eye;zeros=np.zeros
    def checked_eye(a,*args,**kwargs):
        assert a!=n,'full state identity allocated';return eye(a,*args,**kwargs)
    def checked_zeros(shape,*args,**kwargs):
        assert shape!=(n,n),'full state Jacobian allocated';return zeros(shape,*args,**kwargs)
    monkeypatch.setattr(np,'eye',checked_eye);monkeypatch.setattr(np,'zeros',checked_zeros)
    assert np.isfinite(assemble(cb,0.,x,block_columns=7).data).all()


def test_context_evaluated_once_and_columns_are_bounded():
    n=103;calls=[];contexts=[]
    class Callback:
        @staticmethod
        def jacobian_context(t,x):contexts.append(True);return 'frozen'
        @staticmethod
        def jacobian_columns(t,x,cols,ctx):
            assert ctx=='frozen';calls.append(len(cols));a=np.zeros((n,len(cols)));a[cols,np.arange(len(cols))]=1.;a[0,0]=1e-200;return a
    m=assemble(Callback(),0.,np.ones(n),block_columns=16)
    assert len(contexts)==1 and max(calls)<=16 and m[0,0]==1e-200


def test_motor_double_cage_and_heat_rows_match_dense():
    from benchmarks.coupled_jacobian_validation import case
    from power_grid_env.motor_thermal import MotorThermalProtection
    s=case('double_cage_start');s.coupled.motors[0].thermal=MotorThermalProtection(time_constant_s=3.,trip_level=10.)
    m,x=ready(s);x[0:3]=[.3,.1,.8];x[m.cages.addresses[0]]=[.05,.02]
    cb=m.integration_jacobian();assert np.max(abs(assemble(cb,0.,x,block_columns=7).toarray()-cb(0.,x)))<2e-10


@pytest.mark.parametrize('name',['max_genrou_8_32','max_esst_8_32'])
def test_eight_generator_full_coordinate_RHS_derivatives(name):
    from benchmarks.sparse_jacobian_validation import case
    from tests.test_esst1a_jacobian import difference
    m,x=ready(case(name));m.connected[:]=True;m.factorize()
    for i in range(32):x[i*3:i*3+3]=[.3,.1,.8]
    for _,a in m.cages.addresses.items():x[a]=[.05,.02]
    for _,(a,p,k) in m.oel2c.entries.items():x[a+4]=-.07;x[a+6]=.01;x[a+7]=.01
    for _,(a,p) in m.underexcitation_limiters.entries.items():x[a]=p.MEL_MAX*.3
    matrix=assemble(m.integration_jacobian(),0.,x).toarray()
    assert len(x)>=400 and np.max(abs(matrix-difference(m,x)))<4e-5


@pytest.mark.parametrize('controlled',[False,True])
def test_mixed_GENCLS_GENROU_column_blocks(controlled):
    from benchmarks.ex03_cases import scale_case
    from power_grid_env.rms_study import ClassicalMachine
    s=scale_case(512,32,4);s.coupled.motors=s.coupled.motors[:3];p=s.coupled.machines[0]
    s.coupled.machines[0]=ClassicalMachine(generator_id=p.generator_id,rated_va=p.rated_va,inertia_s=p.inertia_s,x_d_prime_pu=p.x_d_prime_pu,damping_pu=p.damping_pu,governor=p.governor if controlled else None,exciter=p.exciter if controlled else None)
    m,x=ready(s);cb=m.integration_jacobian()
    assert np.max(abs(assemble(cb,0.,x,block_columns=7).toarray()-cb(0.,x)))<2e-10
