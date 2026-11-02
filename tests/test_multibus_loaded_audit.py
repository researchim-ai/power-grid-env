"""Independent current closure must distinguish generation and event sides."""
import copy
import numpy as np
import pytest
from benchmarks.multibus_loaded_cases import example
from benchmarks.multibus_operating_point import independent_circuit
from benchmarks.multibus_loaded_audit import current_closure


def packet(spec,scales=(1.,1.),times=(0.,1.2),sides=('regular','regular')):
    c=independent_circuit(spec);channels={};ports={node:dict(real_v=[],imag_v=[]) for node in c.nodes}
    settings=spec.coupled if spec.coupled else spec.induction
    for scale in scales:
        c.cfg['scale']=scale;c.rebuild();voltage,stator,_,_=c.electrical(c.state)
        for node,i in c.nodes.items():ports[node]['real_v'].append(float(voltage[i].real));ports[node]['imag_v'].append(float(voltage[i].imag))
        for m,p,current in zip(settings.motors,c.p,stator):
            s=3*voltage[p['bus']]*np.conj(current)
            channels.setdefault(m.id+'.p_w',[]).append(float(s.real));channels.setdefault(m.id+'.q_var',[]).append(float(s.imag))
        if c.generator:
            g=c.generator.g;s=3*voltage[c.generator_node]*np.conj(c.generator_current)
            channels.setdefault(g.id+'.p_w',[]).append(float(s.real));channels.setdefault(g.id+'.q_var',[]).append(float(s.imag))
    return dict(time_s=list(times),sample_side=list(sides),node_voltage=ports,channels=channels)


@pytest.mark.parametrize('name',['gen_sag','gen_lv_clock'])
def test_physical_KCL_uses_generation_as_injection(name):
    spec=example(name);report=packet(spec)
    assert current_closure(spec,report)<1e-10
    changed=copy.deepcopy(report)
    for key in ('g.p_w','g.q_var'):changed['channels'][key]=[-v for v in changed['channels'][key]]
    assert current_closure(spec,changed)>1e-4


def test_physical_KCL_keeps_original_source_clock_and_event_sides():
    spec=example('gen_sag');report=packet(spec,scales=(1.,1.,.5),times=(0.,.2,.2),sides=('regular','pre_event','post_event'))
    assert current_closure(spec,report)<1e-10
    changed=copy.deepcopy(report);changed['sample_side'][1]='post_event'
    assert current_closure(spec,changed)>1e-3


@pytest.mark.parametrize('value',[0.,float('nan')])
def test_missing_current_information_at_zero_or_nonfinite_voltage_rejects(value):
    spec=example('gen_hold');report=packet(spec)
    report['node_voltage']['lv_a']['real_v'][0]=value;report['node_voltage']['lv_a']['imag_v'][0]=value
    with pytest.raises(ValueError):current_closure(spec,report)
