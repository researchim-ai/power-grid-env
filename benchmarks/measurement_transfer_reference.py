"""Independent measured-relay continuity and break-before-make standby transfer."""
import copy
import math
import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import brentq
from power_grid_env.study_spec import StudySpec
from benchmarks.protected_topology_reference import ProtectedTopologyReference


class MeasurementTransferReference(ProtectedTopologyReference):
    def __init__(self,study):
        original=StudySpec.model_validate(study);a=original.coupled.network_automation
        self.transfer=a.transfers[0] if a.transfers else None
        s=original.model_dump(mode='json')
        s['coupled']['events']=[e for e in s['coupled']['events'] if e['kind'] not in ('relay_measurement_lost','relay_measurement_restored','source_voltage')]
        if self.transfer:
            if len(a.transfers)!=1 or original.coupled.operating_point.winding_state!='passport' or original.network.generators[0].p_w!=0.:
                raise ValueError('one zero-P standby generator with passport temperatures required')
            if len(a.breakers)!=2 or a.relays or original.coupled.duration_s>.3:
                raise ValueError('two actuators, no CT relays, horizon <=0.3 s required for transfer')
            grid=next(b for b in a.breakers if b.target_kind=='source');reserve=next(b for b in a.breakers if b.target_kind=='generator')
            if grid.initial_open or not reserve.initial_open or reserve.fail_open or reserve.fail_close or any(b.maximum_reclose_shots or b.backup_breaker_ids or b.interlock_with for b in a.breakers):
                raise ValueError('initially open healthy reserve and initially closed primary required')
            if (self.transfer.primary_breaker_id,self.transfer.reserve_breaker_id)!=(grid.id,reserve.id) or self.transfer.primary_voltage.kind!='source' or self.transfer.reserve_voltage.kind!='generator':
                raise ValueError('source-to-generator transfer only')
            auto=s['coupled']['network_automation'];auto['transfers']=[]
            for b in auto['breakers']:
                b.update(initial_open=False,fail_open=False)
                if b['target_kind']=='generator':b.update(target_kind='line',target_id=original.network.lines[0].id)
            auto['relays']=[dict(id='surrogate',kind='overcurrent',measurement_kind='generator',measurement_id='g',breaker_id=reserve.id,pickup=150.,time_multiplier_s=.005)]
            s['coupled']['events']=[]
        super().__init__(s);self.study=original;self.auto=a;self.breakers={b.id:b for b in a.breakers}
        self.relays=sorted(a.relays,key=lambda r:r.id)
        if self.transfer:
            # Remove the surrogate relay's slot from all inherited addresses.
            self.control_start-=1
            if self.gov_address is not None:self.gov_address-=1
            if self.avr_address is not None:self.avr_address-=1
            self.heat_address-=1;self.replica_address-=1
            self.entries={k-1:v for k,v in self.entries.items()}
            self.transfer_address=self.replica_address+1
        allowed={'governor_step','voltage_reference_step','breaker_open','relay_measurement_lost','relay_measurement_restored'} if not self.transfer else {'source_voltage','voltage_reference_step'}
        if any(e.kind not in allowed for e in original.coupled.events):raise ValueError('unsupported command')

    def initial_tail(self):
        tail=super().initial_tail()
        return [*tail[:-6],0.,*tail[-6:]] if self.transfer else tail

    def initial_conditions(self):
        if not self.transfer:return super().initial_conditions()
        w=self.w;ts,tr=self.seed_temperatures
        def residual(speed):return self.static(speed,ts,tr)[4]-w.load(speed)
        grid=np.linspace(self.study.coupled.operating_point.minimum_speed_pu,1.,257)
        roots=[brentq(residual,a,b,xtol=1e-14,rtol=1e-14) for a,b in zip(grid,grid[1:]) if residual(a)*residual(b)<0]
        if not roots:raise ValueError('standby motor operating point not found')
        speed=max(roots);vd,im,ir,flux,_=self.static(speed,ts,tr)
        self.initial_flux2=[float(flux[1].real),float(flux[1].imag)];self.initial_temperatures=(ts,tr)
        self.g_amplitude=self.g.v_set_pu*self.gv;self.g_initial_delta=float(np.angle(vd))
        self.g_pm=self.heat.core_loss_w*self.g.v_set_pu**2/self.gp.rated_va
        self.temperature_init=self.heat.initial_c;self.replica_init=self.replica.initial_level
        state=np.array([flux[0].real,flux[0].imag,speed,(abs(im)/w.ib)**2])
        full=np.r_[state,self.initial_tail()];ctx=self.initial_context();indices=[0,1,2,len(full)-4,len(full)-3]
        jac=np.empty((5,5))
        for j,i in enumerate(indices):
            step=1e-6*max(1.,abs(full[i]));a,b=full.copy(),full.copy();a[i]+=step;b[i]-=step
            jac[:,j]=(self.rhs(0,a,ctx)[indices]-self.rhs(0,b,ctx)[indices])/(2*step)
        modes=np.linalg.eigvals(jac)
        return state,dict(mode='independent_standby_grid_motor_root',generator_temperature_c=float(self.temperature_init),generator_mechanical_pu=float(self.g_pm),
                          energy_spectral_abscissa_per_s=float(max(modes.real)),energy_eigenvalues_per_s=[[float(v.real),float(v.imag)] for v in modes],thermal_equilibrium=False)

    def initial_context(self):
        return dict(on=True,line=True,source=True,source_scale=1.,generator_on=not self.transfer,generator=not self.transfer,i2_latched=False,
            faults=set(),thermal=False,available=True,since=0.,held={},opened_at={},inhibit=set(),shots={b:0 for b in self.breakers},failure_announced=set(),
            open_attempts={b:0 for b in self.breakers},close_attempts={b:0 for b in self.breakers},relays={r.id:[False,False,r.measurement_available] for r in self.relays},ats_active=False,ats_latched=False)

    def system(self,x,c):
        a,b=super().system(x,c);b[4]*=c.get('source_scale',1.);return a,b

    def circuit(self,x,c):
        # The inherited LU depends on topology/temperatures only; the RHS
        # includes the historical source amplitude from each saved context.
        return super().circuit(x,c)

    def margin(self,r,x,c):return super().margin(r,x,c) if c['relays'][r.id][2] else -1.

    def currents(self,x,c):
        values=super().currents(x,c)
        vu=self.circuit(x,c)[3]
        values['source']=abs((self.w.es*c.get('source_scale',1.)-vu)/self.w.zs) if c['source'] else 0.
        return values

    def rhs(self,t,x,c):
        rates=super().rhs(t,x,c)
        if not self.transfer:return rates
        level=x[self.transfer_address]
        rate=0. if c.get('ats_latched',False) else (1. if c.get('ats_active',False) else -level)/self.transfer.detection_delay_s
        return np.r_[rates[:-6],rate,rates[-6:]]

    def sample(self,time,side='regular'):
        values=super().sample(time,side);x,c=self.state_at(time,side)
        for r in self.relays:
            if not c['relays'][r.id][2]:values[r.id+'.measurement']=0.
        if self.transfer:
            values['g.stator_thermal_latched']=float(c['i2_latched'])
            for key,v in dict(progress=x[self.transfer_address],active=c['ats_active'],latched=c['ats_latched']).items():values[self.transfer.id+'.'+key]=float(v)
        return values

    def check_close(self,b,time,x,c):
        if not self.transfer:return super().check_close(b,time,x,c)
        other=next(other for other in self.breakers.values() if other.id!=b.id)
        if c[other.target_kind]:return dict(allowed=False,reason='interlocked_breaker_closed',interlock_id=other.id)
        if time-c['opened_at'].get(b.id,-math.inf)<b.minimum_open_s-1e-12:return dict(allowed=False,reason='minimum_open_time')
        _,_,vd,_=self.circuit(x,c);dvd,_=self.voltage_rates(time,x,c);f=self.rhs(time,x,c);eg=self.generator_emf(x)
        deg=1j*eg*f[-6]
        if self.avr_address is not None:
            a=self.avr_address+1;p=self.gp.exciter
            if p.EMIN<=x[a]<=p.EMAX:deg+=self.gv*f[a]*np.exp(1j*x[-6])
        p=b.synchronism;fn=self.w.wb/(2*math.pi);lf=float(fn+(deg/eg).imag/(2*math.pi))
        rf=float(fn+(dvd/vd).imag/(2*math.pi)) if abs(vd)/self.gv>=p.minimum_live_voltage_pu else None
        left,right=abs(eg)/self.gv,abs(vd)/self.gv
        result=dict(left_voltage_pu=float(left),right_voltage_pu=float(right),voltage_difference_pu=float(abs(left-right)),left_frequency_hz=lf,right_frequency_hz=rf)
        reason='synchronized'
        if left<p.minimum_live_voltage_pu:reason='incoming_supply_dead'
        elif not p.minimum_incoming_voltage_pu<=left<=p.maximum_incoming_voltage_pu:reason='incoming_voltage_range'
        elif abs(lf-fn)>p.maximum_incoming_frequency_deviation_hz:reason='incoming_frequency_range'
        elif rf is None:return result|dict(allowed=p.allow_dead_receiver,reason='dead_receiver')
        else:
            result.update(angle_difference_rad=float(abs(np.angle(eg*np.conj(vd)))),frequency_difference_hz=abs(lf-rf))
            if result['voltage_difference_pu']>p.maximum_voltage_difference_pu:reason='voltage_difference'
            elif result['frequency_difference_hz']>p.maximum_frequency_difference_hz:reason='frequency_difference'
            elif result['angle_difference_rad']>p.maximum_angle_rad:reason='angle_difference'
        return result|dict(allowed=reason=='synchronized',reason=reason)

    def run(self,*,max_step=.001,rtol=2e-11,atol=2e-12):
        if max_step<=0 or not 0<rtol<1 or not 0<atol<1:raise ValueError('invalid integration controls')
        state,initial=self.w.initialize();state=np.r_[state,np.zeros(len(self.relays)),self.initial_tail()]
        c=self.initial_context()
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
            queue.append((due,kind,id_,cause));queue.sort(key=lambda q:(q[0],{'open':0,'failure':1,'transfer':2,'close':3}[q[1]],q[2]))
        def request(id_,kind,cause):
            b=self.breakers[id_];due=t+(b.closing_delay_s if kind=='close' else b.opening_delay_s)
            enqueue(due,kind,id_,cause)
            events.append(dict(time_s=float(t),kind='breaker_'+kind+'_requested',equipment_id=id_,cause=cause,due_s=float(due)))
        def refresh():
            for r in self.relays:
                flags=c['relays'][r.id]
                margin=self.margin(r,state,c);tolerance=64*np.finfo(float).eps*max(1.,r.pickup)
                if (not flags[0] and margin>tolerance) or (flags[0] and margin < -tolerance):
                    flags[0]=not flags[0];events.append(dict(time_s=float(t),kind='network_relay_pickup' if flags[0] else 'network_relay_dropout',equipment_id=r.id))
            if self.transfer and not c['ats_latched']:
                value=c['source_scale']*abs(self.w.es)/self.gv;p=self.transfer
                threshold=p.pickup_pu if c['ats_active'] else p.dropout_pu
                if (not c['ats_active'] and value<threshold-1e-12) or (c['ats_active'] and value>threshold+1e-12):
                    c['ats_active']=not c['ats_active'];events.append(dict(time_s=float(t),kind='transfer_voltage_lost' if c['ats_active'] else 'transfer_voltage_restored',equipment_id=p.id))
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
            if self.transfer and not c['ats_latched']:
                p=self.transfer;threshold=p.pickup_pu if c['ats_active'] else p.dropout_pu
                def voltage(time,x):return threshold-c['source_scale']*abs(self.w.es)/self.gv
                voltage.terminal=True;voltage.direction=-1 if c['ats_active'] else 1;guards.append(voltage);identities.append((-3,'pickup'))
                if c['ats_active']:
                    def transfer_due(time,x):return x[self.transfer_address]-1.
                    transfer_due.terminal=True;transfer_due.direction=1;guards.append(transfer_due);identities.append((-3,'trip'))
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
            for _,(kind,i) in sorted(r for r in roots if r[0]==-2):
                r=self.relays[i];flags=c['relays'][r.id]
                if kind=='pickup':
                    flags[0]=not flags[0];events.append(dict(time_s=float(t),kind='network_relay_pickup' if flags[0] else 'network_relay_dropout',equipment_id=r.id))
                else:
                    flags[1]=True;state[4+i]=1.
                    events.append(dict(time_s=float(t),kind='network_relay_trip',equipment_id=r.id,breaker_id=r.breaker_id))
                    request(r.breaker_id,'open','relay:'+r.id)
            if (-3,'pickup') in roots:c['ats_active']=not c['ats_active']
            if (-3,'trip') in roots:
                p=self.transfer;c['ats_latched']=True;state[self.transfer_address]=1.
                events.append(dict(time_s=float(t),kind='transfer_started',equipment_id=p.id))
                request(p.primary_breaker_id,'open','transfer:'+p.id)
                enqueue(t+self.breakers[p.primary_breaker_id].opening_delay_s+p.dead_time_s,'transfer',p.id,'transfer:'+p.id)
            def release_held():
                for a,side in sorted(list(c['held'].items())):
                    rate=raw(state,c,a)
                    if (side=='lower' and rate>eps) or (side=='upper' and rate < -eps):c['held'].pop(a);log(a,side,True)
            for e in sorted(schedule.get(t,[]),key=lambda e:(e.equipment_id,e.kind)):
                if e.kind in ('relay_measurement_lost','relay_measurement_restored'):
                    valid=e.kind=='relay_measurement_restored';c['relays'][e.equipment_id][2]=valid
                    events.append(dict(time_s=float(t),kind=e.kind,equipment_id=e.equipment_id,outcome='applied',measurement_available=valid))
                elif e.kind=='source_voltage':
                    c['source_scale']=e.value;events.append(dict(time_s=float(t),kind=e.kind,equipment_id=e.equipment_id,outcome='applied'))
                elif e.kind.startswith('breaker_'):
                    b=self.breakers[e.equipment_id];closing=e.kind=='breaker_close'
                    if not closing:c['inhibit'].add(b.id)
                    request(b.id,'close' if closing else 'open','manual')
                    events.append(dict(time_s=float(t),kind=e.kind,equipment_id=b.id,outcome='queued'))
                else:
                    c['pref' if e.kind=='governor_step' else 'vref']+=e.value
                    events.append(dict(time_s=float(t),kind=e.kind,equipment_id=e.equipment_id,outcome='applied',value=e.value))
            release_held()
            while queue and queue[0][0]<=t+1e-13:
                _,kind,id_,cause=queue.pop(0)
                if kind=='transfer':
                    p=self.transfer
                    if c['source']:events.append(dict(time_s=float(t),kind='transfer_blocked',equipment_id=id_,reason='primary_not_open'))
                    elif abs(self.generator_emf(state))/self.gv<p.pickup_pu:events.append(dict(time_s=float(t),kind='transfer_blocked',equipment_id=id_,reason='reserve_unavailable'))
                    else:request(p.reserve_breaker_id,'close',cause)
                    continue
                b=self.breakers[id_];closing=kind=='close';check={}
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
                        if b.target_kind=='generator':c['generator_on']=closing
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
