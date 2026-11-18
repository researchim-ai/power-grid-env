"""Opt-in continuous recovery dwell and guarded break-before-make grid return.

Cancellation invalidates/removes only this policy's queued actions. It never
undoes a physical opening, clears operator inhibition or bypasses synchronism.
"""
import heapq

MODES={'armed':0.,'opening':1.,'closing':2.,'blocked':3.}


def configure(bank):
    bank.return_entries={p.id:(bank.end+i,p) for i,p in enumerate(p for p in bank.transfers if p.return_policy is not None)}
    bank.end+=len(bank.return_entries)
    bank.return_mode={id_:'armed' for id_ in bank.return_entries}
    bank.return_active={id_:False for id_ in bank.return_entries}
    bank.return_generation={id_:0 for id_ in bank.return_entries}


def token(bank,id_):return 'automatic_return:'+id_+':'+str(bank.return_generation[id_])


def log(events,time,kind,id_,**fields):events.append(dict(time_s=float(time),kind=kind,equipment_id=id_,**fields))


def health(bank,p,state,*,hysteresis=False):
    for side in ('primary','reserve'):
        if not bank.transfer_available[p.id][side]:return -1.,side+'_measurement_unavailable'
    for b in (p.primary_breaker_id,p.reserve_breaker_id):
        if b in bank.manual_inhibit:return -1.,'manual_inhibit'
    threshold=p.return_policy.dropout_pu if hysteresis else p.pickup_pu
    value=bank.supply(p.primary_voltage,state,bank.model.electrical(state)[0])
    return value-threshold,'primary_voltage_unavailable'


def eligible(bank,p):
    return not bank.position(bank.breakers[p.primary_breaker_id]) and bank.position(bank.breakers[p.reserve_breaker_id])


def refresh(bank,time,state,events):
    for id_,(address,p) in bank.return_entries.items():
        if bank.return_mode[id_]!='armed':continue
        old=bank.return_active[id_];margin,reason=health(bank,p,state,hysteresis=old)
        active=eligible(bank,p) and margin>=0
        if active==old:continue
        bank.return_active[id_]=active;state[address]=0.
        log(events,time,'return_recovery_started' if active else 'return_recovery_cancelled',id_,**({} if active else dict(reason=reason if eligible(bank,p) else 'topology_changed')))


def derivatives(bank,state,result):
    for id_,(address,p) in bank.return_entries.items():
        if bank.return_active[id_] and bank.return_mode[id_]=='armed':result[address-bank.offset]=1/p.return_policy.restore_delay_s


def guards(bank,ids,items):
    def add(identity,fn,direction):fn.terminal=True;fn.direction=direction;ids.append(identity);items.append(fn)
    for id_,(address,p) in bank.return_entries.items():
        if bank.return_mode[id_]!='armed' or not eligible(bank,p):continue
        if not all(bank.transfer_available[id_].values()) or any(b in bank.manual_inhibit for b in (p.primary_breaker_id,p.reserve_breaker_id)):continue
        def supply(time,x,p=p,id_=id_):return health(bank,p,x,hysteresis=bank.return_active[id_])[0]
        add(('return_health',id_),supply,-1 if bank.return_active[id_] else 1)
        if bank.return_active[id_]:
            def due(time,x,address=address):return x[address]-1.
            add(('return_due',id_),due,1)


def apply(bank,roots,time,state,events):
    ordered=sorted((k for k in roots if k[0] in ('return_health','return_due')),key=lambda k:(k[0]!='return_health',k[1]))
    for kind,id_ in ordered:
        address,p=bank.return_entries[id_]
        if kind=='return_health':
            bank.return_active[id_]=not bank.return_active[id_];state[address]=0.
            log(events,time,'return_recovery_started' if bank.return_active[id_] else 'return_recovery_cancelled',id_,**({} if bank.return_active[id_] else dict(reason='primary_voltage_unavailable')))
        else:
            if bank.return_mode[id_]!='armed' or not bank.return_active[id_] or not eligible(bank,p) or health(bank,p,state,hysteresis=True)[0]<0:continue
            bank.return_active[id_]=False;bank.return_mode[id_]='opening';bank.return_generation[id_]+=1;state[address]=1.
            cause=token(bank,id_)
            log(events,time,'automatic_return_started',id_,generation=bank.return_generation[id_])
            bank.request(p.reserve_breaker_id,False,time,cause,events)
            bank.enqueue(time+bank.breakers[p.reserve_breaker_id].opening_delay_s+p.return_policy.dead_time_s,'return_connect',id_,cause)


def remove_pending(bank,id_):
    owner='automatic_return:'+id_
    old=len(bank.queue);bank.queue=[q for q in bank.queue if q[4].rsplit(':',1)[0]!=owner];heapq.heapify(bank.queue)
    bank.keys={(t,kind,target) for t,_,kind,target,_ in bank.queue}
    return old-len(bank.queue)


def block(bank,time,state,events,id_,reason):
    bank.return_mode[id_]='blocked';bank.return_active[id_]=False;remove_pending(bank,id_)
    log(events,time,'automatic_return_blocked',id_,reason=reason)
    _,p=bank.return_entries[id_]
    # If primary power failed after reserve opening, restart the existing
    # forward ATS logic; every subsequent close still checks its own inputs.
    if reason=='primary_voltage_unavailable' and not bank.position(bank.breakers[p.primary_breaker_id]) and not bank.position(bank.breakers[p.reserve_breaker_id]) and all(bank.transfer_available[id_].values()) and not any(b in bank.manual_inhibit for b in (p.primary_breaker_id,p.reserve_breaker_id)):
        i=next(i for i,q in enumerate(bank.transfers) if q.id==id_)
        bank.ats_latched[i]=False;bank.ats_active[i]=False;state[bank.offset+len(bank.relays)+i]=0.
        log(events,time,'automatic_return_fallback_armed',id_)


def service(bank,time,state,events,kind,id_,cause):
    if kind!='return_connect':return False
    if id_ not in bank.return_entries or cause!=token(bank,id_) or bank.return_mode[id_]!='opening':return True
    _,p=bank.return_entries[id_]
    if bank.position(bank.breakers[p.reserve_breaker_id]):block(bank,time,state,events,id_,'reserve_not_open');return True
    margin,reason=health(bank,p,state,hysteresis=True)
    if margin<0:block(bank,time,state,events,id_,reason);return True
    bank.return_mode[id_]='closing';bank.request(p.primary_breaker_id,True,time,cause,events)
    return True


def gate(bank,time,state,events,kind,b,cause):
    if not cause.startswith('automatic_return:'):return True
    id_=next((id_ for id_ in bank.return_entries if cause==token(bank,id_)),None)
    if id_ is None:
        log(events,time,'breaker_'+kind,b.id,outcome='cancelled',reason='stale_return_cycle',cause=cause);return False
    _,p=bank.return_entries[id_];margin,reason=health(bank,p,state,hysteresis=True)
    if margin<0:
        log(events,time,'breaker_'+kind,b.id,outcome='blocked',reason=reason,cause=cause);block(bank,time,state,events,id_,reason);return False
    return True


def applied(bank,time,state,events,b,closed,cause):
    # A successful forward transfer permits another independent return cycle.
    for id_,(address,p) in bank.return_entries.items():
        if b.id==p.reserve_breaker_id and closed and cause=='transfer:'+id_:
            bank.return_mode[id_]='armed';bank.return_active[id_]=False;state[address]=0.
        if b.id==p.primary_breaker_id and closed and cause==token(bank,id_):
            i=next(i for i,q in enumerate(bank.transfers) if q.id==id_)
            bank.ats_latched[i]=False;bank.ats_active[i]=False;state[bank.offset+len(bank.relays)+i]=0.
            bank.return_mode[id_]='armed';bank.return_active[id_]=False;state[address]=0.
            log(events,time,'automatic_return_completed',id_,generation=bank.return_generation[id_])


def close_failed(bank,time,state,events,cause,reason):
    for id_ in bank.return_entries:
        if cause==token(bank,id_):block(bank,time,state,events,id_,reason);return


def command(bank,event,time,state,events):
    if event.kind not in ('return_reset','return_cancel'):return None
    address,p=bank.return_entries[event.equipment_id];removed=remove_pending(bank,p.id)
    bank.return_generation[p.id]+=1;bank.return_mode[p.id]='armed' if event.kind=='return_reset' else 'blocked'
    bank.return_active[p.id]=False;state[address]=0.
    return dict(outcome='applied',cancelled_actions=removed,generation=bank.return_generation[p.id])


def channels(bank):return [id_+'.return.'+key for id_ in bank.return_entries for key in ('progress','active','mode','generation')]


def signals(bank,state):
    return {id_+'.return.'+key:float(value) for id_,(address,p) in bank.return_entries.items() for key,value in
            [('progress',state[address]),('active',bank.return_active[id_]),('mode',MODES[bank.return_mode[id_]]),('generation',bank.return_generation[id_])]}
