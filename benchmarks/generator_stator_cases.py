"""Authored stator R(T) network/energy profiles."""
from benchmarks.generator_energy_cases import example as base
from itertools import product


def example(mode='equilibrium',fault=False):
    s=base(mode,fault);s['coupled']['winding_generator_profile']='GENCLS_TGOV1_SEXS_STATOR_RT';return s


def jobs():
    for mode,fault in product(['passport','equilibrium'],[False,True]):yield dict(id=mode+'-'+('fault' if fault else 'hold'),study=example(mode,fault),fault=fault)


def refusals():
    for kind in ['ceiling','missing_heat','policy']:
        s=example()
        if kind=='ceiling':s['coupled']['generator_heat'][0].update(maximum_c=30.,initial_c=20.)
        elif kind=='missing_heat':s['coupled']['generator_heat']=[]
        else:s['coupled']['operating_point']['winding_state']='passport'
        yield dict(id='negative-'+kind,study=s)
