"""Authored unified limiter and generator-I2-trip pulses with stator R(T)."""
from benchmarks.stator_limiter_cases import example as base
from itertools import product


def example(mode='equilibrium',reverse=False,lead=1.):
    s=base('both',mode,reverse);c=s['coupled'];c['machines'][0]['governor']['R']=.5;c['machines'][0]['exciter']['TATB']=lead;c['generator_thermal']=[dict(generator_id='g',time_constant_s=.05,trip_level=1.8,initial_level=.2,initialization='equilibrium' if mode=='equilibrium' else 'specified')]
    return s


def jobs():
    for mode,lead in product(['passport','equilibrium'],[.1,1.]):
        yield dict(id=mode+'-lead'+str(lead),study=example(mode,False,lead),mode=mode,controls='both',lead=lead,reverse=False)
    for mode in ['passport','equilibrium']:
        yield dict(id=mode+'-lead1.0-reversed',study=example(mode,True,1.),mode=mode,controls='both',lead=1.,reverse=True)
