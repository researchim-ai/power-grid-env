"""Supply-PT failures, CT during transfer, guarded return and explicit ATS retry."""
from benchmarks.measurement_transfer_cases import example as base
SCENARIOS=('primary_pt_pause','reserve_pt_before_request','reserve_pt_inflight','pt_retry','ct_during_transfer','retransfer','repeat_inhibited')


def example(scenario='primary_pt_pause',mode='passport',reverse=False):
    if scenario not in SCENARIOS:raise ValueError('unknown scenario')
    s=base('ats_transfer',mode);c=s['coupled'];a=c['network_automation']
    grid=next(b for b in a['breakers'] if b['id']=='grid-cb');reserve=next(b for b in a['breakers'] if b['id']=='reserve-cb')
    grid.update(closing_delay_s=.005,synchronism=dict(maximum_angle_rad=1.,maximum_voltage_difference_pu=.5,maximum_frequency_difference_hz=5.))
    reserve['opening_delay_s']=.005
    events=[]
    if scenario=='primary_pt_pause':events=[(.055,'transfer_primary_measurement_lost','ats',None),(.075,'transfer_primary_measurement_restored','ats',None)]
    if scenario=='reserve_pt_before_request':events=[(.07,'transfer_reserve_measurement_lost','ats',None),(.1,'transfer_reserve_measurement_restored','ats',None)]
    if scenario=='reserve_pt_inflight':events=[(.077,'transfer_reserve_measurement_lost','ats',None),(.1,'transfer_reserve_measurement_restored','ats',None)]
    if scenario=='pt_retry':
        reserve['synchronism']['minimum_live_voltage_pu']=.5
        events=[(.07,'transfer_reserve_measurement_lost','ats',None),(.076,'transfer_reserve_measurement_restored','ats',None),(.077,'ats_reset','ats',None)]
    if scenario=='ct_during_transfer':
        a['relays']=[dict(id='reserve-oc',kind='overcurrent',measurement_kind='generator',measurement_id='g',breaker_id='reserve-cb',pickup=60.,curve='definite',time_multiplier_s=.005)]
        events=[(.075,'relay_measurement_lost','reserve-oc',None),(.105,'relay_measurement_restored','reserve-oc',None)]
    if scenario in ('retransfer','repeat_inhibited'):
        events=[(.14,'source_voltage','source',1.),(.16,'breaker_close','grid-cb',None),(.18,'breaker_open','reserve-cb',None),(.21,'breaker_close','grid-cb',None),(.23,'ats_reset','ats',None)]
        if scenario=='repeat_inhibited':events.append((.25,'source_voltage','source',0.))
    c['events'] += [dict(time_s=t,kind=kind,equipment_id=id_,**({'value':value} if value is not None else {})) for t,kind,id_,value in events]
    if reverse:c['events'].reverse();a['breakers'].reverse();a['relays'].reverse()
    return s


def jobs():
    for scenario in SCENARIOS:
        for mode in ('passport','hot'):yield dict(id=mode+'-'+scenario,study=example(scenario,mode),scenario=scenario,mode=mode,reverse=False)
    for scenario in ('pt_retry','retransfer'):
        for mode in ('passport','hot'):yield dict(id=mode+'-'+scenario+'-reversed',study=example(scenario,mode,True),scenario=scenario,mode=mode,reverse=True)
