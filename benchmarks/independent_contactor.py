"""Independent event-driven SI-reference state chart; no production control calls.

One voltage contactor and optional latched stator-current thermal replica.
The physical ODE, voltage sensing and configuration are injected callbacks.
"""
import math
import numpy as np
from scipy.integrate import solve_ivp

KEYS=('connected','contactor_sensed_voltage_pu','contactor_voltage_available','contactor_pending_restart','contactor_restart_count','contactor_timer_active','contactor_timer_remaining_s')


def run(*,settings,motor,state,rhs,voltage,get_context,set_context):
    p=motor.contactor
    if p is None:raise ValueError('independent contactor passport required')
    cfg=get_context();v=voltage(state)
    control=dict(available=bool(v>p.dropout_voltage_pu if cfg['connected'] else v>=p.pickup_voltage_pu),changed=0.,pending=False,opened=0.,restarts=0)
    timeline={}
    for e in settings.events:timeline.setdefault(e.time_s,[]).append(e)
    log=[];segments=[];changes=[];trip_time=None;time=0.;transitions=0
    def emit(kind,time,**fields):
        nonlocal transitions
        if kind.startswith('contactor_') or fields.get('cause') in {'undervoltage_timer','automatic_restart'}:
            transitions+=1
            if transitions>settings.maximum_contactor_transitions:raise ValueError('independent contactor transition budget exceeded')
        log.append(dict(time_s=float(time),kind=kind,equipment_id=motor.id,**fields))
    def deadline():
        if cfg['latched']:return math.inf
        if cfg['connected'] and not control['available']:return control['changed']+p.dropout_delay_s
        if not cfg['connected'] and control['available'] and control['pending'] and control['restarts']<p.maximum_automatic_restarts:
            return max(control['changed']+p.pickup_delay_s,control['opened']+p.minimum_off_time_s)
        return math.inf
    def observe(time,forced=False):
        v=voltage(state);threshold=p.dropout_voltage_pu if control['available'] else p.pickup_voltage_pu
        tolerance=32*np.finfo(float).eps*max(1.,abs(v),abs(threshold))
        change=forced or (v<=threshold+tolerance if control['available'] else v>=threshold-tolerance)
        if change:
            control['available']=not control['available'];control['changed']=time
            emit('contactor_voltage_available' if control['available'] else 'contactor_voltage_unavailable',time)
        if cfg['latched'] and control['pending']:
            control['pending']=False;emit('contactor_restart_cancelled',time,outcome='thermal_latch')
    while time<settings.duration_s:
        next_external=min([t for t in timeline if t>time]+[settings.duration_s])
        target=min(next_external,deadline(),settings.duration_s)
        if target<=time:raise ValueError('independent controller produced a nonadvancing deadline')
        guards=[];guard_names=[]
        if motor.thermal and cfg['connected'] and not cfg['latched']:
            def thermal(t,x):return x[5]-motor.thermal.trip_level
            thermal.terminal=True;thermal.direction=1;guards.append(thermal);guard_names.append('thermal')
        threshold=p.dropout_voltage_pu if control['available'] else p.pickup_voltage_pu
        def crossing(t,x):return voltage(x)-threshold
        crossing.terminal=True;crossing.direction=-1 if control['available'] else 1
        guards.append(crossing);guard_names.append('voltage')
        before=dict(control,latched=cfg['latched'])
        sol=solve_ivp(rhs,(time,target),state,method='DOP853',rtol=2e-11,atol=1e-12,max_step=min(.0005,settings.step_s/2),dense_output=True,events=guards)
        if not sol.success:raise ValueError('independent physical ODE failed: '+sol.message)
        end=float(sol.t[-1]);state=sol.y[:,-1]
        segments.append((time,end,sol.sol,cfg['connected'],cfg['scale'],cfg['load_scale'],cfg['pm'],cfg['vref'],before))
        roots={name for name,events in zip(guard_names,sol.t_events) if len(events)}
        epsilon=16*np.finfo(float).eps*max(1.,abs(end))
        scheduled=timeline.pop(next_external,[]) if abs(end-next_external)<=epsilon else []
        due=deadline()<=end+epsilon
        if 'voltage' in roots:observe(end,True)
        if 'thermal' in roots:
            cfg['latched']=True;cfg['connected']=False;trip_time=end
            set_context(cfg);emit('thermal_trip',end,outcome='latched_open')
        # Exogenous changes precede manual interlock evaluation and timed actions.
        for e in scheduled:
            if e.kind=='source_voltage':cfg['scale']=e.value
            elif e.kind=='load_torque_scale':cfg['load_scale']=e.value
            elif e.kind=='governor_step':cfg['pm']+=e.value
            elif e.kind=='voltage_reference_step':cfg['vref']+=e.value
            elif e.kind not in {'motor_open','motor_close'}:raise ValueError('unsupported independent controller event')
        set_context(cfg)
        manual=[e for e in scheduled if e.kind in {'motor_open','motor_close'}]
        if manual:observe(end)
        for e in manual:
            requested=e.kind=='motor_close'
            outcome='blocked_thermal_latch' if requested and cfg['latched'] else 'blocked_undervoltage' if requested and not control['available'] else 'already_in_requested_state' if requested==cfg['connected'] else 'applied'
            if outcome in {'applied','already_in_requested_state'}:
                cfg['connected']=requested;control['pending']=False
                if not requested:control['opened']=end
                set_context(cfg)
            emit(e.kind,end,outcome=outcome)
        if roots or scheduled or due:
            observe(end)
            if deadline()<=end+epsilon:
                if cfg['connected']:
                    cfg['connected']=False;control['opened']=end;control['pending']=control['restarts']<p.maximum_automatic_restarts
                    emit('motor_open',end,cause='undervoltage_timer',outcome='applied')
                else:
                    cfg['connected']=True;control['pending']=False;control['restarts']+=1
                    emit('motor_close',end,cause='automatic_restart',outcome='applied')
                set_context(cfg);observe(end)
            changes.append(end)
        if end<=time and not roots:raise ValueError('independent physical integration did not advance')
        time=end
    names=[motor.id+'.'+k for k in KEYS]+([motor.id+'.thermal_latched'] if motor.thermal else [])
    def signals(snapshot,time,sensed,connected):
        active=not snapshot['latched'] and ((connected and not snapshot['available']) or (not connected and snapshot['available'] and snapshot['pending'] and snapshot['restarts']<p.maximum_automatic_restarts))
        due=(snapshot['changed']+p.dropout_delay_s if connected else max(snapshot['changed']+p.pickup_delay_s,snapshot['opened']+p.minimum_off_time_s)) if active else math.inf
        values=dict(connected=connected,contactor_sensed_voltage_pu=sensed,contactor_voltage_available=snapshot['available'],contactor_pending_restart=snapshot['pending'],contactor_restart_count=snapshot['restarts'],contactor_timer_active=active,contactor_timer_remaining_s=max(0.,due-time) if active else 0.)
        if motor.thermal:values['thermal_latched']=snapshot['latched']
        return {motor.id+'.'+k:float(v) for k,v in values.items()}
    return segments,changes,trip_time,dict(event_log=log,channel_names=names,signals=signals)
