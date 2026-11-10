"""Check cached port assembly against independently assembled winding equations."""
import numpy as np
import pytest
from benchmarks.switching_thermal_cases import document
from power_grid_env.flux_machine import FluxMachinePassport
from power_grid_env.switching_magnetics import SwitchingMagnetics


@pytest.mark.parametrize('cages',[1,2,3,4])
@pytest.mark.parametrize('speed',[-180.,0.,180.])
def test_cached_port_keeps_every_cage_and_matches_full_newton_system(cages,speed):
    d=document('saturated_four_thermal');m=FluxMachinePassport.model_validate(d['motor']);m=m.model_copy(update={'cages':m.cages[:cages]});q=SwitchingMagnetics(m)
    rng=np.random.default_rng(791+cages);old=rng.normal(0,.2,q.size);guess=old+rng.normal(0,.001,q.size);h=1.7e-5;temperature=np.array([90.,70.])
    port=q.affine(old,h,speed,guess,temperature);rs,rr=q.resistances(temperature)
    matrix=q.linear_reluctance.copy();gradient,jac=q.radial_gradient(old[:2],guess[:2]);matrix[:2,:2]+=jac-np.eye(2)/m.unsaturated_h
    offset=np.zeros(q.size);offset[:2]=gradient-jac@guess[:2]
    physical=matrix.copy();physical[:2,:2]+=m.core_loss_s*2/h*np.eye(2)
    resistance=np.repeat(np.r_[rs,rr],2);left=2/h*np.eye(q.size)+resistance[:,None]*physical
    spin=m.pole_pairs*speed*np.array([[0.,-1.],[1.,0.]])
    rhs=-resistance*(matrix@old+offset)
    for k in range(1,q.n):left[2*k:2*k+2,2*k:2*k+2]-=spin;rhs[2*k:2*k+2]+=spin@old[2*k:2*k+2]
    B=np.vstack([np.eye(2),np.zeros((q.size-2,2))]);full=np.linalg.solve(left,np.column_stack([rhs,B]))
    np.testing.assert_allclose(port['offset_delta'],full[:,0],rtol=1e-12,atol=1e-14)
    np.testing.assert_allclose(port['gain_flux'],full[:,1:],rtol=1e-12,atol=1e-16)
    assert port['gain_flux'].shape==(2*(cages+1),2)
