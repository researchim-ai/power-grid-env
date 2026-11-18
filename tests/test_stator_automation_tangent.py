"""R(T) generator current and inverse OC Schur products, independently differenced."""
import numpy as np
import pytest
from benchmarks.network_stator_cases import example
from tests.test_two_cage_winding import initial
from power_grid_env.automation_jacobian import current_directions
from power_grid_env.dae_linearization import DAEBlocks


@pytest.mark.parametrize('curve',['standard_inverse','very_inverse','extremely_inverse'])
@pytest.mark.parametrize('partial',[False,True])
@pytest.mark.parametrize('temperature_only',[False,True])
def test_inverse_relay_products_include_actual_impedance_and_temperature(curve,partial,temperature_only):
    s=example();r=next(r for r in s['coupled']['network_automation']['relays'] if r['id']=='primary-oc');r['curve']=curve
    m,x,_=initial(s);bank=m.automation;bank.initialize(x)
    i=next(i for i,r in enumerate(bank.relays) if r.id=='primary-oc');bank.active[i]=True;bank.relays[i].pickup=30.
    x[m.generator_heat.entries[0][0]]+=30.;u=np.random.default_rng(45).normal(size=len(x))*.02
    if temperature_only:u[:]=0.;u[m.generator_heat.entries[0][0]]=1.
    v=m.electrical(x)[0];dv=np.full(len(v),.03+.04j) if partial else None;h=2e-5
    cb=m.integration_jacobian()
    if partial:
        v=v+.04j
        actual=cb.dae_actions(.1,x,u[:,None],m.electrical_at_voltage(x,v),voltage_directions=dv[:,None])[:,0]
        expected=(m.differential_at_voltage(.1,x+h*u,v+h*dv)-m.differential_at_voltage(.1,x-h*u,v-h*dv))/(2*h)
    else:
        actual=cb.jacobian_actions(.1,x,u[:,None],m.electrical(x))[:,0]
        expected=(m.rhs(.1,x+h*u)-m.rhs(.1,x-h*u))/(2*h)
        blocks=DAEBlocks(m,cb,.1,x,16,lambda:None,lambda:None)
        np.testing.assert_allclose(blocks.action(u+1j*u),actual+1j*actual,rtol=1e-11,atol=1e-8)
    np.testing.assert_allclose(actual,expected,rtol=3e-6,atol=3e-6)
    assert abs(actual[bank.offset+i])>1e-7


def test_generator_port_temperature_partial_and_open_boundary():
    m,x,_=initial(example());bank=m.automation;r=next(r for r in bank.relays if r.id=='primary-oc')
    x[m.generator_heat.entries[0][0]]+=30.;u=np.zeros((len(x),1));u[m.generator_heat.entries[0][0]]=1.
    v=m.electrical(x)[0]+.04j;dv=np.zeros((len(v),1),complex);h=1e-4
    def current(z):return bank.port('generator','g',z,v)[1]
    expected=(current(x+h*u[:,0])-current(x-h*u[:,0]))/(2*h)
    actual=current_directions(bank,r,x,u,dv,voltage=v)[0]
    assert abs(expected)>.01
    assert actual==pytest.approx(expected,abs=1e-9)
    m.gen_connected[0]=False
    assert current_directions(bank,r,x,u,dv,voltage=v)[0]==0
