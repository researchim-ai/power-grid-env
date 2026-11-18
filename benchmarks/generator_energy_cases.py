"""Authored energy-consistent lumped-loss GENCLS cases."""
from benchmarks.generator_heat_cases import example as base
from itertools import product


def example(mode='equilibrium',fault=False):
    s=base(mode,fault);s['coupled']['winding_generator_profile']='GENCLS_TGOV1_SEXS_ENERGY';return s


def jobs():
    for mode,fault in product(['passport','equilibrium'],[False,True]):yield dict(id=mode+'-'+('fault' if fault else 'hold'),study=example(mode,fault),fault=fault)


def refusals():
    for kind in ['missing_heat','runaway','ceiling','policy']:
        s=example();h=s['coupled']['generator_heat'][0]
        if kind=='missing_heat':s['coupled']['generator_heat']=[]
        elif kind=='runaway':h['ambient_w_per_k']=.01
        elif kind=='ceiling':h.update(maximum_c=30.,initial_c=20.)
        else:h['initialization']='specified'
        yield dict(id='negative-'+kind,study=s)
