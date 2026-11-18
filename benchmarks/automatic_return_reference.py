"""Independent SI automatic-return policy; no native policy/event helpers."""
import copy
import numpy as np
from scipy.integrate import solve_ivp
from power_grid_env.study_spec import StudySpec
from benchmarks.transfer_lifecycle_reference import TransferLifecycleReference


class AutomaticReturnReference(TransferLifecycleReference):
    extra_event_kinds=frozenset()

    def extra_command(self,event,time,state,context):return None

    def after_thermal_trip(self,time,state,context):pass

    def __init__(self,study):
        original=StudySpec.model_validate(study)
        if len(original.coupled.network_automation.transfers)!=1 or original.coupled.network_automation.relays:
            raise ValueError('one standby ATS without CT required')
        p=original.coupled.network_automation.transfers[0]
        if p.return_policy is None:raise ValueError('explicit automatic return policy required')
        s=original.model_dump(mode='json');s['coupled']['network_automation']['transfers'][0].pop('return_policy')
        s['coupled']['events']=[e for e in s['coupled']['events'] if e['kind'] not in {'return_reset','return_cancel'}|self.extra_event_kinds]
        for b in s['coupled']['network_automation']['breakers']:b['fail_open']=False
        super().__init__(s);self.study=original;self.transfer=p;self.policy=p.return_policy
        self.breakers={b.id:b for b in original.coupled.network_automation.breakers}
        self.return_address=self.transfer_address+1
        if any(e.kind not in {'source_voltage','transfer_primary_measurement_lost','transfer_primary_measurement_restored','return_reset','return_cancel','breaker_close'}|self.extra_event_kinds for e in original.coupled.events):raise ValueError('unsupported return command')

    def initial_tail(self):
        values=super().initial_tail();return [*values[:-6],0.,*values[-6:]]

    def initial_context(self):
        c=super().initial_context();c.update(ret_mode='armed',ret_active=False,ret_generation=0);return c

    def rhs(self,t,x,c):
        values=super().rhs(t,x,c)
        rate=1/self.policy.restore_delay_s if c.get('ret_active',False) and c.get('ret_mode','armed')=='armed' else 0.
        return np.r_[values[:-6],rate,values[-6:]]

    def return_health(self,x,c,hysteresis=False):
        for side in ('primary','reserve'):
            if not c[side+'_valid']:return -1.,side+'_measurement_unavailable'
        if c['inhibit']:return -1.,'manual_inhibit'
        value=abs(self.w.es)*c['source_scale']/self.gv
        return value-(self.policy.dropout_pu if hysteresis else self.transfer.pickup_pu),'primary_voltage_unavailable'

    def sample(self,time,side='regular'):
        values=super().sample(time,side);x,c=self.state_at(time,side)
        for key,v in dict(progress=x[self.return_address],active=c['ret_active'],mode={'armed':0,'opening':1,'closing':2,'blocked':3}[c['ret_mode']],generation=c['ret_generation']).items():values['ats.return.'+key]=float(v)
        return values

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
            queue.append((due,kind,id_,cause));queue.sort(key=lambda q:(q[0],{'open':0,'failure':1,'transfer':2,'return_connect':2,'close':3}[q[1]],q[2]))
        def request(id_,kind,cause):
            b=self.breakers[id_];due=t+(b.closing_delay_s if kind=='close' else b.opening_delay_s)
            enqueue(due,kind,id_,cause)
            events.append(dict(time_s=float(t),kind='breaker_'+kind+'_requested',equipment_id=id_,cause=cause,due_s=float(due)))
        def ret_token():return 'automatic_return:ats:'+str(c['ret_generation'])
        def remove_return():
            old=len(queue);queue[:]=[q for q in queue if q[3].rsplit(':',1)[0]!='automatic_return:ats'];return old-len(queue)
        def ret_block(reason):
            c['ret_mode']='blocked';c['ret_active']=False;remove_return()
            events.append(dict(time_s=float(t),kind='automatic_return_blocked',equipment_id='ats',reason=reason))
            if reason=='primary_voltage_unavailable' and not c['source'] and not c['generator_on'] and c['primary_valid'] and c['reserve_valid'] and not c['inhibit']:
                c['ats_latched']=False;c['ats_active']=False;state[self.transfer_address]=0.
                events.append(dict(time_s=float(t),kind='automatic_return_fallback_armed',equipment_id='ats'))
        def ret_refresh():
            if c['ret_mode']!='armed':return
            eligible=not c['source'] and c['generator_on'];margin,reason=self.return_health(state,c,c['ret_active'])
            active=eligible and margin>=0
            if active!=c['ret_active']:
                c['ret_active']=active;state[self.return_address]=0.
                events.append(dict(time_s=float(t),kind='return_recovery_started' if active else 'return_recovery_cancelled',equipment_id='ats',**({} if active else dict(reason=reason if eligible else 'topology_changed'))))
        def refresh():
            for r in self.relays:
                flags=c['relays'][r.id]
                margin=self.margin(r,state,c);tolerance=64*np.finfo(float).eps*max(1.,r.pickup)
                if (not flags[0] and margin>tolerance) or (flags[0] and margin < -tolerance):
                    flags[0]=not flags[0];events.append(dict(time_s=float(t),kind='network_relay_pickup' if flags[0] else 'network_relay_dropout',equipment_id=r.id))
            if self.transfer and not c['ats_latched']:
                if not c['primary_valid']:
                    if c['ats_active']:
                        c['ats_active']=False;events.append(dict(time_s=float(t),kind='transfer_detection_suspended',equipment_id=self.transfer.id,reason='primary_measurement_unavailable'))
                    return
                value=c['source_scale']*abs(self.w.es)/self.gv;p=self.transfer
                threshold=p.pickup_pu if c['ats_active'] else p.dropout_pu
                if (not c['ats_active'] and value<threshold-1e-12) or (c['ats_active'] and value>threshold+1e-12):
                    c['ats_active']=not c['ats_active'];events.append(dict(time_s=float(t),kind='transfer_voltage_lost' if c['ats_active'] else 'transfer_voltage_restored',equipment_id=p.id))
            ret_refresh()
        if any(self.margin(r,state,c)>=0 for r in self.relays):raise ValueError('initially inactive relay profile required')
        for batch in range(1000):
            if t>=end-1e-14:break
            right=min([time for time in schedule if time>t+1e-13]+[end]+[due for due,_,_,_ in queue]);guards=[];identities=[]
            return_deadline=t+(1-state[self.return_address])*self.policy.restore_delay_s if c['ret_active'] and c['ret_mode']=='armed' else None
            if return_deadline is not None:right=min(right,return_deadline)
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
            if self.transfer and not c['ats_latched'] and c['primary_valid']:
                p=self.transfer;threshold=p.pickup_pu if c['ats_active'] else p.dropout_pu
                def voltage(time,x):return threshold-c['source_scale']*abs(self.w.es)/self.gv
                voltage.terminal=True;voltage.direction=-1 if c['ats_active'] else 1;guards.append(voltage);identities.append((-3,'pickup'))
                if c['ats_active']:
                    def transfer_due(time,x):return x[self.transfer_address]-1.
                    transfer_due.terminal=True;transfer_due.direction=1;guards.append(transfer_due);identities.append((-3,'trip'))
            if c['ret_mode']=='armed' and not c['source'] and c['generator_on'] and c['primary_valid'] and c['reserve_valid'] and not c['inhibit']:
                def health_guard(time,x):return self.return_health(x,c,c['ret_active'])[0]
                health_guard.terminal=True;health_guard.direction=-1 if c['ret_active'] else 1;guards.append(health_guard);identities.append((-4,'health'))
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
            if return_deadline is not None and abs(t-return_deadline)<=64*np.finfo(float).eps*max(1.,abs(t)):roots.append((-4,'due'))
            if (-1,'trip') in roots:
                c['generator_on']=False;c['generator']=False;c['i2_latched']=True;events.append(dict(time_s=float(t),kind='generator_thermal_trip',equipment_id=self.g.id,level=float(state[self.replica_address]),threshold=self.replica.trip_level,outcome='latched_electrical_open'))
                self.after_thermal_trip(t,state,c)
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
            if (-4,'health') in roots:
                c['ret_active']=not c['ret_active'];state[self.return_address]=0.
                events.append(dict(time_s=float(t),kind='return_recovery_started' if c['ret_active'] else 'return_recovery_cancelled',equipment_id='ats',**({} if c['ret_active'] else dict(reason='primary_voltage_unavailable'))))
            if (-4,'due') in roots:
                c['ret_active']=False;c['ret_mode']='opening';c['ret_generation']+=1;state[self.return_address]=1.
                events.append(dict(time_s=float(t),kind='automatic_return_started',equipment_id='ats',generation=c['ret_generation']))
                request('reserve-cb','open',ret_token())
                enqueue(t+self.breakers['reserve-cb'].opening_delay_s+self.policy.dead_time_s,'return_connect','ats',ret_token())
            def release_held():
                for a,side in sorted(list(c['held'].items())):
                    rate=raw(state,c,a)
                    if (side=='lower' and rate>eps) or (side=='upper' and rate < -eps):c['held'].pop(a);log(a,side,True)
            for e in sorted(schedule.get(t,[]),key=lambda e:(e.equipment_id,e.kind)):
                custom=self.extra_command(e,t,state,c)
                if custom is not None:
                    events.append(dict(time_s=float(t),kind=e.kind,equipment_id=e.equipment_id,**custom))
                elif e.kind in ('return_reset','return_cancel'):
                    removed=remove_return();c['ret_generation']+=1;c['ret_mode']='armed' if e.kind=='return_reset' else 'blocked';c['ret_active']=False;state[self.return_address]=0.
                    events.append(dict(time_s=float(t),kind=e.kind,equipment_id=e.equipment_id,outcome='applied',cancelled_actions=removed,generation=c['ret_generation']))
                elif e.kind.startswith('transfer_'):
                    side='primary' if e.kind.startswith('transfer_primary_') else 'reserve';valid=e.kind.endswith('_restored');c[side+'_valid']=valid
                    events.append(dict(time_s=float(t),kind=e.kind,equipment_id=e.equipment_id,outcome='applied',measurement_available=valid,measurement_side=side))
                elif e.kind=='ats_reset':
                    c['ats_latched']=False;c['ats_active']=False;state[self.transfer_address]=0.
                    events.append(dict(time_s=float(t),kind=e.kind,equipment_id=e.equipment_id,outcome='applied'))
                elif e.kind in ('relay_measurement_lost','relay_measurement_restored'):
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
                if kind=='return_connect':
                    if cause!=ret_token() or c['ret_mode']!='opening':continue
                    if c['generator_on']:ret_block('reserve_not_open');continue
                    margin,reason=self.return_health(state,c,True)
                    if margin<0:ret_block(reason);continue
                    c['ret_mode']='closing';request('grid-cb','close',cause);continue
                if kind=='transfer':
                    p=self.transfer
                    if c['source']:events.append(dict(time_s=float(t),kind='transfer_blocked',equipment_id=id_,reason='primary_not_open'))
                    elif not c['primary_valid']:events.append(dict(time_s=float(t),kind='transfer_blocked',equipment_id=id_,reason='primary_measurement_unavailable'))
                    elif not c['reserve_valid']:events.append(dict(time_s=float(t),kind='transfer_blocked',equipment_id=id_,reason='reserve_measurement_unavailable'))
                    elif abs(self.generator_emf(state))/self.gv<p.pickup_pu:events.append(dict(time_s=float(t),kind='transfer_blocked',equipment_id=id_,reason='reserve_unavailable'))
                    else:request(p.reserve_breaker_id,'close',cause)
                    continue
                b=self.breakers[id_];closing=kind=='close';check={}
                if kind=='failure':
                    if id_ not in c['failure_announced'] and c[b.target_kind] and self.currents(state,c)[b.target_kind]>b.breaker_failure_current_a:
                        c['failure_announced'].add(id_);events.append(dict(time_s=float(t),kind='breaker_failure_confirmed',equipment_id=id_))
                        for backup in sorted(b.backup_breaker_ids):request(backup,'open','breaker_failure:'+id_)
                    continue
                if cause.startswith('automatic_return:'):
                    if cause!=ret_token():
                        events.append(dict(time_s=float(t),kind='breaker_'+kind,equipment_id=id_,outcome='cancelled',reason='stale_return_cycle',cause=cause));continue
                    margin,reason=self.return_health(state,c,True)
                    if margin<0:
                        events.append(dict(time_s=float(t),kind='breaker_'+kind,equipment_id=id_,outcome='blocked',reason=reason,cause=cause));ret_block(reason);continue
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
                if outcome=='applied' and closing and id_=='reserve-cb' and cause=='transfer:ats':
                    c['ret_mode']='armed';c['ret_active']=False;state[self.return_address]=0.
                if closing and cause==ret_token():
                    if outcome=='applied':
                        c['ats_latched']=False;c['ats_active']=False;state[self.transfer_address]=0.
                        c['ret_mode']='armed';c['ret_active']=False;state[self.return_address]=0.
                        events.append(dict(time_s=float(t),kind='automatic_return_completed',equipment_id='ats',generation=c['ret_generation']))
                    elif outcome=='blocked':ret_block('close_'+check['reason'])
                release_held()
            refresh()
            for a,side in sorted(list(c['held'].items())):
                rate=raw(state,c,a)
                if (side=='lower' and rate>eps) or (side=='upper' and rate < -eps):c['held'].pop(a);log(a,side,True)
            if len(events)>before:boundaries.append((t,pre,pc,state.copy(),copy.deepcopy(c)))
            elif stop<right-1e-13:raise ValueError('root made no discrete progress')
        else:raise ValueError('hybrid event budget exceeded')
        self.segments,self.boundaries,self.events,self.initial=segments,boundaries,events,initial;return self
