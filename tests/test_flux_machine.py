import numpy as np
import pytest
from power_grid_env.flux_machine import FluxMachinePassport,GammaWinding


def passport(**kw):
    d=dict(id='m',node_id='n',stator_r_ohm=3.7,unsaturated_h=.34,cages=[dict(resistance_ohm=2.5,leakage_h=.023)],pole_pairs=2,inertia_kg_m2=.03,
           provenance=dict(electrical='Aalto 2.2 kW Gamma passport',magnetics='authored test curve',mechanical='published inertia'))
    return FluxMachinePassport.model_validate(d|kw)


@pytest.mark.parametrize('beta',[0.,.84])
@pytest.mark.parametrize('cages',[1,2])
def test_energy_gradient_flux_and_differential_port_are_consistent(beta,cages):
    p=passport(saturation_beta_per_vs=beta,cages=[dict(resistance_ohm=2.5+i,leakage_h=.023+i*.01) for i in range(cages)])
    m=GammaWinding(p);current=np.array([2+.4j,*[-.3+.2j]*cages]);v=300+20j;speed=45.;q=m.quantities(current,speed,None)
    di,dw,_,_=m.derivatives(current,speed,None,v,q);eps=1e-7
    a=m.magnetic(current+eps*di);b=m.magnetic(current-eps*di)
    ds=(a[0]-b[0])/(2*eps);dr=(a[1]-b[1])/(2*eps)
    assert abs(ds-(v-p.stator_r_ohm*current[0]))<2e-5
    assert np.allclose(dr,q['rotor_flux_derivative'],atol=2e-5)
    de=(a[3]-b[3])/(2*eps)
    expected=1.5*(v*np.conj(current[0])).real-q['stator_copper_w']-q['rotor_copper_w']-q['torque_nm']*speed
    assert abs(de-expected)<1e-4
    assert q['energy_j']>=0 and min(np.linalg.eigvalsh(q['Y']))>0
