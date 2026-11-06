"""Independent complementarity guards for SI winding/transfer-block references.

Layouts and free rates are supplied by independent circuits, never production.
"""
import numpy as np


def attach_layout(circuit,entries):
    circuit.cfg['reference_controller_modes']=np.zeros(len(entries),int)
    original=circuit.rhs
    def rhs(t,x):
        out=original(t,x)
        for k,(address,*_) in enumerate(entries):
            if circuit.cfg['reference_controller_modes'][k]:out[address]=0.
            elif getattr(getattr(circuit,'s',None),'event_policy','legacy')=='coincident_roots_v1':
                # Trial stages may cross a bound. Integrate the free rate until
                # the independently located event activates complementarity.
                out[address]=entries[k][-1](x)
        return out
    def guards(state):
        result=[]
        for k,(a,gid,group,lo,hi,rate) in enumerate(entries):
            mode=circuit.cfg['reference_controller_modes'][k]
            if mode:
                eps=32*np.finfo(float).eps;initial=rate(state)
                instant=initial>eps if mode<0 else initial < -eps
                def release(t,x,rate=rate,mode=mode,instant=instant):
                    if instant:return 0.
                    eps=32*np.finfo(float).eps
                    return rate(x)+(-eps if mode<0 else eps)
                release.direction=1 if mode<0 else -1;release.terminal=True
                result.append(((k,'release'),release))
            else:
                for side,limit,direction in [('lower',lo,-1),('upper',hi,1)]:
                    def crossing(t,x,a=a,limit=limit):return x[a]-limit
                    crossing.direction=direction;crossing.terminal=True;result.append(((k,side),crossing))
        return result
    def apply(roots,t,x,cfg):
        selected=set(roots)
        if roots and getattr(getattr(circuit,'s',None),'event_policy','legacy')!='coincident_roots_v1':
            for k,(a,gid,group,lo,hi,rate) in enumerate(entries):
                if cfg['reference_controller_modes'][k]:continue
                eps=32*np.finfo(float).eps*max(1.,abs(lo),abs(hi));free=rate(x)
                if abs(x[a]-lo)<=eps and free<0:selected.add((k,'lower'))
                if abs(x[a]-hi)<=eps and free>0:selected.add((k,'upper'))
        log=[]
        for k,side in sorted(selected):
            a,gid,group,lo,hi,rate=entries[k]
            if side=='release':cfg['reference_controller_modes'][k]=0
            else:
                x[a]=lo if side=='lower' else hi
                cfg['reference_controller_modes'][k]=-1 if side=='lower' else 1
            log.append(dict(time_s=float(t),kind='controller_limit_release' if side=='release' else 'controller_limit_enter',equipment_id=gid,controller=group,bound=side))
        return log
    def after(t,x,cfg):
        roots=set();eps=32*np.finfo(float).eps
        for k,(_,_,_,_,_,rate) in enumerate(entries):
            mode=cfg['reference_controller_modes'][k]
            if mode and (rate(x)>eps if mode<0 else rate(x)<-eps):roots.add((k,'release'))
        return apply(roots,t,x,cfg) if roots else []
    circuit.rhs=rhs;circuit.extra_guards=guards;circuit.extra_apply=apply;circuit.after_commands=after
    return circuit


def attach(circuit):
    entries=[]
    for node,g,at in circuit.generator_ports():
        def governor_rate(x,g=g,at=at):
            v=x[at];return (g.pref-(v[1]-1)/g.p.governor.R-v[2])/g.p.governor.T1
        def exciter_rate(x,node=node,g=g,at=at):
            v=x[at];error=g.vref-abs(circuit.electrical(x)[0][node])/g.vbase;p=g.p.exciter
            return (p.K*(g.Ca*v[4]+g.Da*error)-v[5])/p.TE
        entries += [(at.start+2,g.g.id,'TGOV1',g.p.governor.VMIN,g.p.governor.VMAX,governor_rate),
                    (at.start+5,g.g.id,'SEXS',g.p.exciter.EMIN,g.p.exciter.EMAX,exciter_rate)]
    return attach_layout(circuit,entries)


def align_commuting_groups(primary_times,reference_times,primary_events,reference_events,scheduled_times,*,tolerance_s=1e-6):
    """Existing clock tolerance, allowing partitions of independent reset rows.

    TGOV1/SEXS resets on distinct controller states commute. Never merge a
    scheduled command, a protection action, or reordered actions on one device.
    This does not increase the event-time acceptance tolerance.
    """
    if not np.isfinite(tolerance_s) or not 0<tolerance_s<=1e-6:raise ValueError('existing finite clock tolerance required')
    clocks=[*primary_times,*reference_times,*scheduled_times,*[e['time_s'] for e in primary_events],*[e['time_s'] for e in reference_events]]
    if not np.isfinite(clocks).all() or any(t<0 for t in clocks):raise ValueError('finite nonnegative event clocks required')
    allowed={'controller_limit_enter','controller_limit_release'}
    def streams(events):
        out={}
        for e in events:
            if e['kind'] not in allowed:continue
            if e['controller'] not in {'TGOV1','SEXS'}:raise ValueError('unsupported controller reset family')
            key=(e['equipment_id'],e['controller'])
            out.setdefault(key,[]).append((e['kind'],e['bound'],float(e['time_s'])))
        return out
    a,b=streams(primary_events),streams(reference_events)
    if a.keys()!=b.keys():raise ValueError('controller event identity differs')
    maximum=0.
    for key,events in a.items():
        peer=b[key]
        if len(events)!=len(peer) or [e[:2] for e in events]!=[e[:2] for e in peer]:raise ValueError('controller event sequence differs')
        maximum=max(maximum,max((abs(x[2]-y[2]) for x,y in zip(events,peer)),default=0.))
    if maximum>tolerance_s:raise ValueError('controller event time exceeds existing tolerance')
    def nearest(t,axis):
        if not axis:raise ValueError('missing independent event boundary')
        at=min(axis,key=lambda x:abs(x-t))
        if abs(at-t)>tolerance_s:raise ValueError('event boundary exceeds existing tolerance')
        return at
    mapping={t:nearest(t,reference_times) for t in primary_times}
    reverse={t:nearest(t,primary_times) for t in reference_times}
    for mapping_,events in ((mapping,primary_events),(reverse,reference_events)):
        values=list(mapping_.values())
        for t,at in mapping_.items():
            if values.count(at)<=1:continue
            if any(abs(t-command)<=tolerance_s for command in scheduled_times):raise ValueError('scheduled boundary cannot be repartitioned')
            cohort=[e for e in events if e['time_s']==t]
            if not cohort or any(e['kind'] not in allowed for e in cohort):raise ValueError('noncommuting event boundary cannot be repartitioned')
    return mapping,dict(policy='distinct_TGOV1_SEXS_reset_rows_only',maximum_controller_event_error_s=maximum,
                        event_time_tolerance_s=tolerance_s,primary_groups=len(primary_times),reference_groups=len(reference_times))
