"""CT/PT interruptions and explicit standby-generator transfer stress cases."""
from benchmarks.protected_topology_cases import example as protected
CT_CASES=('ct_interrupt','ct_reclose','ct_never_restore','pt_interrupt')
ATS_CASES=('ats_transfer','ats_primary_failure','ats_reserve_low','ats_sync_blocked','ats_recovered_dip')


def example(scenario='ct_interrupt',mode='equilibrium',reverse=False):
    if scenario in CT_CASES:
        s=protected('undervoltage' if scenario=='pt_interrupt' else 'very_inverse',mode)
        c=s['coupled'];loss,restore=(.142,.18) if scenario=='pt_interrupt' else (.105,.13) if scenario=='ct_reclose' else (.057,.085)
        c['events'].append(dict(time_s=loss,kind='relay_measurement_lost',equipment_id='primary-oc'))
        if scenario!='ct_never_restore':c['events'].append(dict(time_s=restore,kind='relay_measurement_restored',equipment_id='primary-oc'))
    elif scenario in ATS_CASES:
        s=protected('definite_reclose','passport');c=s['coupled'];c.update(duration_s=.3,events=[])
        s['network']['generators'][0]['p_w']=0.
        c['machines'][0]['governor'].update(VMIN=0.)
        c['machines'][0]['exciter'].update(EMIN=.5)
        if mode=='hot':
            c['motors'][0]['winding_thermal']['initial_temperature_c']=[110.,90.]
            c['generator_heat'][0]['initial_c']=100.
        c['network_automation']=dict(allow_islands=True,relays=[],breakers=[
            dict(id='grid-cb',target_kind='source',target_id='source',opening_delay_s=.005,fail_open=scenario=='ats_primary_failure',breaker_failure_delay_s=.01,breaker_failure_current_a=1.),
            dict(id='reserve-cb',target_kind='generator',target_id='g',initial_open=True,closing_delay_s=.005,synchronism=dict(maximum_angle_rad=1.,maximum_voltage_difference_pu=.5,maximum_frequency_difference_hz=.1 if scenario=='ats_sync_blocked' else 5.))],
            transfers=[dict(id='ats',primary_breaker_id='grid-cb',reserve_breaker_id='reserve-cb',primary_voltage=dict(kind='source',id='source'),reserve_voltage=dict(kind='generator',id='g'),detection_delay_s=.01,dead_time_s=.01)])
        c['events']=[dict(time_s=.05,kind='source_voltage',equipment_id='source',value=0.)]
        if scenario=='ats_recovered_dip':c['events'].append(dict(time_s=.055,kind='source_voltage',equipment_id='source',value=1.))
        if scenario=='ats_reserve_low':c['events'].append(dict(time_s=.055,kind='voltage_reference_step',equipment_id='g',value=-.8))
    else:raise ValueError('unknown scenario')
    if reverse:
        c['events'].reverse();c['network_automation']['relays'].reverse();c['network_automation']['breakers'].reverse()
    return s


def jobs():
    for scenario in CT_CASES:
        for mode in ('passport','equilibrium'):yield dict(id=mode+'-'+scenario,study=example(scenario,mode),mode=mode,scenario=scenario,reverse=False)
    for scenario in ATS_CASES:
        for mode in ('passport','hot'):yield dict(id=mode+'-'+scenario,study=example(scenario,mode),mode=mode,scenario=scenario,reverse=False)
    for mode in ('passport','equilibrium'):yield dict(id=mode+'-ct_interrupt-reversed',study=example('ct_interrupt',mode,True),mode=mode,scenario='ct_interrupt',reverse=True)
    for mode in ('passport','hot'):yield dict(id=mode+'-ats_transfer-reversed',study=example('ats_transfer',mode,True),mode=mode,scenario='ats_transfer',reverse=True)
