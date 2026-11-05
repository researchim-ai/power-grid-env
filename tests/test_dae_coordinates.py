"""Rotating open-rotor integration against independent exact free-decay flows."""
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from scipy.linalg import expm
from power_grid_env.dae_solver import NetworkRadau,ShiftedSystem,statistics
from power_grid_env.dae_options import DAEOptions
from power_grid_env.induction_network import InductionNetwork
from power_grid_env.motor_thermal import MotorThermalProtection
from benchmarks.rms_scalability import example


def opened(cage):
    s=example(2);m=s.induction.motors[0];m.connected=False;m.fan_torque_pu=.6;m.inertia_s=1.5
    m.thermal=MotorThermalProtection(time_constant_s=2.,trip_level=10.,initial_level=.3)
    if cage=='double':m.model='double_cage';m.rotor2_r_pu=.06;m.rotor2_x_pu=.2
    model=InductionNetwork(s.network,s.induction);x=np.zeros(model.motor_state_end);x[:3]=[.2,.1,.8];x[model.thermal_indices[0]]=.3
    if cage=='double':x[model.cages.addresses[0]]=[.1,.02]
    return model,x


def exact(model,x,t):
    m=model.settings.motors[0];a=m.fan_torque_pu/(2*m.inertia_s);w=x[2];phase=-2*np.pi*model.network.frequency_hz*(t-np.log1p(a*w*t)/a)
    y=x.copy();y[2]=w/(1+a*w*t);y[model.thermal_indices[0]]=x[model.thermal_indices[0]]*np.exp(-t/m.thermal.time_constant_s)
    e=complex(*x[:2])
    if 0 in model.cages.addresses:
        address=model.cages.addresses[0];z=expm(model.cages.coefficients[0][1]*t)@np.array([e,complex(*x[address])])*np.exp(1j*phase);e=z[0];y[address]=[z[1].real,z[1].imag]
    else:e=e*np.exp(-t/model.constants[0][2]+1j*phase)
    y[:2]=[e.real,e.imag];return y


@pytest.mark.parametrize('cage',['single','double'])
def test_piecewise_frames_and_dense_output_match_exact_free_decay(cage):
    model,x=opened(cage);initial=x.copy();stats=statistics();cb=model.integration_jacobian()
    for left,right in zip(np.arange(30)*.1,np.arange(1,31)*.1):
        r=solve_ivp(model.rhs,(left,right),x,method=NetworkRadau,model=model,callback=cb,options=DAEOptions(),stats=stats,check_time=lambda:None,rtol=1e-9,atol=1e-11,max_step=.05,dense_output=True)
        assert r.success;model._dae_coordinates.physical_solution(r);mid=(left+right)/2
        assert np.allclose(r.sol(mid),exact(model,initial,mid),rtol=2e-6,atol=1e-7)
        x=r.y[:,-1]
    assert np.allclose(x,exact(model,initial,3.),rtol=2e-6,atol=1e-7)
    assert stats['accepted_steps']<5000 and stats['linear_iterations']<200000 and stats['rotating_coordinate_segments']==30


@pytest.mark.parametrize('cage',['single','double'])
def test_transformed_real_and_complex_jacobian_actions_match_finite_differences(cage):
    model,x=opened(cage);stats=statistics();solver=NetworkRadau(model.rhs,0.,x,.1,model=model,callback=model.integration_jacobian(),options=DAEOptions(),stats=stats,check_time=lambda:None)
    time=.073;frame=solver.coordinates;z=frame.encode(time,x);j=solver._snapshot(time,z);v=np.random.default_rng(7).normal(size=len(x));eps=1e-6
    def rate(q):return frame.encode(time,model.rhs(time,frame.decode(time,q)))-frame.rate(q)
    fd=(rate(z+eps*v)-rate(z-eps*v))/(2*eps)
    assert np.allclose(j.action(v),fd,rtol=2e-6,atol=2e-6)
    assert np.allclose(j.action(v+2j*v),j.action(v)*(1+2j),rtol=1e-12,atol=1e-12)


@pytest.mark.parametrize('complex_rhs',[False,True])
def test_independent_open_device_blocks_solve_without_krylov(monkeypatch,complex_rhs):
    model,x=opened('double');stats=statistics();solver=NetworkRadau(model.rhs,0.,x,.1,model=model,callback=model.integration_jacobian(),options=DAEOptions(),stats=stats,check_time=lambda:None)
    def forbidden(*a,**kw):raise AssertionError('independent local blocks must not need Krylov')
    monkeypatch.setattr('power_grid_env.dae_solver.gmres',forbidden)
    factor=ShiftedSystem(2000+3000j,solver.J);rhs=np.arange(1,len(x)+1,dtype=float)
    if complex_rhs:rhs=rhs*(1+.3j)
    value=factor.solve(rhs)
    assert np.linalg.norm(factor.action(value)-rhs)<=max(1e-14,1e-11*np.linalg.norm(rhs))
    assert stats['linear_iterations']==0 and stats['preconditioner_direct_solves']==1


def test_vectorized_physical_coordinates_preserve_real_and_complex_directions():
    model,x=opened('double')
    from power_grid_env.dae_coordinates import RotatingMotorCoordinates
    frame=RotatingMotorCoordinates(model,.2,x);axis=np.array([.2,.3,.4]);values=np.tile(x[:,None],(1,3))*(1+.5j)
    assert np.allclose(frame.encode(axis,frame.decode(axis,values)),values,rtol=1e-13,atol=1e-13)
