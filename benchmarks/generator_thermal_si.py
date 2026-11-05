"""Independent winding-circuit thermal relay and electrical-only generator trip."""
import math
import numpy as np
from benchmarks.multibus_multi_generator import MultiCircuit


class ProtectedCircuit(MultiCircuit):
    def __init__(self,spec,*,maximum_duration_s=3.,current_constraints=None,maximum_nodes=64,sparse_network=False,maximum_generators=4):
        ids=[p.generator_id for p in spec.coupled.generator_thermal]
        if len(set(ids))!=len(ids) or not set(ids)<={g.id for g in spec.network.generators}:raise ValueError('independent unique existing generator thermal targets required')
        self.relays={}
        super().__init__(spec,maximum_duration_s=maximum_duration_s,current_constraints=current_constraints,maximum_nodes=maximum_nodes,sparse_network=sparse_network,maximum_generators=maximum_generators)
        ids={g.g.id:i for i,(_,g,_) in enumerate(self.ports)}
        self.cfg['generator_connected']=np.ones(len(self.ports),dtype=bool)
        self.cfg['generator_latched']=np.zeros(len(self.ports),dtype=bool)
        self.electrical(self.state)
        for p in self.s.generator_thermal:
            i=ids[p.generator_id];a=self.dimension;self.dimension+=1;self.relays[i]=(a,p)
            _,g,_=self.ports[i];level=abs(self.generator_currents[i]/g.ibase)**2 if p.initialization=='equilibrium' else p.initial_level
            if level>=p.trip_level:raise ValueError('independent initial generator thermal limit')
            self.state=np.r_[self.state,level]
        self.rebuild()

    def generator_closed(self,j):return bool(self.cfg.get('generator_connected',np.ones(len(self.ports),bool))[j])

    def rhs(self,t,x):
        out=super().rhs(t,x)
        for i,(a,p) in self.relays.items():
            _,g,_=self.ports[i];out[a]=(abs(self.generator_currents[i]/g.ibase)**2-x[a])/p.time_constant_s
        return out

    def handle_event(self,event,cfg):
        if event.kind!='generator_open':return super().handle_event(event,cfg)
        i=next(i for i,(_,g,_) in enumerate(self.ports) if g.g.id==event.equipment_id)
        closed=bool(cfg['generator_connected'][i]);cfg['generator_connected'][i]=False
        return dict(outcome='applied' if closed else 'already_in_requested_state')

    def extra_guards(self,state=None):
        result=[]
        for i,(a,p) in self.relays.items():
            if not self.generator_closed(i) or self.cfg['generator_latched'][i]:continue
            def crossing(t,x,a=a,p=p):return x[a]-p.trip_level
            crossing.direction=1;crossing.terminal=True;result.append((i,crossing))
        return result

    def extra_apply(self,roots,t,x,cfg):
        self.electrical(x);selected=set(roots)
        for i,(a,p) in self.relays.items():
            _,g,_=self.ports[i];heat=abs(self.generator_currents[i]/g.ibase)**2
            eps=32*np.finfo(float).eps*max(abs(x[a]),p.trip_level,np.finfo(float).tiny)
            if cfg['generator_connected'][i] and not cfg['generator_latched'][i] and x[a]>=p.trip_level-eps and heat>x[a]:selected.add(i)
        log=[]
        for i in sorted(selected):
            a,p=self.relays[i];cfg['generator_connected'][i]=False;cfg['generator_latched'][i]=True
            log.append(dict(time_s=float(t),kind='generator_thermal_trip',equipment_id=p.generator_id,level=float(x[a]),threshold=p.trip_level,outcome='latched_electrical_open'))
        return log

    def extra_signals(self,x):
        out={}
        voltage=self.electrical(x)[0]
        for i,(node,g,at) in enumerate(self.ports):
            terminal=voltage[node] if self.generator_closed(i) else g.emf(x[at])
            out.update({g.g.id+'.connected':float(self.generator_closed(i)),g.g.id+'.current_pu':abs(self.generator_currents[i]/g.ibase),g.g.id+'.terminal_voltage_pu':abs(terminal)/g.vbase})
        for i,(a,p) in self.relays.items():out.update({p.generator_id+'.stator_thermal_level':x[a],p.generator_id+'.stator_thermal_latched':float(self.cfg['generator_latched'][i])})
        return out
