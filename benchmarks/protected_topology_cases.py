"""Authored active protection, breaker backup and bounded auto-reclose profiles."""
from itertools import product
from benchmarks.topology_stator_cases import example as base

SCENARIOS=('definite_reclose','standard_inverse','very_inverse','extremely_inverse','failure_backup','manual_inhibit','sync_blocked','undervoltage')


def example(scenario='definite_reclose',mode='equilibrium',reverse=False):
    if scenario not in SCENARIOS:raise ValueError('unknown scenario')
    s=base('line_cycle',mode);c=s['coupled'];c.update(rtol=2e-11,atol=2e-12);a=c['network_automation']
    c['events']=[e for e in c['events'] if not e['kind'].startswith('breaker_')]
    b=next(b for b in a['breakers'] if b['id']=='primary-cb');r=next(r for r in a['relays'] if r['id']=='primary-oc')
    inverse=scenario in ('standard_inverse','very_inverse','extremely_inverse')
    r.update(measurement_kind='generator',measurement_id='g',pickup=150.,curve=scenario if inverse else 'definite',time_multiplier_s=(.0005 if scenario=='very_inverse' else .0002) if inverse else .005)
    b.update(maximum_reclose_shots=0 if scenario in ('failure_backup','undervoltage') else 1,reclose_dead_s=.04)
    if scenario=='failure_backup':b.update(fail_open=True,breaker_failure_delay_s=.01,breaker_failure_current_a=1.,backup_breaker_ids=['backup-cb'])
    if scenario=='manual_inhibit':c['events'].append(dict(time_s=.08,kind='breaker_open',equipment_id=b['id']))
    if scenario=='sync_blocked':b['synchronism']['maximum_frequency_difference_hz']=.001
    if scenario=='undervoltage':
        r.update(kind='undervoltage',measurement_kind='node',measurement_id='n1',pickup=.94,time_multiplier_s=.005)
        c['events'].append(dict(time_s=.1,kind='voltage_reference_step',equipment_id='g',value=-.5))
    if reverse:
        c['events'].reverse();a['breakers'].reverse();a['relays'].reverse()
    return s


def jobs():
    for mode,scenario in product(['passport','equilibrium'],SCENARIOS):
        yield dict(id=mode+'-'+scenario,study=example(scenario,mode),mode=mode,scenario=scenario,reverse=False)
    for mode in ['passport','equilibrium']:
        yield dict(id=mode+'-very_inverse-reversed',study=example('very_inverse',mode,True),mode=mode,scenario='very_inverse',reverse=True)
