"""Independent SI winding circuit with field-current feedback on transfer blocks.

Does not import production limiter rates, field reconstruction, or controllers.
"""
import math
import numpy as np
from benchmarks.generator_thermal_si import ProtectedCircuit
from benchmarks.genrou_reference import saturation_curve


class ExcitationCircuit(ProtectedCircuit):
    def __init__(self,spec,*,maximum_duration_s=3.,current_constraints=None):
        if spec.coupled.oel2c_limiters:raise ValueError('OEL2C requires the native Model Exchange feedback reference')
        self.limiters={};self.field_relays={};self.uels={}
        super().__init__(spec,maximum_duration_s=maximum_duration_s,current_constraints=current_constraints)
        ids={g.g.id:i for i,(_,g,_) in enumerate(self.ports)}
        passports=spec.coupled.overexcitation_limiters
        if len({p.generator_id for p in passports})!=len(passports):raise ValueError('duplicate independent OEL target')
        self.cfg['field_enabled']=np.ones(len(self.ports),dtype=bool)
        self.cfg['field_thermal_latched']=np.zeros(len(self.ports),dtype=bool)
        self.cfg['uel_regions']=np.full(len(self.ports),-1,dtype=int)
        self.cfg['controller_bounds']=np.zeros(2*len(self.ports),dtype=int)
        self.electrical(self.state)
        for p in passports:
            if p.generator_id not in ids:raise ValueError('unknown independent OEL target')
            i=ids[p.generator_id];_,g,at=self.ports[i]
            measured=abs(self.reaction(g,self.state[at],self.generator_currents[i]))/p.rated_field_reaction_pu
            if measured>=p.current_limit_pu:raise ValueError('independent initial overexcitation limit')
            a=self.dimension;self.dimension+=2;self.limiters[i]=(a,p)
            self.state=np.r_[self.state,measured,0.]

        for p in self.s.generator_field_thermal:
            if p.generator_id not in ids:raise ValueError('unknown field thermal target')
            i=ids[p.generator_id]
            if i in self.field_relays:raise ValueError('duplicate field thermal target')
            _,g,at=self.ports[i]
            heat=(abs(self.reaction(g,self.state[at],self.generator_currents[i]))/p.rated_field_reaction_pu)**2
            level=heat if p.initialization=='equilibrium' else p.initial_level
            if level>=p.trip_level:raise ValueError('independent initial field thermal limit')
            a=self.dimension;self.dimension+=1;self.field_relays[i]=(a,p);self.state=np.r_[self.state,level]

        for p in self.s.underexcitation_limiters:
            if p.generator_id not in ids:raise ValueError('unknown independent UEL target')
            i=ids[p.generator_id]
            if i in self.uels:raise ValueError('duplicate independent UEL target')
            a=self.dimension;self.dimension+=2;self.uels[i]=(a,p);self.state=np.r_[self.state,0.,0.]
            error=self.uel_values(self.state,i)[0]
            if error>=-64*np.finfo(float).eps*max(1.,p.Radius**2):raise ValueError('independent initial underexcitation limit')
            if p.initialization=='inactive_equilibrium':self.state[a]=p.K_M*error

    def uel_values(self,x,i):
        a,p=self.uels[i];node,g,at=self.ports[i];v=self.electrical(x)[0][node] if self.generator_closed(i) else g.emf(x[at])
        power=3*v*np.conj(self.generator_currents[i])/g.p.rated_va
        error=power.real**2+(power.imag-p.Q_0*(abs(v)/g.vbase)**2)**2-(p.Radius*(abs(v)/g.vbase)**2)**2
        output=min(max(x[a],0.),p.MEL_MAX);feedback=p.K_F2/p.T_F2*(output-x[a+1])
        return error,output,feedback,power,v

    def uel_rate(self,x,i):
        a,p=self.uels[i];error,output,feedback,_,_=self.uel_values(x,i)
        return (p.K_M*(error-feedback)-x[a])/p.T_M

    def set_context(self,cfg):
        super().set_context(cfg)
        for i,(_,g,_) in enumerate(self.ports):g.field_enabled=bool(cfg.get('field_enabled',np.ones(len(self.ports),bool))[i])

    @staticmethod
    def reaction(g,x,current):
        p=g.p;eq,_,winding,_=x[6:10];pd,pq=g.flux(x)
        direct_current=-(current/g.ibase*np.exp(-1j*x[0])).imag
        winding_residual=eq-winding-(p.x_d_prime_pu-p.leakage_pu)*direct_current
        magnetizing=direct_current+(p.x_d_prime_pu-p.x_d_subtransient_pu)/(p.x_d_prime_pu-p.leakage_pu)**2*winding_residual
        return eq+(p.x_d_pu-p.x_d_prime_pu)*magnetizing+saturation_curve(p,math.hypot(pd,pq))*pd

    def rhs(self,t,x):
        out=super().rhs(t,x)
        voltage=self.electrical(x)[0]
        for i in set(self.limiters)|set(self.uels):
            node,g,at=self.ports[i];original=g.vref;bias=0.
            if i in self.limiters:
                a,p=self.limiters[i];bias+=min(max(x[a+1],0.),p.maximum_voltage_bias_pu)
                measured=abs(self.reaction(g,x[at],self.generator_currents[i]))/p.rated_field_reaction_pu
                out[a]=(measured-x[a])/p.measurement_time_s
                target=min(max(p.feedback_gain_pu*(x[a]-p.current_limit_pu),0.),p.maximum_voltage_bias_pu)
                out[a+1]=(target-x[a+1])/p.bias_time_s
            if i in self.uels:
                a,p=self.uels[i];_,output,_,_,_=self.uel_values(x,i);bias-=output
                out[a]=self.uel_rate(x,i);out[a+1]=(output-x[a+1])/p.T_F2
            try:
                g.vref=original-bias
                out[at]=g.rhs(x[at],self.generator_currents[i],voltage[node] if self.generator_closed(i) else g.emf(x[at]))
            finally:g.vref=original
        for j,(a,p) in self.field_relays.items():
            _,g,at=self.ports[j];heat=(abs(self.reaction(g,x[at],self.generator_currents[j]))/p.rated_field_reaction_pu)**2
            out[a]=(heat-x[a])/p.time_constant_s
        for j,(_,g,at) in enumerate(self.ports):
            modes=self.cfg.get('controller_bounds',np.zeros(2*len(self.ports),int))
            for slot,local in [(0,2),(1,5)]:
                if modes[2*j+slot]:out[at.start+local]=0.
        return out

    def free_rate(self,x,j,slot):
        node,g,at=self.ports[j];q=x[at];p=g.p
        if slot==0:return (g.pref-(q[1]-1)/p.governor.R-q[2])/p.governor.T1
        voltage=self.electrical(x)[0][node] if self.generator_closed(j) else g.emf(q)
        bias=0.
        if j in self.limiters:
            a,lim=self.limiters[j];bias=min(max(x[a+1],0.),lim.maximum_voltage_bias_pu)
        if j in self.uels:bias-=self.uel_values(x,j)[1]
        error=g.vref-abs(voltage)/g.vbase-bias
        return (p.exciter.K*(g.Ca*q[4]+g.Da*error)-q[5])/p.exciter.TE

    def extra_guards(self,state=None):
        thermal=super().extra_guards();result=[(('thermal',i),guard) for i,guard in thermal]
        for j,(_,g,at) in enumerate(self.ports):
            for slot,local,low,high in [(0,2,g.p.governor.VMIN,g.p.governor.VMAX),(1,5,g.p.exciter.EMIN,g.p.exciter.EMAX)]:
                mode=self.cfg['controller_bounds'][2*j+slot]
                if mode:
                    initial=self.free_rate(state,j,slot);eps=32*np.finfo(float).eps
                    instant=initial>eps if mode==-1 else initial < -eps
                    def release(t,x,j=j,slot=slot,mode=mode,instant=instant):
                        if instant:return 0.
                        rate=self.free_rate(x,j,slot);eps=32*np.finfo(float).eps
                        return rate-eps if mode==-1 else rate+eps
                    release.terminal=True;release.direction=1 if mode==-1 else -1
                    result.append((('controller',j,slot,'release'),release))
                else:
                    for side,limit,direction in [('lower',low,-1),('upper',high,1)]:
                        def crossing(t,x,a=at.start+local,limit=limit):return x[a]-limit
                        crossing.terminal=True;crossing.direction=direction
                        result.append((('controller',j,slot,side),crossing))
        for i,(a,p) in self.field_relays.items():
            if self.cfg['field_thermal_latched'][i]:continue
            def crossing(t,x,a=a,p=p):return x[a]-p.trip_level
            crossing.direction=1;crossing.terminal=True;result.append((('field',i),crossing))
        for i,(a,p) in self.uels.items():
            region=self.cfg['uel_regions'][i]
            if region:
                eps=1e-8 if p.event_detection=='conditioned_1e-8' else 32*np.finfo(float).eps;initial=self.uel_rate(state,i)
                instant=initial>eps if region<0 else initial < -eps
                def reversal(t,x,i=i,region=region,instant=instant,eps=eps):
                    if instant:return 0.
                    return self.uel_rate(x,i)+(-eps if region<0 else eps)
                reversal.terminal=True;reversal.direction=1 if region<0 else -1;result.append((('uel',i,'reset'),reversal))
            else:
                for side,limit,direction in [('lower',0.,-1),('upper',p.MEL_MAX,1)]:
                    def crossing(t,x,a=a,limit=limit):return x[a]-limit
                    crossing.terminal=True;crossing.direction=direction;result.append((('uel',i,side),crossing))
        return result

    def extra_apply(self,roots,t,x,cfg):
        self.electrical(x)
        reactions={i:self.reaction(g,x[at],self.generator_currents[i]) for i,(_,g,at) in enumerate(self.ports)}
        logs=super().extra_apply({key[1] for key in roots if key[0]=='thermal'},t,x,cfg)
        field_roots={key[1] for key in roots if key[0]=='field'}
        if roots:
            for i,(a,p) in self.field_relays.items():
                eps=32*np.finfo(float).eps*max(abs(x[a]),p.trip_level,np.finfo(float).tiny)
                if not cfg['field_thermal_latched'][i] and x[a]>=p.trip_level-eps and (abs(reactions[i])/p.rated_field_reaction_pu)**2>x[a]:field_roots.add(i)
        for i in sorted(field_roots):
            if cfg['field_thermal_latched'][i]:continue
            a,p=self.field_relays[i];closed=bool(cfg['generator_connected'][i]);cfg['generator_connected'][i]=False
            cfg['field_enabled'][i]=False;cfg['field_thermal_latched'][i]=True
            logs.append(dict(time_s=float(t),kind='generator_field_thermal_trip',equipment_id=p.generator_id,level=float(x[a]),threshold=p.trip_level,action=p.action,stator_was_closed=closed,outcome='latched_zero_field_voltage'))
        for _,i,side in sorted(key for key in roots if key[0]=='uel'):
            a,p=self.uels[i];before=float(x[a]);region=cfg['uel_regions'][i]
            if side=='reset':
                x[a]=0. if region<0 else p.MEL_MAX;rate=self.uel_rate(x,i);eps=32*np.finfo(float).eps
                cfg['uel_regions'][i]=(-1 if rate<=0. else 0) if region<0 else (1 if rate>=0. else 0)
            else:cfg['uel_regions'][i]=-1 if side=='lower' else 1
            logs.append(dict(time_s=float(t),kind='uel_lag_reset' if side=='reset' else 'uel_lag_region_enter',equipment_id=p.generator_id,
                bound=('lower' if region<0 else 'upper') if side=='reset' else side,raw_state_before_pu=before,raw_state_after_pu=float(x[a])))
        selected={key for key in roots if key[0]=='controller'}
        if selected:
            for j,(_,g,at) in enumerate(self.ports):
                for slot,local,low,high in [(0,2,g.p.governor.VMIN,g.p.governor.VMAX),(1,5,g.p.exciter.EMIN,g.p.exciter.EMAX)]:
                    if cfg['controller_bounds'][2*j+slot]:continue
                    rate=self.free_rate(x,j,slot);eps=32*np.finfo(float).eps*max(1.,abs(low),abs(high))
                    if abs(x[at.start+local]-low)<=eps and rate<0:selected.add(('controller',j,slot,'lower'))
                    if abs(x[at.start+local]-high)<=eps and rate>0:selected.add(('controller',j,slot,'upper'))
        for key in sorted(selected):
            _,j,slot,side=key;_,g,at=self.ports[j]
            if side=='release':cfg['controller_bounds'][2*j+slot]=0
            else:
                low,high=(g.p.governor.VMIN,g.p.governor.VMAX) if slot==0 else (g.p.exciter.EMIN,g.p.exciter.EMAX)
                x[at.start+(2 if slot==0 else 5)]=low if side=='lower' else high
                cfg['controller_bounds'][2*j+slot]=-1 if side=='lower' else 1
            logs.append(dict(time_s=float(t),kind='controller_limit_release' if side=='release' else 'controller_limit_enter',equipment_id=g.g.id,controller='TGOV1' if slot==0 else 'SEXS',bound=side))
        return logs

    def after_commands(self,t,x,cfg):
        roots=set();eps=32*np.finfo(float).eps
        for i,(a,p) in self.uels.items():
            region=cfg['uel_regions'][i];floor=1e-8 if p.event_detection=='conditioned_1e-8' else 32*np.finfo(float).eps
            if region and (self.uel_rate(x,i)>floor if region<0 else self.uel_rate(x,i)<-floor):roots.add(('uel',i,'reset'))
        for j,(_,g,at) in enumerate(self.ports):
            for slot in (0,1):
                mode=cfg['controller_bounds'][2*j+slot]
                if not mode:continue
                rate=self.free_rate(x,j,slot)
                if (rate>eps if mode==-1 else rate < -eps):roots.add(('controller',j,slot,'release'))
        return self.extra_apply(roots,t,x,cfg) if roots else []

    def extra_signals(self,x):
        out=super().extra_signals(x)
        for i,(a,p) in self.limiters.items():
            _,g,at=self.ports[i];reaction=self.reaction(g,x[at],self.generator_currents[i])
            values=dict(field_reaction_pu=reaction,field_current_pu=abs(reaction)/p.rated_field_reaction_pu,
                        measured_current_pu=x[a],bias_pu=min(max(x[a+1],0.),p.maximum_voltage_bias_pu),
                        bias_state_pu=x[a+1],current_limit_pu=p.current_limit_pu)
            out.update({p.generator_id+'.OEL.'+k:float(v) for k,v in values.items()})
        for i,(a,p) in self.field_relays.items():
            _,g,at=self.ports[i]
            values=dict(field_thermal_level=x[a],field_thermal_latched=self.cfg['field_thermal_latched'][i],field_current_pu=abs(self.reaction(g,x[at],self.generator_currents[i]))/p.rated_field_reaction_pu,field_voltage_enabled=self.cfg['field_enabled'][i])
            out.update({p.generator_id+'.'+k:float(v) for k,v in values.items()})
        for i,(a,p) in self.uels.items():
            error,output,feedback,power,v=self.uel_values(x,i);_,g,_=self.ports[i]
            values=dict(p_pu=power.real,q_pu=power.imag,voltage_pu=abs(v)/g.vbase,circle_error_pu2=error,output_pu=output,
                        raw_lag_state_pu=x[a],feedback_pu=feedback,feedback_memory_pu=x[a+1])
            out.update({p.generator_id+'.UEL.'+k:float(v) for k,v in values.items()})
        return out
