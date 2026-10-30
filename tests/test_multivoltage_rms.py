"""Voltage bases, vector groups and taps across the canonical RMS boundary."""
import numpy as np
import pytest
from power_grid_env.build import NetBuilder
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.rms_study import RMSSettings,ClassicalMachine,RMSEvent
from power_grid_env.model import parse_vector_group
from power_grid_env.backend_opendss import solve_network


def example(group='Dyn11', tap_hv=1., tap_lv=1.025, base=10e6):
    b=NetBuilder('multivoltage','Multivoltage',110.,s_base_va=base)
    b.node('hv');b.node('lv',10.);b.node('load',10.)
    b.source('hv',1.,5.)
    parsed=parse_vector_group(group)
    def ground(i):return 'not_applicable' if parsed.connections[i]=='delta' else ('solid' if parsed.neutral_solid[i] else 'ungrounded')
    b.transformer('hv','lv',vector_group=group,kva=10000,hv_kv=110,lv_kv=10,
                  x_pct=6.,r_pct=1.,hv_ground=ground(0),lv_ground=ground(1),transformer_id='t')
    b.line_z1('lv','load',.1+.2j,line_id='line')
    b.load('load',1e6,2e5,load_id='load')
    b.generator('hv',5e5,mode='pv',v_set_pu=1.,q_min_var=-20e6,q_max_var=20e6,generator_id='g')
    n=b.build();n.transformers[0].windings[0].tap_pu=tap_hv;n.transformers[0].windings[1].tap_pu=tap_lv
    n.transformers[0].ppm_antifloat=0.
    return StudySpec(id='multivoltage',study='rms',backend='andes',network=n,
       rms=RMSSettings(duration_s=.2,step_s=.005,machines=[ClassicalMachine(generator_id='g',rated_va=10e6,
           inertia_s=4.,x_d_prime_pu=.3)],events=[RMSEvent(time_s=.1,kind='load_scale',equipment_id='load',value=1.2)]))


@pytest.mark.parametrize('group,tap_hv,tap_lv',[('YNyn0',1.,1.),('Dyn11',1.,1.025),('YNd1',1.025,1.),('Dyn5',1.,1.),('YNd5',1.,1.)])
def test_transformer_pf_matches_independent_phase_backend(group,tap_hv,tap_lv):
    s=example(group,tap_hv,tap_lv)
    r=run_study(s);assert r['completed'],r
    phase=solve_network(s.network);assert phase.status=='converged',phase.diagnostics
    for v in phase.voltages:
        if v.phase=='A':
            assert r['initial']['bus_voltage_pu'][v.bus_id]==pytest.approx(abs(v.complex)/v.v_base_ln_v,abs=1e-5)
            angle=r['initial']['bus_angle_rad'][v.bus_id]
            assert abs(np.angle(np.exp(1j*(angle-np.angle(v.complex)))))<1e-4
    # Independent local transformer + line equation in physical SI units.
    # This checks the sign of the clock shift, not only agreement between adapters.
    from scipy.optimize import root
    from power_grid_env.topology import compile_topology
    topology=compile_topology(s.network)
    hv=next(v.complex for v in phase.voltages if v.phase=='A' and v.bus_id==topology.node_to_bus['hv'])
    shift={'YNyn0':0.,'Dyn11':30.,'YNd1':-30.,'Dyn5':-150.,'YNd5':-150.}[group]
    ratio=10/110*tap_lv/tap_hv*np.exp(1j*np.radians(shift))
    leakage=(.01+1j*.06)*110e3**2/10e6*tap_hv**2
    equivalent=leakage*abs(ratio)**2+.1+.2j
    emf=hv*ratio;power=(1e6+2e5j)/3
    def residual(x):
        v=x[0]+1j*x[1]; mismatch=v+equivalent*np.conj(power/v)-emf
        return [mismatch.real,mismatch.imag]
    solved=root(residual,[emf.real,emf.imag],tol=1e-10)
    assert solved.success
    expected=complex(*solved.x)
    actual=next(v.complex for v in phase.voltages if v.phase=='A' and v.bus_id==topology.node_to_bus['load'])
    assert abs(actual-expected)/(10e3/np.sqrt(3))<1e-5
    assert 't' in r['mapping']['transformers']
    assert abs(r['channels']['g.omega'][-1]-1)>1e-6


def test_nonzero_magnetization_and_three_windings_are_rejected():
    s=example();s.network.transformers[0].imag_pct=.1
    assert run_study(s)['reason']=='rms_scope'
    s=example();s.network.transformers[0].windings.append(s.network.transformers[0].windings[-1])
    assert run_study(s)['reason']=='rms_scope'


def test_transformer_system_base_and_explicit_winding_losses():
    a=example();b=example(base=5e6)
    # Independent explicit winding data must sum to the same loss percentage.
    b.network.transformers[0].windings[0].r_pct=.3
    b.network.transformers[0].windings[1].r_pct=.7
    r=run_study(a);other=run_study(b)
    assert r['completed'] and other['completed']
    assert r['initial']['bus_voltage_pu']==pytest.approx(other['initial']['bus_voltage_pu'],abs=1e-7)
    assert r['channels']['g.delta']==pytest.approx(other['channels']['g.delta'],abs=1e-7)
