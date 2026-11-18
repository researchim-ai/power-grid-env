"""Authored dimensioned thermal-observer parameters, not OEM data."""
from benchmarks.controlled_winding_cases import example as base
from itertools import product


def example(mode='equilibrium',fault=False):
    s=base('both',fault,mode);c=s['coupled'];c['winding_generator_profile']='GENCLS_TGOV1_SEXS_HEAT'
    c['generator_heat']=[dict(generator_id='g',capacity_j_per_k=10000.,ambient_w_per_k=20.,resistance_ohm=.01,core_loss_w=200.,alpha_per_c=.00393,
      ambient_c=20.,initial_c=80.,maximum_c=180.,initialization='equilibrium' if mode=='equilibrium' else 'specified',provenance='owner://generator-heat/authored-v1:assumed,not OEM')]
    return s


def jobs():
    for mode,fault in product(['passport','equilibrium'],[False,True]):yield dict(id=mode+'-'+('fault' if fault else 'hold'),study=example(mode,fault),fault=fault)


def refusals():
    for kind in ['policy','runaway','ceiling','flag']:
        s=example();h=s['coupled']['generator_heat'][0]
        if kind=='policy':h['initialization']='specified'
        elif kind=='runaway':h['ambient_w_per_k']=.01
        elif kind=='ceiling':h.update(maximum_c=30.,initial_c=20.)
        else:s['coupled']['winding_generator_profile']='GENCLS_TGOV1_SEXS'
        yield dict(id='negative-'+kind,study=s)
