"""Independent SI machine/winding network with root-localized generator I2 trip.

One permanent generator opening; no reclose, islands, active OC/UV or limiter
crossings. Opening removes both generator Norton admittance and injection.
"""
import copy
import numpy as np
from scipy.integrate import solve_ivp
from scipy.linalg import lu_factor,lu_solve
from benchmarks.generator_replica_reference import GeneratorReplicaReference
from benchmarks.controlled_winding_reference import ControlledReference


class GeneratorTripReference(GeneratorReplicaReference):
    def __init__(self,study):
        super().__init__(study)
        if self.study.coupled.events or self.w.m.contactor or any(r.pickup<1e8 for r in self.relays):raise ValueError('inactive-protection profile without commands required')

    def circuit(self,x,c):
        w=self.w;connected=c.get('generator_on',True)
        key=(connected,c['on'],c['line'],c['source'],tuple(sorted(c['faults'])),float(x[-2]))
        if key not in self.factors:
            self.factors.clear();up=down=0j
            for id in c['faults']:
                f=w.faults[id];value=1/complex(f.r_ohm,f.x_ohm)
                if f.node_id==w.up:up+=value
                else:down+=value
            yl=1/w.zl;ys=1/w.zs;rs,_=self.resistances(x[-2],x[-1]);yg=1/self.gz if connected else 0j
            matrix=np.array([[rs+1j*w.wb*w.ls,1j*w.wb*w.lm,1j*w.wb*w.lm,-1,0],
              [w.lm,self.lr[0,0],self.lr[0,1],0,0],[w.lm,self.lr[1,0],self.lr[1,1],0,0],
              [1,0,0,w.yl+down+yl+yg,-yl],[0,0,0,-yl,yl+ys+up]],complex)
            self.factors[key]=lu_factor(matrix)
        rhs=[0,complex(x[0],x[1]),complex(x[-4],x[-3]),self.generator_emf(x)/self.gz if connected else 0j,w.es/w.zs]
        q=lu_solve(self.factors[key],rhs);return q[0],q[1:3],q[3],q[4]

    def control_values(self,x,c):
        rates,channels=ControlledReference.control_values(self,x,c)
        if not c.get('generator_on',True) and self.avr_address is not None:
            a=self.avr_address;p=self.gp.exciter;error=self.vref-abs(self.generator_emf(x))/self.gv
            rate=(p.K*(p.TATB*error+(1-p.TATB)*x[a])-x[a+1])/p.TE
            if (x[a+1]>=p.EMAX and rate>0) or (x[a+1]<=p.EMIN and rate<0):rate=0.
            slot=a-self.control_start;rates[slot]=(error-x[a])/p.TB;rates[slot+1]=rate;channels['SEXS.error_pu']=float(error)
        return rates,channels

    def generator_current(self,x,c):return (self.generator_emf(x)-self.circuit(x,c)[2])/self.gz if c.get('generator_on',True) else 0j

    def rhs(self,t,x,c):
        result=GeneratorReplicaReference.rhs(self,t,x,c);eg=self.generator_emf(x);ig=self.generator_current(x,c)
        result[-5]=(self.mechanical(x)-3*(eg*np.conj(ig)).real/self.gp.rated_va-self.gp.damping_pu*(x[-5]-1))/(2*self.gp.inertia_s)
        result[self.replica_address]=((abs(ig)/self.gib)**2-x[self.replica_address])/self.replica.time_constant_s
        return result

    def sample(self,time,side='regular'):
        values=ControlledReference.sample(self,time,side);x,c=self.state_at(time,side);ig=self.generator_current(x,c);eg=self.generator_emf(x);vd=self.circuit(x,c)[2]
        connected=c.get('generator_on',True);terminal=vd if connected else eg;power=3*vd*np.conj(ig)
        data=dict(current_pu=abs(ig)/self.gib,p_w=power.real,q_var=power.imag,electrical_power_pu=3*(eg*np.conj(ig)).real/self.gp.rated_va,
            connected=float(connected),terminal_voltage_pu=abs(terminal)/self.gv,terminal_angle_rad=np.angle(terminal),
            stator_thermal_level=x[self.replica_address],stator_thermal_latched=float(not connected))
        values.update({'g.'+k:float(v) for k,v in data.items()});return values

    def run(self,*,max_step=.001,rtol=2e-11,atol=2e-12):
        if max_step<=0 or not 0<rtol<1 or not 0<atol<1:raise ValueError('invalid integration controls')
        state,initial=self.w.initialize();state=np.r_[state,np.zeros(len(self.relays)),self.initial_tail()]
        context=dict(on=True,line=True,source=True,faults=set(),thermal=False,available=True,since=0.,generator_on=True,relays={r.id:[False,False,r.measurement_available] for r in self.relays})
        schedule={}
        for f in self.study.coupled.faults:
            for time,kind in [(f.apply_s,'fault_apply'),(f.clear_s,'fault_clear')]:schedule.setdefault(time,[]).append((kind,f.id))
        t=0.;end=self.study.coupled.duration_s;segments=[];boundaries=[];events=[]
        for batch in range(32):
            if t>=end-1e-14:break
            right=min([time for time in schedule if time>t+1e-13]+[end]);c=copy.deepcopy(context)
            def trip(time,x):return x[self.replica_address]-self.replica.trip_level
            trip.terminal=True;trip.direction=1
            sol=solve_ivp(lambda time,x:self.rhs(time,x,c),(t,right),state,method='DOP853',dense_output=True,events=[trip] if c['generator_on'] else None,max_step=max_step,rtol=rtol,atol=atol)
            if not sol.success:raise ValueError(sol.message)
            stop=float(sol.t[-1]);segments.append((t,stop,sol.sol,c));state=sol.y[:,-1].copy();t=stop;pre=state.copy();pc=copy.deepcopy(context);before=len(events)
            for kind,id in sorted(schedule.get(t,[]),key=lambda e:(e[1],e[0])):
                if kind=='fault_apply':context['faults'].add(id)
                else:context['faults'].discard(id)
                events.append(dict(time_s=float(t),kind=kind,equipment_id=id,outcome='applied'))
            triggered=c['generator_on'] and len(sol.t_events[0])>0
            if triggered:
                context['generator_on']=False
                events.append(dict(time_s=float(t),kind='generator_thermal_trip',equipment_id=self.g.id,level=float(state[self.replica_address]),threshold=self.replica.trip_level,outcome='latched_electrical_open'))
            if len(events)>before:boundaries.append((t,pre,pc,state.copy(),copy.deepcopy(context)))
        else:raise ValueError('trip event budget exceeded')
        self.segments,self.boundaries,self.events,self.initial=segments,boundaries,events,initial;return self
