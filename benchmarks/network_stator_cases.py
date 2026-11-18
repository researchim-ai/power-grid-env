"""Authored generator-port OC / I2 race with delayed breaker opening."""
from benchmarks.hybrid_stator_cases import example as base
from itertools import product


def example(scenario='relay_first',mode='equilibrium',reverse=False):
    if scenario not in {'relay_first','thermal_first','failed_open'}:raise ValueError('unknown scenario')
    s=base(mode,reverse,1.);a=s['coupled']['network_automation']
    b=next(b for b in a['breakers'] if b['id']=='primary-cb')
    b.update(target_kind='generator',target_id='g',opening_delay_s=.08 if scenario=='thermal_first' else .005,fail_open=scenario=='failed_open')
    r=next(r for r in a['relays'] if r['id']=='primary-oc')
    r.update(measurement_kind='generator',measurement_id='g',pickup=150.,curve='definite',time_multiplier_s=.005)
    if reverse:
        a['relays'].reverse();a['breakers'].reverse()
    return s


def jobs():
    for mode,scenario in product(['passport','equilibrium'],['relay_first','thermal_first','failed_open']):
        yield dict(id=mode+'-'+scenario,study=example(scenario,mode),scenario=scenario,mode=mode,reverse=False)
    for mode in ['passport','equilibrium']:
        yield dict(id=mode+'-thermal_first-reversed',study=example('thermal_first',mode,True),scenario='thermal_first',mode=mode,reverse=True)
