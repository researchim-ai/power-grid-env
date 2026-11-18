"""Explicit generator stator I2 replica, not winding temperature."""
from benchmarks.controlled_winding_cases import example as base
from itertools import product


def example(controls='both',mode='equilibrium',fault=False):
    s=base(controls,fault,mode);c=s['coupled'];c['winding_generator_profile']='GENCLS_TGOV1_SEXS_I2'
    c['generator_thermal']=[dict(generator_id='g',time_constant_s=.1,trip_level=20.,initial_level=.2,initialization='equilibrium' if mode=='equilibrium' else 'specified')]
    return s


def jobs():
    for mode,fault in product(['passport','equilibrium'],[False,True]):
        yield dict(id=mode+'-'+('fault' if fault else 'hold'),study=example(mode=mode,fault=fault),fault=fault)


def refusals():
    for kind in ['flag','limit','policy']:
        s=example()
        if kind=='flag':s['coupled']['winding_generator_profile']='GENCLS_TGOV1_SEXS'
        elif kind=='limit':s['coupled']['generator_thermal'][0]['trip_level']=.01
        else:s['coupled']['generator_thermal'][0]['initialization']='specified'
        yield dict(id='negative-'+kind,study=s)
