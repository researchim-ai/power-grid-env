"""Independent bounded SI generator-port OC, delayed actuator and I2 race.

A passive source/line surrogate is used ONLY for inherited initial layout checks;
original relay passports, clocks, measurements and actuators govern integration.
No native automation, current, limiter or thermal event functions are called.
"""
import copy
import numpy as np
from scipy.integrate import solve_ivp
from power_grid_env.study_spec import StudySpec
from benchmarks.hybrid_stator_reference import HybridStatorReference


class NetworkStatorReference(HybridStatorReference):
    def __init__(self,study):
        original=StudySpec.model_validate(study);a=original.coupled.network_automation
        active=[r for r in a.relays if r.pickup<1e8]
        if len(active)!=1 or active[0].kind!='overcurrent' or active[0].measurement_kind!='generator' or active[0].measurement_id!='g' or active[0].directional or active[0].curve!='definite' or not active[0].measurement_available:
            raise ValueError('one available definite generator OC required')
        b=next(b for b in a.breakers if b.id==active[0].breaker_id)
        if b.target_kind!='generator' or b.target_id!='g' or a.transfers or any(b.initial_open or b.maximum_reclose_shots or b.backup_breaker_ids for b in a.breakers):
            raise ValueError('initially closed generator actuator without backups/ATS/reclose required')
        if any(r.pickup<1e8 and r.breaker_id!=b.id for r in a.relays):raise ValueError('unsupported active relay')
        s=copy.deepcopy(study)
        for r in s['coupled']['network_automation']['relays']:
            if r['measurement_kind']=='generator':r.update(measurement_kind='line',measurement_id=original.network.lines[0].id)
            r['pickup']=1e8
        for bb in s['coupled']['network_automation']['breakers']:
            if bb['target_kind']=='generator':bb.update(target_kind='line',target_id=original.network.lines[0].id)
        super().__init__(s);self.study=original;self.auto=a;self.relays=sorted(a.relays,key=lambda r:r.id);self.breakers={b.id:b for b in a.breakers}

    def currents(self,x,c):
        values=super().currents(x,c);values['generator']=abs(self.current(x,c));return values

    def rhs(self,t,x,c):
        rates=super().rhs(t,x,c)
        for i,r in enumerate(self.relays):
            active,latched,available=c['relays'][r.id]
            if r.curve=='definite' and active and available and not latched:
                rates[4+i]=1/r.time_multiplier_s
        return rates

    def sample(self,time,side='regular'):
        values=super().sample(time,side);x,c=self.state_at(time,side)
        values['g.stator_thermal_latched']=float(c['i2_latched'])
        for b in self.breakers.values():
            for key,value in dict(open_attempts=c['attempts'][b.id],close_attempts=0,reclose_shots=0,manual_inhibit=0).items():values[b.id+'.'+key]=float(value)
        return values

    def run(self,*,max_step=.001,rtol=2e-11,atol=2e-12):
        if max_step<=0 or not 0<rtol<1 or not 0<atol<1:raise ValueError('invalid integration controls')
        state,initial=self.w.initialize();state=np.r_[state,np.zeros(len(self.relays)),self.initial_tail()]
        c=dict(on=True,line=True,source=True,faults=set(),thermal=False,available=True,since=0.,held={},generator_on=True,generator=True,i2_latched=False,attempts={b:0 for b in self.breakers},relays={r.id:[False,False,r.measurement_available] for r in self.relays})
        if self.gov_address is not None:c['pref']=self.pref
        if self.avr_address is not None:c['vref']=self.vref
        schedule={}
        for e in self.study.coupled.events:schedule.setdefault(e.time_s,[]).append(e)
        if any(self.currents(state,c)[r.measurement_kind]>=r.pickup for r in self.relays if r.measurement_available):
            raise ValueError('initial inactive OC profile required')
        queue=[]
        t=0.;end=self.study.coupled.duration_s;segments=[];boundaries=[];events=[];eps=64*np.finfo(float).eps
        def raw(x,ctx,a):return self.control_values(x,ctx,free=True)[0][a-self.control_start]
        def log(a,side,release=False):events.append(dict(time_s=float(t),kind='controller_limit_release' if release else 'controller_limit_enter',equipment_id=self.g.id,controller=self.entries[a][0],bound='release' if release else side))
        for batch in range(1000):
            if t>=end-1e-14:break
            right=min([time for time in schedule if time>t+1e-13]+[end]+[due for due,_,_,_ in queue]);guards=[];identities=[]
            if c['generator_on']:
                def trip(time,x):return x[self.replica_address]-self.replica.trip_level
                trip.terminal=True;trip.direction=1;guards.append(trip);identities.append((-1,'trip'))
            for i,r in enumerate(self.relays):
                active,latched,available=c['relays'][r.id]
                if not available:continue
                def pickup(time,x,r=r,active=active):return self.currents(x,c)[r.measurement_kind]-r.pickup*(r.dropout_ratio if active else 1.)
                pickup.direction=-1 if active else 1;pickup.terminal=True;guards.append(pickup);identities.append((-2,('pickup',i)))
                if active and not latched:
                    def operate(time,x,i=i):return x[4+i]-1.
                    operate.direction=1;operate.terminal=True;guards.append(operate);identities.append((-2,('operate',i)))
            for a,(_,lo,hi) in sorted(self.entries.items()):
                if a in c['held']:
                    side=c['held'][a]
                    def guard(time,x,a=a,side=side):return raw(x,c,a)-eps if side=='lower' else raw(x,c,a)+eps
                    guard.direction=1 if side=='lower' else -1;guard.terminal=True;guards.append(guard);identities.append((a,'release'))
                else:
                    for side,limit,direction in [('lower',lo,-1),('upper',hi,1)]:
                        def guard(time,x,a=a,limit=limit):return x[a]-limit
                        guard.direction=direction;guard.terminal=True;guards.append(guard);identities.append((a,side))
            context=copy.deepcopy(c)
            sol=solve_ivp(lambda time,x:self.rhs(time,x,context),(t,right),state,method='DOP853',dense_output=True,events=guards,max_step=max_step,rtol=rtol,atol=atol)
            if not sol.success:raise ValueError(sol.message)
            stop=float(sol.t[-1]);segments.append((t,stop,sol.sol,context));state=sol.y[:,-1].copy();t=stop;pre=state.copy();pc=copy.deepcopy(c);before=len(events)
            roots=[identity for identity,ts in zip(identities,sol.t_events) if len(ts) and abs(ts[-1]-t)<1e-11]
            if (-1,'trip') in roots:
                c['generator_on']=False;c['generator']=False;c['i2_latched']=True;events.append(dict(time_s=float(t),kind='generator_thermal_trip',equipment_id=self.g.id,level=float(state[self.replica_address]),threshold=self.replica.trip_level,outcome='latched_electrical_open'))
            for a,side in sorted(r for r in roots if r[0]>=0):
                if side=='release':c['held'].pop(a,None);log(a,side,True)
                else:state[a]=self.entries[a][1 if side=='lower' else 2];c['held'][a]=side;log(a,side)
            for e in sorted(schedule.get(t,[]),key=lambda e:(e.equipment_id,e.kind)):
                c['pref' if e.kind=='governor_step' else 'vref']+=e.value;events.append(dict(time_s=float(t),kind=e.kind,equipment_id=e.equipment_id,outcome='applied',value=e.value))
            for _,(kind,i) in sorted(r for r in roots if r[0]==-2):
                r=self.relays[i];flags=c['relays'][r.id]
                if kind=='pickup':
                    flags[0]=not flags[0];events.append(dict(time_s=float(t),kind='network_relay_pickup' if flags[0] else 'network_relay_dropout',equipment_id=r.id))
                else:
                    flags[1]=True;state[4+i]=1.;b=self.breakers[r.breaker_id];due=t+b.opening_delay_s;cause='relay:'+r.id
                    events.append(dict(time_s=float(t),kind='network_relay_trip',equipment_id=r.id,breaker_id=b.id))
                    events.append(dict(time_s=float(t),kind='breaker_open_requested',equipment_id=b.id,cause=cause,due_s=float(due)))
                    queue.append((due,'open',b.id,cause));queue.sort()
            while queue and queue[0][0]<=t+1e-13:
                _,kind,id_,cause=queue.pop(0);b=self.breakers[id_]
                if kind=='failure':
                    if c['generator_on'] and abs(self.current(state,c))>b.breaker_failure_current_a:
                        events.append(dict(time_s=float(t),kind='breaker_failure_confirmed',equipment_id=id_))
                    continue
                if not c['generator_on']:outcome='already_in_requested_state'
                else:
                    c['attempts'][id_]+=1;outcome='failed_to_operate' if b.fail_open else 'applied'
                    if not b.fail_open:c['generator_on']=False;c['generator']=False
                    else:queue.append((t+b.breaker_failure_delay_s,'failure',id_,cause));queue.sort()
                events.append(dict(time_s=float(t),kind='breaker_open',equipment_id=id_,outcome=outcome,cause=cause))
            for a,side in sorted(list(c['held'].items())):
                rate=raw(state,c,a)
                if (side=='lower' and rate>eps) or (side=='upper' and rate < -eps):c['held'].pop(a);log(a,side,True)
            # Refresh measurement hysteresis after a thermal or mechanical opening.
            for r in self.relays:
                flags=c['relays'][r.id]
                if not flags[2]:continue
                margin=self.currents(state,c)[r.measurement_kind]-r.pickup*(r.dropout_ratio if flags[0] else 1.)
                tolerance=64*np.finfo(float).eps*max(1.,r.pickup)
                if (not flags[0] and margin>tolerance) or (flags[0] and margin < -tolerance):
                    flags[0]=not flags[0];events.append(dict(time_s=float(t),kind='network_relay_pickup' if flags[0] else 'network_relay_dropout',equipment_id=r.id))
            if len(events)>before:boundaries.append((t,pre,pc,state.copy(),copy.deepcopy(c)))
            elif stop<right-1e-13:raise ValueError('root made no discrete progress')
        else:raise ValueError('hybrid event budget exceeded')
        self.segments,self.boundaries,self.events,self.initial=segments,boundaries,events,initial;return self
