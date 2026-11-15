"""Authored equilibrium seeds, mutual leakage and hold/fault cases."""
from itertools import product
from benchmarks.mixed_winding_cases import example as fixed_example


def example(mutual=.02,temperatures=(90.,70.),fault=False):
    s=fixed_example(mutual,temperatures,fault)
    s['coupled']['operating_point']['winding_state']='equilibrium'
    return s


def jobs():
    for mutual,temp,fault in product([0.,.02],[(20.,20.),(90.,70.)],[False,True]):
        yield dict(id=f'mutual{mutual:.2f}-seed{int(temp[0])}-'+('fault' if fault else 'hold'),study=example(mutual,temp,fault),fault=fault)


def refusals():
    for kind in ['cooling','ceiling','q_limit','relay_policy']:
        s=example();h=s['coupled']['motors'][0]['winding_thermal']
        if kind=='cooling':h.update(stator_ambient_w_per_k=0.,rotor_ambient_w_per_k=0.,cross_w_per_k=0.)
        elif kind=='ceiling':h.update(maximum_c=30.,initial_temperature_c=[20.,20.],stator_capacity_j_per_k=1e16,rotor_capacity_j_per_k=1e16)
        elif kind=='q_limit':s['network']['generators'][0].update(q_min_var=-1.,q_max_var=1.)
        else:s['coupled']['operating_point']['thermal_state']='passport'
        yield dict(id='negative-'+kind,study=s)
