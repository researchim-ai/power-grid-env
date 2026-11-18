"""Authored generator trip and permanent electrical opening studies."""
from itertools import product
from benchmarks.generator_replica_cases import example as base


def example(controls='both',mode='passport',fault=True):
    s=base(controls,mode,fault);c=s['coupled']
    if c['machines'][0].get('governor'):c['machines'][0]['governor']['R']=.5
    c['generator_thermal'][0].update(trip_level=.3 if mode=='passport' else 1.,initial_level=.2,time_constant_s=.05)
    return s


def jobs():
    for controls,mode in product(['governor','exciter','both'],['passport','equilibrium']):
        yield dict(id=controls+'-'+mode,study=example(controls,mode),controls=controls,mode=mode)
