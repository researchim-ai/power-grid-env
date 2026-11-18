"""Opt-in automatic return with interrupted dwell, faults and explicit cancellation."""
from benchmarks.transfer_lifecycle_cases import example as base
SCENARIOS=('success','dwell_dip','pt_dwell','loss_before_open','loss_after_open','inflight_drop','reserve_fail_open','cancel_reset','cancel_after_open')


def example(scenario='success',mode='passport',reverse=False):
    if scenario not in SCENARIOS:raise ValueError('unknown scenario')
    s=base('retransfer',mode);c=s['coupled'];a=c['network_automation']
    c['events']=[e for e in c['events'] if e['kind']=='source_voltage']
    a['transfers'][0]['return_policy']=dict(restore_delay_s=.02,dead_time_s=.01,dropout_pu=.85)
    reserve=next(b for b in a['breakers'] if b['id']=='reserve-cb')
    if scenario=='reserve_fail_open':reserve.update(fail_open=True,breaker_failure_delay_s=.01,breaker_failure_current_a=1.)
    extra=[]
    if scenario=='dwell_dip':extra=[(.15,'source_voltage','source',0.),(.17,'source_voltage','source',1.)]
    if scenario=='pt_dwell':extra=[(.15,'transfer_primary_measurement_lost','ats',None),(.17,'transfer_primary_measurement_restored','ats',None)]
    if scenario=='loss_before_open':extra=[(.162,'source_voltage','source',0.),(.20,'source_voltage','source',1.),(.21,'return_reset','ats',None)]
    if scenario in ('loss_after_open','inflight_drop'):extra=[(.17 if scenario=='loss_after_open' else .177,'source_voltage','source',0.),(.23,'source_voltage','source',1.)]
    if scenario=='cancel_reset':extra=[(.162,'return_cancel','ats',None),(.19,'return_reset','ats',None)]
    if scenario=='cancel_after_open':extra=[(.17,'return_cancel','ats',None),(.22,'breaker_close','grid-cb',None)]
    c['events'] += [dict(time_s=t,kind=k,equipment_id=id_,**({'value':v} if v is not None else {})) for t,k,id_,v in extra]
    if reverse:c['events'].reverse();a['breakers'].reverse()
    return s


def jobs():
    for k in SCENARIOS:
        for mode in ('passport','hot'):yield dict(id=mode+'-'+k,study=example(k,mode),mode=mode,scenario=k,reverse=False)
    for k in ('success','cancel_reset'):
        for mode in ('passport','hot'):yield dict(id=mode+'-'+k+'-reversed',study=example(k,mode,True),mode=mode,scenario=k,reverse=True)
