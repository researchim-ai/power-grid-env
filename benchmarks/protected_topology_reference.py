"""Independent SI OC/UV clocks, backup actions and one-shot reclose in an island.

Inherited passive initialization checks use a neutral automation surrogate.
Original passports govern every integrated clock, root and queued action.
"""
import copy
import math
import numpy as np
from scipy.integrate import solve_ivp
from power_grid_env.study_spec import StudySpec
from benchmarks.topology_stator_reference import TopologyStatorReference


class ProtectedTopologyReference(TopologyStatorReference):
    def __init__(self,study):
        original=StudySpec.model_validate(study);a=original.coupled.network_automation
        active=[r for r in a.relays if r.pickup<1e8]
        if len(active)!=1 or active[0].directional or not active[0].measurement_available:
            raise ValueError('one available non-directional active relay required')
        r=active[0]
        if (r.kind=='overcurrent' and (r.measurement_kind!='generator' or r.measurement_id!='g')) or (r.kind=='undervoltage' and (r.measurement_kind!='node' or r.measurement_id!='n1')):
            raise ValueError('generator OC or downstream node UV required')
        if a.transfers or any(b.maximum_reclose_shots>1 or b.fail_close or b.initial_open or b.interlock_with for b in a.breakers):
            raise ValueError('bounded initially closed actuators, no ATS/close failure/interlock')
        if any(e.kind not in {'breaker_open','governor_step','voltage_reference_step'} for e in original.coupled.events):
            raise ValueError('reference pulses or manual opening only')
        s=original.model_dump(mode='json')
        for relay in s['coupled']['network_automation']['relays']:
            relay.update(kind='overcurrent',measurement_kind='line',measurement_id=original.network.lines[0].id,pickup=1e8)
        for b in s['coupled']['network_automation']['breakers']:
            b.update(fail_open=False,backup_breaker_ids=[],maximum_reclose_shots=0)
        super().__init__(s);self.study=original;self.auto=a
        self.relays=sorted(a.relays,key=lambda r:r.id);self.breakers={b.id:b for b in a.breakers}

    def currents(self,x,c):
        values=super().currents(x,c)
        values['generator']=abs(self.current(x,c));values['node']=abs(self.circuit(x,c)[2])/self.gv
        return values

    def measurement(self,r,x,c):return self.currents(x,c)[r.measurement_kind]

    def margin(self,r,x,c):
        active=c['relays'][r.id][0];value=self.measurement(r,x,c)
        return r.pickup/(r.dropout_ratio if active else 1.)-value if r.kind=='undervoltage' else value-r.pickup*(r.dropout_ratio if active else 1.)

    def rhs(self,t,x,c):
        rates=super().rhs(t,x,c)
        for i,r in enumerate(self.relays):
            active,latched,available=c['relays'][r.id]
            rate=0.
            if not latched:
                if not active:rate=-x[4+i]/r.reset_s
                elif available:
                    if r.kind=='undervoltage' or r.curve=='definite':rate=1/r.time_multiplier_s
                    else:
                        multiple=self.measurement(r,x,c)/r.pickup
                        if multiple>1:
                            k,alpha={'standard_inverse':(.14,.02),'very_inverse':(13.5,1.),'extremely_inverse':(80.,2.)}[r.curve]
                            rate=math.expm1(alpha*math.log(multiple))/(k*r.time_multiplier_s)
            rates[4+i]=rate
        return rates

    def sample(self,time,side='regular'):
        values=super().sample(time,side);x,c=self.state_at(time,side)
        for b in self.breakers:values[b+'.reclose_shots']=float(c['shots'][b])
        for r in self.relays:values[r.id+'.measurement']=float(self.measurement(r,x,c))
        return values

    def run(self,*,max_step=.001,rtol=2e-11,atol=2e-12):
        if max_step<=0 or not 0<rtol<1 or not 0<atol<1:raise ValueError('invalid integration controls')
        state,initial=self.w.initialize();state=np.r_[state,np.zeros(len(self.relays)),self.initial_tail()]
        c=dict(on=True,line=True,source=True,faults=set(),thermal=False,available=True,since=0.,held={},generator_on=True,opened_at={},inhibit=set(),shots={b:0 for b in self.breakers},failure_announced=set(),open_attempts={b:0 for b in self.breakers},close_attempts={b:0 for b in self.breakers},relays={r.id:[False,False,r.measurement_available] for r in self.relays})
        if self.gov_address is not None:c['pref']=self.pref
        if self.avr_address is not None:c['vref']=self.vref
        schedule={}
        for e in self.study.coupled.events:schedule.setdefault(e.time_s,[]).append(e)
        queue=[]
        t=0.;end=self.study.coupled.duration_s;segments=[];boundaries=[];events=[];eps=64*np.finfo(float).eps
        def raw(x,ctx,a):return self.control_values(x,ctx,free=True)[0][a-self.control_start]
        def log(a,side,release=False):events.append(dict(time_s=float(t),kind='controller_limit_release' if release else 'controller_limit_enter',equipment_id=self.g.id,controller=self.entries[a][0],bound='release' if release else side))
        def enqueue(due,kind,id_,cause):
            if any(q[:3]==(due,kind,id_) for q in queue):return
            queue.append((due,kind,id_,cause));queue.sort(key=lambda q:(q[0],{'open':0,'failure':1,'close':3}[q[1]],q[2]))
        def request(id_,kind,cause):
            b=self.breakers[id_];due=t+(b.closing_delay_s if kind=='close' else b.opening_delay_s)
            enqueue(due,kind,id_,cause)
            events.append(dict(time_s=float(t),kind='breaker_'+kind+'_requested',equipment_id=id_,cause=cause,due_s=float(due)))
        def refresh():
            for r in self.relays:
                flags=c['relays'][r.id]
                if not flags[2]:continue
                margin=self.margin(r,state,c);tolerance=64*np.finfo(float).eps*max(1.,r.pickup)
                if (not flags[0] and margin>tolerance) or (flags[0] and margin < -tolerance):
                    flags[0]=not flags[0];events.append(dict(time_s=float(t),kind='network_relay_pickup' if flags[0] else 'network_relay_dropout',equipment_id=r.id))
        if any(self.margin(r,state,c)>=0 for r in self.relays):raise ValueError('initially inactive relay profile required')
        for batch in range(1000):
            if t>=end-1e-14:break
            right=min([time for time in schedule if time>t+1e-13]+[end]+[due for due,_,_,_ in queue]);guards=[];identities=[]
            if c['generator_on']:
                def trip(time,x):return x[self.replica_address]-self.replica.trip_level
                trip.terminal=True;trip.direction=1;guards.append(trip);identities.append((-1,'trip'))
            for i,r in enumerate(self.relays):
                flags=c['relays'][r.id]
                if not flags[2]:continue
                def pickup(time,x,r=r):return self.margin(r,x,c)
                pickup.terminal=True;pickup.direction=-1 if flags[0] else 1;guards.append(pickup);identities.append((-2,('pickup',i)))
                if flags[0] and not flags[1]:
                    def operate(time,x,i=i):return x[4+i]-1.
                    operate.terminal=True;operate.direction=1;guards.append(operate);identities.append((-2,('operate',i)))
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
                c['generator_on']=False;events.append(dict(time_s=float(t),kind='generator_thermal_trip',equipment_id=self.g.id,level=float(state[self.replica_address]),threshold=self.replica.trip_level,outcome='latched_electrical_open'))
            for a,side in sorted(r for r in roots if r[0]>=0):
                if side=='release':c['held'].pop(a,None);log(a,side,True)
                else:state[a]=self.entries[a][1 if side=='lower' else 2];c['held'][a]=side;log(a,side)
            for _,(kind,i) in sorted(r for r in roots if r[0]==-2):
                r=self.relays[i];flags=c['relays'][r.id]
                if kind=='pickup':
                    flags[0]=not flags[0];events.append(dict(time_s=float(t),kind='network_relay_pickup' if flags[0] else 'network_relay_dropout',equipment_id=r.id))
                else:
                    flags[1]=True;state[4+i]=1.
                    events.append(dict(time_s=float(t),kind='network_relay_trip',equipment_id=r.id,breaker_id=r.breaker_id))
                    request(r.breaker_id,'open','relay:'+r.id)
            def release_held():
                for a,side in sorted(list(c['held'].items())):
                    rate=raw(state,c,a)
                    if (side=='lower' and rate>eps) or (side=='upper' and rate < -eps):c['held'].pop(a);log(a,side,True)
            for e in sorted(schedule.get(t,[]),key=lambda e:(e.equipment_id,e.kind)):
                if e.kind.startswith('breaker_'):
                    b=self.breakers[e.equipment_id];closing=e.kind=='breaker_close'
                    if not closing:c['inhibit'].add(b.id)
                    request(b.id,'close' if closing else 'open','manual')
                    events.append(dict(time_s=float(t),kind=e.kind,equipment_id=b.id,outcome='queued'))
                else:
                    c['pref' if e.kind=='governor_step' else 'vref']+=e.value
                    events.append(dict(time_s=float(t),kind=e.kind,equipment_id=e.equipment_id,outcome='applied',value=e.value))
            release_held()
            while queue and queue[0][0]<=t+1e-13:
                _,kind,id_,cause=queue.pop(0);b=self.breakers[id_];closing=kind=='close';check={}
                if kind=='failure':
                    if id_ not in c['failure_announced'] and c[b.target_kind] and self.currents(state,c)[b.target_kind]>b.breaker_failure_current_a:
                        c['failure_announced'].add(id_);events.append(dict(time_s=float(t),kind='breaker_failure_confirmed',equipment_id=id_))
                        for backup in sorted(b.backup_breaker_ids):request(backup,'open','breaker_failure:'+id_)
                    continue
                if closing and cause!='manual' and id_ in c['inhibit']:
                    events.append(dict(time_s=float(t),kind='breaker_close',equipment_id=id_,outcome='blocked',reason='manual_inhibit',cause=cause));continue
                if c[b.target_kind]==closing:
                    outcome='already_in_requested_state'
                    if closing and cause=='manual':c['inhibit'].discard(id_)
                else:
                    c[('close_attempts' if closing else 'open_attempts')][id_]+=1
                    if closing:check=self.check_close(b,t,state,c)
                    outcome='applied' if not closing or check['allowed'] else 'blocked'
                    if not closing and b.fail_open:
                        outcome='failed_to_operate';enqueue(t+b.breaker_failure_delay_s,'failure',id_,cause)
                    if outcome=='applied':
                        c[b.target_kind]=closing
                        if closing:
                            if cause=='manual':c['inhibit'].discard(id_)
                            c['failure_announced'].discard(id_)
                            for i,r in enumerate(self.relays):
                                if r.breaker_id==id_:c['relays'][r.id][:2]=[False,False];state[4+i]=0.
                        else:
                            c['opened_at'][id_]=t
                            if cause.startswith('relay:') and c['shots'][id_]<b.maximum_reclose_shots:
                                c['shots'][id_]+=1;enqueue(t+max(b.reclose_dead_s,b.minimum_open_s),'close',id_,'automatic_reclose')
                events.append(dict(time_s=float(t),kind='breaker_'+kind,equipment_id=id_,outcome=outcome,cause=cause,**({'synchronism':check} if closing and check else {})))
                release_held()
            refresh()
            for a,side in sorted(list(c['held'].items())):
                rate=raw(state,c,a)
                if (side=='lower' and rate>eps) or (side=='upper' and rate < -eps):c['held'].pop(a);log(a,side,True)
            if len(events)>before:boundaries.append((t,pre,pc,state.copy(),copy.deepcopy(c)))
            elif stop<right-1e-13:raise ValueError('root made no discrete progress')
        else:raise ValueError('hybrid event budget exceeded')
        self.segments,self.boundaries,self.events,self.initial=segments,boundaries,events,initial;return self
