"""Authored controlled R(T) islands and checked manual reconnection."""
from itertools import product
from benchmarks.hybrid_stator_cases import example as base


def example(scenario='source_cycle',mode='equilibrium',reverse=False):
    if scenario not in {'source_cycle','line_cycle','dual_cycle','source_blocked'}:raise ValueError('unknown scenario')
    s=base(mode,False,1.);c=s['coupled'];c['generator_thermal'][0]['trip_level']=100.
    for b in c['network_automation']['breakers']:
        b.update(opening_delay_s=.005,closing_delay_s=.005,synchronism=dict(maximum_angle_rad=1.,maximum_voltage_difference_pu=.5,maximum_frequency_difference_hz=.001 if scenario=='source_blocked' else 2.))
    commands=[(.1,'breaker_open','primary-cb'),(.1,'breaker_open','backup-cb'),(.14,'breaker_close','backup-cb'),(.18,'breaker_close','primary-cb')] if scenario=='dual_cycle' else [(.1,'breaker_open','primary-cb' if scenario=='line_cycle' else 'backup-cb'),(.14,'breaker_close','primary-cb' if scenario=='line_cycle' else 'backup-cb')]
    c['events'] += [dict(time_s=t,kind=kind,equipment_id=id_) for t,kind,id_ in commands]
    if reverse:
        c['events'].reverse();c['network_automation']['relays'].reverse();c['network_automation']['breakers'].reverse()
    return s


def jobs():
    for mode,scenario in product(['passport','equilibrium'],['source_cycle','line_cycle','dual_cycle','source_blocked']):
        yield dict(id=mode+'-'+scenario,study=example(scenario,mode),mode=mode,scenario=scenario,reverse=False)
    for mode in ['passport','equilibrium']:
        yield dict(id=mode+'-dual_cycle-reversed',study=example('dual_cycle',mode,True),mode=mode,scenario='dual_cycle',reverse=True)
