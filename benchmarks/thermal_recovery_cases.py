"""Declared stator-I2 acknowledgement and guarded warm-standby recovery cases."""
from benchmarks.standby_interlocks_cases import example as base

SCENARIOS=('hot_reset','manual_retrip','ats_retrip','connected_reset',
           'minimum_open','close_deadline','operator_inhibit','double_reset')


def example(scenario='manual_retrip',mode='passport',reverse=False):
    if scenario not in SCENARIOS:raise ValueError('unknown recovery scenario')
    s=base('thermal_manual',mode);c=s['coupled']
    c['events']=[e for e in c['events'] if e['kind']=='source_voltage']
    c['generator_thermal'][0]['reset_level']=.01
    b=next(b for b in c['network_automation']['breakers'] if b['id']=='reserve-cb')
    b['synchronism']['minimum_live_voltage_pu']=.5
    if scenario=='minimum_open':b['minimum_open_s']=.08
    commands={
        'hot_reset':[(.085,'generator_thermal_reset','g'),(.11,'breaker_close','reserve-cb')],
        'manual_retrip':[(.105,'generator_thermal_reset','g'),(.11,'breaker_close','reserve-cb'),(.215,'breaker_close','reserve-cb')],
        'ats_retrip':[(.105,'generator_thermal_reset','g'),(.19,'ats_reset','ats')],
        'connected_reset':[(.0801,'generator_thermal_reset','g'),(.11,'breaker_close','reserve-cb')],
        'minimum_open':[(.105,'generator_thermal_reset','g'),(.11,'breaker_close','reserve-cb'),(.215,'breaker_close','reserve-cb')],
        'close_deadline':[(.215,'breaker_close','reserve-cb'),(.22,'generator_thermal_reset','g')],
        'operator_inhibit':[(.09,'breaker_open','reserve-cb'),(.105,'generator_thermal_reset','g'),(.19,'ats_reset','ats'),(.24,'breaker_close','reserve-cb')],
        'double_reset':[(.105,'generator_thermal_reset','g'),(.106,'generator_thermal_reset','g'),(.215,'breaker_close','reserve-cb')],
    }[scenario]
    c['events'] += [dict(time_s=t,kind=k,equipment_id=id_) for t,k,id_ in commands]
    if reverse:c['events'].reverse();c['network_automation']['breakers'].reverse()
    return s


def jobs():
    for scenario in SCENARIOS:
        for mode in ('passport','hot'):
            yield dict(id=mode+'-'+scenario,study=example(scenario,mode),scenario=scenario,mode=mode)
    for scenario in ('close_deadline','operator_inhibit'):
        for mode in ('passport','hot'):
            yield dict(id=mode+'-'+scenario+'-reversed',study=example(scenario,mode,True),scenario=scenario,mode=mode)
