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
        if roots:
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
