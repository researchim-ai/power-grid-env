"""Independent SI winding/stator KCL, island voltages and synchrochecked actions.

No native topology stamps, voltage tangents, synchronism or event engine. A
zero equation for an isolated empty upstream node is pinned to voltage zero;
no artificial admittance or power dissipation is introduced.
"""
import copy
import math
import numpy as np
from scipy.integrate import solve_ivp
from scipy.linalg import lu_factor, lu_solve
from power_grid_env.study_spec import StudySpec
from benchmarks.hybrid_stator_reference import HybridStatorReference


class TopologyStatorReference(HybridStatorReference):
    def __init__(self, study):
        original=StudySpec.model_validate(study);a=original.coupled.network_automation
        s=original.model_dump(mode='json');s['coupled']['events']=[]
        super().__init__(s);self.study=original
        if a.transfers or not a.allow_islands or any(b.fail_open or b.fail_close or b.initial_open or b.backup_breaker_ids or b.interlock_with or b.maximum_reclose_shots or b.target_kind not in ('source','line') for b in a.breakers):
            raise ValueError('initially closed source/line actuators without failure/ATS/reclose required')
        if {b.target_kind for b in a.breakers}!={'source','line'} or len(a.breakers)!=2:
            raise ValueError('one source and one line actuator required')
        commands={'breaker_open','breaker_close','governor_step','voltage_reference_step'}
        if any(e.kind not in commands for e in original.coupled.events):
            raise ValueError('manual breaker and reference commands only')
        if any(e.equipment_id not in self.breakers for e in original.coupled.events if e.kind.startswith('breaker_')):
            raise ValueError('unknown actuator')
        if any(e.equipment_id!='g' for e in original.coupled.events if not e.kind.startswith('breaker_')):
            raise ValueError('unknown controller')

    def system(self,x,c):
        w=self.w;zg=self.impedance_at(x[self.heat_address])
        yl=1/w.zl if c['line'] else 0j;ys=1/w.zs if c['source'] else 0j
        yg=1/zg if c.get('generator_on',True) else 0j
        rs,_=self.resistances(x[-2],x[-1])
        a=np.array([[rs+1j*w.wb*w.ls,1j*w.wb*w.lm,1j*w.wb*w.lm,-1,0],
            [w.lm,self.lr[0,0],self.lr[0,1],0,0],
            [w.lm,self.lr[1,0],self.lr[1,1],0,0],
            [1,0,0,w.yl+yl+yg,-yl],[0,0,0,-yl,yl+ys]],complex)
        b=np.array([0,complex(x[0],x[1]),complex(x[-4],x[-3]),
                    yg*self.generator_emf(x),ys*w.es],complex)
        if not c['line'] and not c['source']: a[4,4]=1.
        return a,b

    def circuit(self,x,c):
        key=(c['line'],c['source'],c.get('generator_on',True),float(x[-2]),float(x[self.heat_address]))
        if key not in self.factors:
            self.factors.clear();a,_=self.system(x,c);self.factors[key]=lu_factor(a)
        _,b=self.system(x,c);q=lu_solve(self.factors[key],b)
        return q[0],q[1:3],q[3],q[4]

    def voltage_rates(self,time,x,c):
        """Differentiate A(x) q=b(x) in SI: A qdot=bdot-Adot q."""
        f=self.rhs(time,x,c);a,b=self.system(x,c);q=np.linalg.solve(a,b)
        da=np.zeros_like(a);db=np.zeros(5,complex)
        da[0,0]=self.rs0*self.h.alpha_per_c*f[-2]
        zg=self.impedance_at(x[self.heat_address])
        dyg=-self.heat.resistance_ohm*self.heat.alpha_per_c*f[self.heat_address]/zg**2 if c.get('generator_on',True) else 0j
        da[3,3]=dyg
        eg=self.generator_emf(x);deg=1j*eg*f[-6]
        if self.avr_address is not None:
            field=self.avr_address+1;p=self.gp.exciter
            # Same declared clipped state, differentiated independently inside
            # its interval; a held boundary has zero actual field rate.
            if p.EMIN<=x[field]<=p.EMAX:deg+=self.gv*f[field]*np.exp(1j*x[-6])
        db[1]=complex(f[0],f[1]);db[2]=complex(f[-4],f[-3])
        db[3]=dyg*eg+(deg/zg if c.get('generator_on',True) else 0j)
        change=np.linalg.solve(a,db-da@q)
        return change[3],change[4]

    def check_close(self,b,time,x,c):
        if time-c['opened_at'].get(b.id,-math.inf)<b.minimum_open_s-1e-12:
            return dict(allowed=False,reason='minimum_open_time')
        _,_,vd,vu=self.circuit(x,c);dvd,dvu=self.voltage_rates(time,x,c)
        p=b.synchronism;fn=self.w.wb/(2*math.pi)
        def frequency(v,dv):return float(fn+(dv/v).imag/(2*math.pi)) if abs(v)/self.gv>=p.minimum_live_voltage_pu else None
        if b.target_kind=='source':left,right=self.w.es/self.gv,vu/self.gv;lf,rf=fn,frequency(vu,dvu)
        else:left,right=vu/self.gv,vd/self.gv;lf,rf=frequency(vu,dvu),frequency(vd,dvd)
        incoming=b.target_kind=='source';ll,lr=abs(left)>=p.minimum_live_voltage_pu,abs(right)>=p.minimum_live_voltage_pu
        result=dict(left_voltage_pu=float(abs(left)),right_voltage_pu=float(abs(right)),voltage_difference_pu=float(abs(abs(left)-abs(right))),left_frequency_hz=lf,right_frequency_hz=rf)
        if incoming and not ll:return result|dict(allowed=False,reason='incoming_supply_dead')
        if incoming:
            if not p.minimum_incoming_voltage_pu<=abs(left)<=p.maximum_incoming_voltage_pu:return result|dict(allowed=False,reason='incoming_voltage_range')
            if lf is None or abs(lf-fn)>p.maximum_incoming_frequency_deviation_hz:return result|dict(allowed=False,reason='incoming_frequency_range')
        if not ll or not lr:return result|dict(allowed=p.allow_dead_receiver,reason='dead_receiver' if incoming else 'dead_network_side')
        if lf is None or rf is None:return result|dict(allowed=False,reason='invalid_frequency_measurement')
        angle=float(abs(np.angle(left*np.conj(right))));frequency=abs(lf-rf)
        result.update(angle_difference_rad=angle,frequency_difference_hz=frequency)
        reason='synchronized'
        if result['voltage_difference_pu']>p.maximum_voltage_difference_pu:reason='voltage_difference'
        elif frequency>p.maximum_frequency_difference_hz:reason='frequency_difference'
        elif angle>p.maximum_angle_rad:reason='angle_difference'
        return result|dict(allowed=reason=='synchronized',reason=reason)

    def sample(self,time,side='regular'):
        values=super().sample(time,side);x,c=self.state_at(time,side)
        _,_,vd,vu=self.circuit(x,c)
        values.update({'source.connected':float(c['source']),
            'network.zero_reference_components':float(not c['source'] and not c['line'])})
        for b in self.breakers.values():
            for k,v in dict(open_attempts=c['open_attempts'][b.id],close_attempts=c['close_attempts'][b.id],reclose_shots=0,manual_inhibit=b.id in c['inhibit']).items():values[b.id+'.'+k]=float(v)
        return values

    def run(self,*,max_step=.001,rtol=2e-11,atol=2e-12):
        if max_step<=0 or not 0<rtol<1 or not 0<atol<1:raise ValueError('invalid integration controls')
        state,initial=self.w.initialize();state=np.r_[state,np.zeros(len(self.relays)),self.initial_tail()]
        c=dict(on=True,line=True,source=True,faults=set(),thermal=False,available=True,since=0.,held={},generator_on=True,opened_at={},inhibit=set(),open_attempts={b:0 for b in self.breakers},close_attempts={b:0 for b in self.breakers},relays={r.id:[False,False,r.measurement_available] for r in self.relays})
        if self.gov_address is not None:c['pref']=self.pref
        if self.avr_address is not None:c['vref']=self.vref
        schedule={}
        for e in self.study.coupled.events:schedule.setdefault(e.time_s,[]).append(e)
        queue=[]
        t=0.;end=self.study.coupled.duration_s;segments=[];boundaries=[];events=[];eps=64*np.finfo(float).eps
        def raw(x,ctx,a):return self.control_values(x,ctx,free=True)[0][a-self.control_start]
        def log(a,side,release=False):events.append(dict(time_s=float(t),kind='controller_limit_release' if release else 'controller_limit_enter',equipment_id=self.g.id,controller=self.entries[a][0],bound='release' if release else side))
        for batch in range(1000):
            if t>=end-1e-14:break
            right=min([time for time in schedule if time>t+1e-13]+[end]+[due for due,_,_ in queue]);guards=[];identities=[]
            if c['generator_on']:
                def trip(time,x):return x[self.replica_address]-self.replica.trip_level
                trip.terminal=True;trip.direction=1;guards.append(trip);identities.append((-1,'trip'))
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
            def release_held():
                for a,side in sorted(list(c['held'].items())):
                    rate=raw(state,c,a)
                    if (side=='lower' and rate>eps) or (side=='upper' and rate < -eps):c['held'].pop(a);log(a,side,True)
            for e in sorted(schedule.get(t,[]),key=lambda e:(e.equipment_id,e.kind)):
                if e.kind.startswith('breaker_'):
                    b=self.breakers[e.equipment_id];closing=e.kind=='breaker_close'
                    if not closing:c['inhibit'].add(b.id)
                    due=t+(b.closing_delay_s if closing else b.opening_delay_s)
                    events.append(dict(time_s=float(t),kind=e.kind+'_requested',equipment_id=b.id,cause='manual',due_s=float(due)))
                    events.append(dict(time_s=float(t),kind=e.kind,equipment_id=b.id,outcome='queued'))
                    queue.append((due,'close' if closing else 'open',b.id));queue.sort(key=lambda q:(q[0],q[1]=='close',q[2]))
                else:
                    c['pref' if e.kind=='governor_step' else 'vref']+=e.value
                    events.append(dict(time_s=float(t),kind=e.kind,equipment_id=e.equipment_id,outcome='applied',value=e.value))
            release_held()
            while queue and queue[0][0]<=t+1e-13:
                _,kind,id_=queue.pop(0);b=self.breakers[id_];closing=kind=='close';check={}
                if c[b.target_kind]==closing:
                    outcome='already_in_requested_state'
                    if closing:c['inhibit'].discard(id_)
                else:
                    c[('close_attempts' if closing else 'open_attempts')][id_]+=1
                    if closing:check=self.check_close(b,t,state,c)
                    outcome='applied' if not closing or check['allowed'] else 'blocked'
                    if outcome=='applied':
                        c[b.target_kind]=closing
                        if closing:c['inhibit'].discard(id_)
                        else:c['opened_at'][id_]=t
                events.append(dict(time_s=float(t),kind='breaker_'+kind,equipment_id=id_,outcome=outcome,cause='manual',**({'synchronism':check} if closing and check else {})))
                release_held()
            for a,side in sorted(list(c['held'].items())):
                rate=raw(state,c,a)
                if (side=='lower' and rate>eps) or (side=='upper' and rate < -eps):c['held'].pop(a);log(a,side,True)
            if len(events)>before:boundaries.append((t,pre,pc,state.copy(),copy.deepcopy(c)))
            elif stop<right-1e-13:raise ValueError('root made no discrete progress')
        else:raise ValueError('hybrid event budget exceeded')
        self.segments,self.boundaries,self.events,self.initial=segments,boundaries,events,initial;return self
