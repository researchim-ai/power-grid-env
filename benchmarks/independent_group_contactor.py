"""Independent multi-motor hybrid SI driver with atomic group actions.

No production control or network solver is used. Voltage and thermal-state
addresses come from the independent winding circuit, not production layout.
"""
import math
import numpy as np
from scipy.integrate import solve_ivp


def run(*,settings,state,rhs,voltage,heating,thermal_indices,get_context,set_context):
    motors=settings.motors;cfg=get_context();controls={};time=0.;log=[];segments=[];changes=[];trips={};transitions=0
    for i,m in enumerate(motors):
        if m.contactor:
            p=m.contactor;v=voltage(state,i)
            controls[i]=dict(available=bool(v>p.dropout_voltage_pu if cfg['connected'][i] else v>=p.pickup_voltage_pu),changed=0.,pending=False,opened=0.,restarts=0)
    timeline={}
    for e in settings.events:timeline.setdefault(e.time_s,[]).append(e)
    def emit(i,kind,time,**fields):
        nonlocal transitions
        if kind.startswith('contactor_') or fields.get('cause') in {'undervoltage_timer','automatic_restart'}:
            transitions+=1
            if transitions>settings.maximum_contactor_transitions:raise ValueError('independent group transition budget exceeded')
        log.append(dict(time_s=float(time),kind=kind,equipment_id=motors[i].id,**fields))
    def deadline(i):
        c=controls[i];p=motors[i].contactor
        if cfg['latched'][i]:return math.inf
        if cfg['connected'][i] and not c['available']:return c['changed']+p.dropout_delay_s
        if not cfg['connected'][i] and c['available'] and c['pending'] and c['restarts']<p.maximum_automatic_restarts:
            return max(c['changed']+p.pickup_delay_s,c['opened']+p.minimum_off_time_s)
        return math.inf
    def observe(time,forced=()):
        for i,c in controls.items():
            p=motors[i].contactor;v=voltage(state,i);threshold=p.dropout_voltage_pu if c['available'] else p.pickup_voltage_pu
            eps=32*np.finfo(float).eps*max(1.,abs(v),abs(threshold))
            if i in forced or (v<=threshold+eps if c['available'] else v>=threshold-eps):
                c['available']=not c['available'];c['changed']=time
                emit(i,'contactor_voltage_available' if c['available'] else 'contactor_voltage_unavailable',time,outcome='hysteresis_transition')
            if cfg['latched'][i] and c['pending']:
                c['pending']=False;emit(i,'contactor_restart_cancelled',time,outcome='thermal_latch')
    while time<settings.duration_s:
        right=min([t for t in timeline if t>time]+[settings.duration_s])
        target=min([right,*[deadline(i) for i in controls]])
        if target<=time:raise ValueError('independent group deadline did not advance')
        guards=[];names=[]
        for i,address in thermal_indices.items():
            if cfg['connected'][i] and not cfg['latched'][i]:
                def thermal(t,x,i=i,address=address):return x[address]-motors[i].thermal.trip_level
                thermal.terminal=True;thermal.direction=1;guards.append(thermal);names.append(('thermal',i))
        for i,c in controls.items():
            threshold=motors[i].contactor.dropout_voltage_pu if c['available'] else motors[i].contactor.pickup_voltage_pu
            def crossing(t,x,i=i,threshold=threshold):return voltage(x,i)-threshold
            crossing.terminal=True;crossing.direction=-1 if c['available'] else 1;guards.append(crossing);names.append(('voltage',i))
        snapshot=dict(latched=cfg['latched'].copy(),controls={i:c.copy() for i,c in controls.items()})
        sol=solve_ivp(rhs,(time,target),state,method='DOP853',rtol=2e-11,atol=1e-12,max_step=min(.0005,settings.step_s/2),dense_output=True,events=guards or None)
        if not sol.success:raise ValueError('independent group physical integration failed')
        end=float(sol.t[-1]);state=sol.y[:,-1]
        segments.append((time,end,sol.sol,cfg['connected'].copy(),cfg['scale'],cfg['load_scale'].copy(),snapshot))
        roots={name for name,values in zip(names,sol.t_events or []) if len(values)}
        eps=16*np.finfo(float).eps*max(1.,abs(end))
        scheduled=timeline.pop(right,[]) if abs(end-right)<=eps else []
        due=any(deadline(i)<=end+eps for i in controls)
        voltage_roots={i for kind,i in roots if kind=='voltage'}
        if voltage_roots:observe(end,voltage_roots)
        thermal_roots={i for kind,i in roots if kind=='thermal'}
        if thermal_roots:
            watts=heating(state)
            for i,address in thermal_indices.items():
                threshold=motors[i].thermal.trip_level;tol=64*np.finfo(float).eps*max(abs(state[address]),threshold,1e-300)
                if cfg['connected'][i] and not cfg['latched'][i] and state[address]>=threshold-tol and watts[i]>state[address]:thermal_roots.add(i)
        for i in sorted(thermal_roots):
            cfg['connected'][i]=False;cfg['latched'][i]=True;trips[motors[i].id]=end
            emit(i,'thermal_trip',end,outcome='latched_open')
        set_context(cfg)
        for e in scheduled:
            if e.kind=='source_voltage':cfg['scale']=e.value
            elif e.kind=='load_torque_scale':cfg['load_scale'][next(i for i,m in enumerate(motors) if m.id==e.equipment_id)]=e.value
            elif e.kind not in {'motor_open','motor_close'}:raise ValueError('unsupported independent group event')
        set_context(cfg)
        manual=[e for e in scheduled if e.kind in {'motor_open','motor_close'}]
        if manual:observe(end)
        for e in manual:
            i=next(i for i,m in enumerate(motors) if m.id==e.equipment_id);requested=e.kind=='motor_close';c=controls.get(i)
            outcome='blocked_thermal_latch' if requested and cfg['latched'][i] else 'blocked_undervoltage' if requested and c and not c['available'] else 'already_in_requested_state' if requested==cfg['connected'][i] else 'applied'
            if outcome in {'applied','already_in_requested_state'}:
                cfg['connected'][i]=requested
                if c:
                    c['pending']=False
                    if not requested:c['opened']=end
            emit(i,e.kind,end,outcome=outcome)
        set_context(cfg)
        if roots or scheduled or due:
            observe(end)
            # Evaluate every deadline against the same pre-action voltage. Apply
            # the group before evaluating voltage again, as simultaneous switches.
            actions=[(i,'motor_open' if cfg['connected'][i] else 'motor_close') for i in controls if deadline(i)<=end+eps]
            for i,kind in actions:
                c=controls[i];cfg['connected'][i]=kind=='motor_close'
                if kind=='motor_open':
                    c['opened']=end;c['pending']=c['restarts']<motors[i].contactor.maximum_automatic_restarts
                    emit(i,kind,end,cause='undervoltage_timer',outcome='applied')
                else:
                    c['restarts']+=1;c['pending']=False;emit(i,kind,end,cause='automatic_restart',outcome='applied')
            if actions:set_context(cfg);observe(end)
            changes.append(end)
        if end<=time and not roots:raise ValueError('independent group ODE did not advance')
        time=end
    def signals(snapshot,t,connected,sensed):
        values={}
        for i,m in enumerate(motors):
            values[m.id+'.connected']=float(connected[i])
            if m.thermal:values[m.id+'.thermal_latched']=float(snapshot['latched'][i])
            if i not in snapshot['controls']:continue
            c=snapshot['controls'][i];p=m.contactor
            active=not snapshot['latched'][i] and ((connected[i] and not c['available']) or (not connected[i] and c['available'] and c['pending'] and c['restarts']<p.maximum_automatic_restarts))
            due=(c['changed']+p.dropout_delay_s if connected[i] else max(c['changed']+p.pickup_delay_s,c['opened']+p.minimum_off_time_s)) if active else math.inf
            row=dict(contactor_sensed_voltage_pu=sensed[i],contactor_voltage_available=c['available'],contactor_pending_restart=c['pending'],contactor_restart_count=c['restarts'],contactor_timer_active=active,contactor_timer_remaining_s=max(0.,due-t) if active else 0.)
            values.update({m.id+'.'+k:float(v) for k,v in row.items()})
        return values
    return segments,changes,trips,dict(event_log=log,signals=signals)
