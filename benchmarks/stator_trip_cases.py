"""Authored stator R(T)/physical heat/I2 protection permanent-opening cases."""
from itertools import product
from benchmarks.generator_stator_cases import example as base
from benchmarks.controlled_winding_cases import example as controls_base


def example(controls='both',mode='equilibrium'):
    s=base(mode,True);c=s['coupled'];p=c['machines'][0]
    if controls=='governor':p['exciter']=None
    if controls=='exciter':p['governor']=None
    if p.get('governor'):p['governor']['R']=.5
    c['generator_thermal']=[dict(generator_id='g',time_constant_s=.05,trip_level=.3 if mode=='passport' else 1.,initial_level=.2,initialization='specified' if mode=='passport' else 'equilibrium')]
    return s


def jobs():
    for controls,mode in product(['governor','exciter','both'],['passport','equilibrium']):
        yield dict(id=controls+'-'+mode,study=example(controls,mode),controls=controls,mode=mode)
