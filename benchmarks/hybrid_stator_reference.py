"""Independent unified controller-limiter/I2-trip engine for stator R(T) studies."""
import copy
import numpy as np
from scipy.integrate import solve_ivp
from power_grid_env.study_spec import StudySpec
from benchmarks.stator_trip_reference import StatorTripReference
from benchmarks.limiter_winding_reference import LimiterReference


class HybridStatorReference(StatorTripReference):
    def __init__(self,study):
        original=StudySpec.model_validate(study);s=copy.deepcopy(study);s['coupled']['events']=[]
        super().__init__(s);self.study=original
        if original.coupled.faults or original.coupled.duration_s>2. or any(e.kind not in {'governor_step','voltage_reference_step'} or e.equipment_id!=self.g.id for e in original.coupled.events):raise ValueError('bounded reference pulses only')
        self.entries={}
        if self.gov_address is not None:self.entries[self.gov_address]=('TGOV1',self.gp.governor.VMIN,self.gp.governor.VMAX)
        if self.avr_address is not None:self.entries[self.avr_address+1]=('SEXS',self.gp.exciter.EMIN,self.gp.exciter.EMAX)

    def control_values(self,x,c,*,free=False):
        rates,channels=LimiterReference.control_values(self,x,c,free=True)
        if not c.get('generator_on',True) and self.avr_address is not None:
            a=self.avr_address;p=self.gp.exciter;error=c.get('vref',self.vref)-abs(self.generator_emf(x))/self.gv;slot=a-self.control_start
            rates[slot]=(error-x[a])/p.TB;rates[slot+1]=(p.K*(p.TATB*error+(1-p.TATB)*x[a])-x[a+1])/p.TE;channels['SEXS.error_pu']=float(error)
        if not free:
            for a in c.get('held',{}):rates[a-self.control_start]=0.
        return rates,channels

    def run(self,*,max_step=.001,rtol=2e-11,atol=2e-12):
        if max_step<=0 or not 0<rtol<1 or not 0<atol<1:raise ValueError('invalid integration controls')
        state,initial=self.w.initialize();state=np.r_[state,np.zeros(len(self.relays)),self.initial_tail()]
        c=dict(on=True,line=True,source=True,faults=set(),thermal=False,available=True,since=0.,held={},generator_on=True,relays={r.id:[False,False,r.measurement_available] for r in self.relays})
        if self.gov_address is not None:c['pref']=self.pref
        if self.avr_address is not None:c['vref']=self.vref
        schedule={}
        for e in self.study.coupled.events:schedule.setdefault(e.time_s,[]).append(e)
        t=0.;end=self.study.coupled.duration_s;segments=[];boundaries=[];events=[];eps=64*np.finfo(float).eps
        def raw(x,ctx,a):return self.control_values(x,ctx,free=True)[0][a-self.control_start]
        def log(a,side,release=False):events.append(dict(time_s=float(t),kind='controller_limit_release' if release else 'controller_limit_enter',equipment_id=self.g.id,controller=self.entries[a][0],bound='release' if release else side))
        for batch in range(1000):
            if t>=end-1e-14:break
            right=min([time for time in schedule if time>t+1e-13]+[end]);guards=[];identities=[]
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
            for e in sorted(schedule.get(t,[]),key=lambda e:(e.equipment_id,e.kind)):
                c['pref' if e.kind=='governor_step' else 'vref']+=e.value;events.append(dict(time_s=float(t),kind=e.kind,equipment_id=e.equipment_id,outcome='applied',value=e.value))
            for a,side in sorted(list(c['held'].items())):
                rate=raw(state,c,a)
                if (side=='lower' and rate>eps) or (side=='upper' and rate < -eps):c['held'].pop(a);log(a,side,True)
            if len(events)>before:boundaries.append((t,pre,pc,state.copy(),copy.deepcopy(c)))
            elif stop<right-1e-13:raise ValueError('root made no discrete progress')
        else:raise ValueError('hybrid event budget exceeded')
        self.segments,self.boundaries,self.events,self.initial=segments,boundaries,events,initial;return self
