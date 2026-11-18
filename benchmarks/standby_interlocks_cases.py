"""Initial PT faults and persistent generator thermal interlock, authored cases."""
from benchmarks.automatic_return_cases import example as base

SCENARIOS = ('primary_restore', 'reserve_restore', 'reserve_retry',
             'both_restore', 'primary_missing', 'reserve_missing',
             'thermal_manual', 'thermal_retry')


def example(scenario='primary_restore', mode='passport', reverse=False):
    if scenario not in SCENARIOS:
        raise ValueError('unknown standby interlock scenario')
    s = base('success', mode)
    c = s['coupled']
    p = c['network_automation']['transfers'][0]
    extra = []
    if scenario in ('primary_restore', 'both_restore', 'primary_missing'):
        p['initial_primary_measurement_available'] = False
    if scenario in ('reserve_restore', 'reserve_retry', 'both_restore', 'reserve_missing'):
        p['initial_reserve_measurement_available'] = False
    if scenario in ('primary_restore', 'both_restore'):
        extra.append((.07 if scenario == 'primary_restore' else .06,
                      'transfer_primary_measurement_restored', 'ats'))
    if scenario in ('reserve_restore', 'reserve_retry', 'both_restore'):
        extra.append((.09 if scenario == 'reserve_retry' else .065 if scenario == 'both_restore' else .07,
                      'transfer_reserve_measurement_restored', 'ats'))
    if scenario == 'reserve_retry':
        extra.append((.10, 'ats_reset', 'ats'))
    if scenario.startswith('thermal_'):
        c['events'] = [e for e in c['events'] if e['time_s'] != .14]
        c['generator_thermal'][0].update(time_constant_s=.02, trip_level=.03, initial_level=0.)
        extra += ([(.12, 'breaker_close', 'reserve-cb'), (.24, 'breaker_close', 'reserve-cb')]
                  if scenario == 'thermal_manual' else
                  [(.12, 'ats_reset', 'ats'), (.20, 'ats_reset', 'ats')])
    c['events'] += [dict(time_s=t, kind=k, equipment_id=target) for t, k, target in extra]
    if reverse:
        c['events'].reverse()
        c['network_automation']['breakers'].reverse()
    return s


def jobs():
    for scenario in SCENARIOS:
        for mode in ('passport', 'hot'):
            yield dict(id=mode+'-'+scenario, study=example(scenario, mode),
                       mode=mode, scenario=scenario, reverse=False)
    for scenario in ('both_restore', 'thermal_retry'):
        for mode in ('passport', 'hot'):
            yield dict(id=mode+'-'+scenario+'-reversed', study=example(scenario, mode, True),
                       mode=mode, scenario=scenario, reverse=True)
