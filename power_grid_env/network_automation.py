"""Continuous relay progress, finite breaker actions and safe RMS island transitions.

Relays integrate actual local RMS measurements. Breaker requests, failed actions,
backup trips and one-way break-before-make transfer are distinct recorded events.
No instantaneous-current, arc, communication-security or OEM-equivalence claim.
"""
import heapq,math
from typing import Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.protection import iec_operate_time
from power_grid_env.coupled_lines import line_graph


class SynchronismLimits(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    maximum_angle_rad: float=Field(default=math.pi/18,gt=0,le=math.pi)
    maximum_voltage_difference_pu: float=Field(default=.1,gt=0,le=.5)
    maximum_frequency_difference_hz: float=Field(default=.1,gt=0,le=5)
    minimum_live_voltage_pu: float=Field(default=.1,gt=0,le=.5)
    allow_dead_receiver: bool=True
    minimum_incoming_voltage_pu: float=Field(default=.8,gt=0,le=1.5)
    maximum_incoming_voltage_pu: float=Field(default=1.2,gt=0,le=2.)
    maximum_incoming_frequency_deviation_hz: float=Field(default=1.,gt=0,le=10.)

    @model_validator(mode='after')
    def voltage_range(self):
        if self.maximum_incoming_voltage_pu<=self.minimum_incoming_voltage_pu:raise ValueError('invalid incoming voltage range')
        return self


def synchronism(left,right,left_hz,right_hz,limits,*,incoming_supply=True,nominal_hz=50.):
    """Signed peak/RMS-independent per-unit phasors, with explicit live validity."""
    if not np.isfinite([left.real,left.imag,right.real,right.imag]).all():return dict(allowed=False,reason='invalid_voltage_measurement')
    live_left=abs(left)>=limits.minimum_live_voltage_pu;live_right=abs(right)>=limits.minimum_live_voltage_pu
    result=dict(left_voltage_pu=float(abs(left)),right_voltage_pu=float(abs(right)),voltage_difference_pu=float(abs(abs(left)-abs(right))),left_frequency_hz=left_hz,right_frequency_hz=right_hz)
    if incoming_supply and not live_left:return result|dict(allowed=False,reason='incoming_supply_dead')
    if incoming_supply:
        if not limits.minimum_incoming_voltage_pu<=abs(left)<=limits.maximum_incoming_voltage_pu:return result|dict(allowed=False,reason='incoming_voltage_range')
        if left_hz is None or not np.isfinite(left_hz) or abs(left_hz-nominal_hz)>limits.maximum_incoming_frequency_deviation_hz:return result|dict(allowed=False,reason='incoming_frequency_range')
    if not live_left or not live_right:
        return result|dict(allowed=limits.allow_dead_receiver,reason='dead_receiver' if incoming_supply else 'dead_network_side')
    if left_hz is None or right_hz is None or not np.isfinite([left_hz,right_hz]).all():return result|dict(allowed=False,reason='invalid_frequency_measurement')
    angle=float(abs(np.angle(left*np.conj(right))));frequency=abs(left_hz-right_hz);result.update(angle_difference_rad=angle,frequency_difference_hz=frequency)
    reason='synchronized'
    if result['voltage_difference_pu']>limits.maximum_voltage_difference_pu:reason='voltage_difference'
    elif frequency>limits.maximum_frequency_difference_hz:reason='frequency_difference'
    elif angle>limits.maximum_angle_rad:reason='angle_difference'
    return result|dict(allowed=reason=='synchronized',reason=reason)


class RMSBreaker(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    id: str=Field(min_length=1)
    target_kind: Literal['line','generator','source']
    target_id: str=Field(min_length=1)
    opening_delay_s: float=Field(default=0.,ge=0,le=10)
    closing_delay_s: float=Field(default=0.,ge=0,le=10)
    minimum_open_s: float=Field(default=.02,gt=0,le=60)
    initial_open: bool=False
    fail_open: bool=False
    fail_close: bool=False
    breaker_failure_delay_s: float=Field(default=.1,gt=0,le=10)
    breaker_failure_current_a: float=Field(default=1.,ge=0)
    backup_breaker_ids: list[str]=Field(default_factory=list,max_length=16)
    reclose_dead_s: float=Field(default=.2,gt=0,le=60)
    maximum_reclose_shots: int=Field(default=0,ge=0,le=8)
    interlock_with: list[str]=Field(default_factory=list,max_length=16)
    synchronism: SynchronismLimits=Field(default_factory=SynchronismLimits)


class NetworkRelay(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    id: str=Field(min_length=1)
    kind: Literal['overcurrent','undervoltage']
    measurement_kind: Literal['line','generator','source','node']
    measurement_id: str=Field(min_length=1)
    breaker_id: str=Field(min_length=1)
    pickup: float=Field(gt=0)
    curve: Literal['definite','standard_inverse','very_inverse','extremely_inverse']='definite'
    time_multiplier_s: float=Field(gt=0)
    reset_s: float=Field(default=.05,gt=0)
    dropout_ratio: float=Field(default=.95,gt=0,lt=1)
    directional: bool=False
    minimum_forward_power_factor: float=Field(default=.01,gt=0,lt=1)
    minimum_directional_voltage_pu: float=Field(default=.05,gt=0,lt=1)
    measurement_available: bool=True

    @model_validator(mode='after')
    def applicable(self):
        if self.kind=='undervoltage' and (self.curve!='definite' or self.directional):raise ValueError('undervoltage relay requires nondirectional definite time')
        if self.kind=='overcurrent' and self.measurement_kind=='node':raise ValueError('overcurrent measurement requires a physical equipment port')
        return self


class SupplyVoltage(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind: Literal['source','generator','node']
    id: str


class TransferScheme(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    id: str=Field(min_length=1)
    primary_breaker_id: str
    reserve_breaker_id: str
    primary_voltage: SupplyVoltage
    reserve_voltage: SupplyVoltage
    dropout_pu: float=Field(default=.7,gt=0,lt=1)
    pickup_pu: float=Field(default=.9,gt=0,le=1.5)
    detection_delay_s: float=Field(default=.1,gt=0,le=60)
    dead_time_s: float=Field(default=.05,gt=0,le=60)

    @model_validator(mode='after')
    def hysteresis(self):
        if self.pickup_pu<=self.dropout_pu or self.primary_breaker_id==self.reserve_breaker_id:raise ValueError('transfer requires hysteresis and distinct breakers')
        return self


class NetworkAutomation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    allow_islands: bool=True
    breakers: list[RMSBreaker]=Field(default_factory=list,max_length=64)
    relays: list[NetworkRelay]=Field(default_factory=list,max_length=64)
    transfers: list[TransferScheme]=Field(default_factory=list,max_length=8)

    @model_validator(mode='after')
    def identities(self):
        ids=[p.id for group in [self.breakers,self.relays,self.transfers] for p in group]
        if len(set(ids))!=len(ids):raise ValueError('unique automation/channel IDs required')
        breakers={b.id:b for b in self.breakers}
        if len({(b.target_kind,b.target_id) for b in self.breakers})!=len(self.breakers):raise ValueError('one actuator per equipment port required')
        for b in self.breakers:
            if b.initial_open and b.target_kind=='source':raise ValueError('initial source opening is not supported by the grid-connected initialization profile')
            if any(x not in breakers or x==b.id for x in b.backup_breaker_ids+b.interlock_with):raise ValueError('invalid breaker backup or interlock')
            if len(set(b.backup_breaker_ids))!=len(b.backup_breaker_ids) or len(set(b.interlock_with))!=len(b.interlock_with):raise ValueError('duplicate backup or interlock')
        visiting=set();done=set()
        def visit(id_):
            if id_ in visiting:raise ValueError('breaker-failure backup graph must be acyclic')
            if id_ in done:return
            visiting.add(id_)
            for other in breakers[id_].backup_breaker_ids:visit(other)
            visiting.remove(id_);done.add(id_)
        for b in breakers:visit(b)
        if any(r.breaker_id not in breakers for r in self.relays):raise ValueError('relay requires a known breaker')
        if any(t.primary_breaker_id not in breakers or t.reserve_breaker_id not in breakers for t in self.transfers):raise ValueError('transfer requires known breakers')
        return self


AUTOMATION_EVENTS={'generator_close','source_open','source_close','breaker_open','breaker_close','relay_reset','ats_reset','relay_measurement_lost','relay_measurement_restored'}


def validate_automation(network,topology,settings):
    p=settings.network_automation
    if p is None:return ['network automation events require an explicit passport'] if any(e.kind in AUTOMATION_EVENTS for e in settings.events) else []
    errors=[]
    if settings.integration_method!='radau' or settings.jacobian_network!='cached_ports' or settings.event_policy!='coincident_roots_v1':
        errors.append('network automation v1 requires Radau, cached_ports and coincident_roots_v1; analytic DAE relay derivatives are not qualified')
    targets=dict(line={x.id for x in network.lines},generator={x.id for x in network.generators},source={x.id for x in network.sources},node=set(topology.node_to_bus))
    ids=set().union(*targets.values(),{x.id for x in settings.motors})
    for x in [*p.breakers,*p.relays,*p.transfers]:
        if x.id in ids:errors.append('automation ID collides with network equipment')
    for b in p.breakers:
        if b.target_id not in targets[b.target_kind]:errors.append('unknown breaker target')
    for r in p.relays:
        if r.measurement_id not in targets[r.measurement_kind]:errors.append('unknown relay measurement')
    for t in p.transfers:
        for port in [t.primary_voltage,t.reserve_voltage]:
            if port.id not in targets[port.kind]:errors.append('unknown transfer voltage measurement')
        by_id={b.id:b for b in p.breakers}
        for port,breaker_id in [(t.primary_voltage,t.primary_breaker_id),(t.reserve_voltage,t.reserve_breaker_id)]:
            breaker=by_id[breaker_id]
            if port.kind!='node' and (port.kind,port.id)!=(breaker.target_kind,breaker.target_id):
                errors.append('transfer supply voltage must match its controlled supply port; use an explicit node for local PT sensing')
    lookup={(b.target_kind,b.target_id):b.id for b in p.breakers};breakers={b.id for b in p.breakers};seen=set()
    for e in settings.events:
        if e.kind in {'source_open','source_close','generator_close','line_close'}:
            kind=e.kind.split('_')[0]
            if (kind,e.equipment_id) not in lookup:errors.append('new source/generator command requires a configured actuator')
        if e.kind in {'breaker_open','breaker_close'} and e.equipment_id not in breakers:errors.append('unknown breaker command')
        if e.kind in {'relay_reset','relay_measurement_lost','relay_measurement_restored'} and e.equipment_id not in {r.id for r in p.relays}:errors.append('unknown relay reset')
        if e.kind=='ats_reset' and e.equipment_id not in {t.id for t in p.transfers}:errors.append('unknown transfer reset')
        key=None
        if e.kind in {'breaker_open','breaker_close'}:key=e.equipment_id
        elif e.kind.endswith(('_open','_close')):
            kind=e.kind.split('_')[0];key=lookup.get((kind,e.equipment_id))
        if key is not None:
            if (e.time_s,key) in seen:errors.append('conflicting simultaneous actuator commands')
            seen.add((e.time_s,key))
    from power_grid_env.coupled_lines import disconnected_buses
    closed={line.id:True for line in network.lines}
    for b in p.breakers:
        if b.initial_open and b.target_kind=='line' and b.target_id in closed:closed[b.target_id]=False
        if b.initial_open and b.target_kind=='generator':
            g=next((g for g in network.generators if g.id==b.target_id),None)
            if g is not None and g.p_w!=0:errors.append('initially disconnected generator requires zero specified active power')
    if disconnected_buses(network,topology,closed):errors.append('initial topology must remain source-reachable; island transitions are supported during the study')
    return errors


def voltage_derivatives(model,time,state):
    """Analytic Norton injection derivative at fixed discrete topology."""
    from power_grid_env.dae import injection_product
    f=model.physical_rhs(time,state)
    return model.lu.solve(injection_product(model,state,f[:,None])[:,0]),f


def generator_emf_derivative(model,state,f):
    from power_grid_env.genrou_jacobian import clip_slope
    eg=model.generator_internal(state);result=np.zeros(len(eg),complex)
    for i,p in enumerate(model.gen_passports):
        delta=model.gen_offset+2*i;result[i]=1j*eg[i]*f[delta]
        if i in model.round_rotors.addresses:
            a=model.round_rotors.addresses[i];gd1,gd2,gq1,_,_=model.round_rotors.coefficients[i]
            dpd=gd1*f[a.start]+gd2*(p.x_d_prime_pu-p.leakage_pu)*f[a.start+2];dpq=gq1*f[a.start+1]+(1-gq1)*f[a.start+3]
            result[i]+=(dpd-1j*dpq)*np.exp(1j*state[delta])
        elif i in model.controllers.avr:
            _,field=model.controllers.avr[i];result[i]+=clip_slope(state[field],p.exciter.EMIN,p.exciter.EMAX)*f[field]*np.exp(1j*state[delta])
    return result


class NetworkAutomationBank:
    def __init__(self,model,offset):
        self.model=model;self.p=model.settings.network_automation;self.breakers={b.id:b for b in sorted(self.p.breakers,key=lambda x:x.id)}
        self.relays=sorted(self.p.relays,key=lambda x:x.id);self.transfers=sorted(self.p.transfers,key=lambda x:x.id)
        self.offset=offset;self.end=offset+len(self.relays)+len(self.transfers);self.active=[False]*len(self.relays);self.tripped=[False]*len(self.relays)
        self.ats_active=[False]*len(self.transfers);self.ats_latched=[False]*len(self.transfers);self.initialized=False;self.in_service=False;self.available={p.id:p.measurement_available for p in self.relays}
        self.queue=[];self.keys=set();self.failure_announced=set();self.opened_at={};self.manual_inhibit=set();self.shots={id_:0 for id_ in self.breakers}
        self.open_attempts={id_:0 for id_ in self.breakers};self.close_attempts={id_:0 for id_ in self.breakers}
        self.targets={(b.target_kind,b.target_id):b.id for b in self.breakers.values()}
        self.gen_index={g.id:i for i,g in enumerate(model.generators)};self.bus_index={b:i for i,b in enumerate(model.bus_names)}
        for b in self.breakers.values():
            if b.initial_open:self.set_position(b,False)
        self.interlocks={id_:set(b.interlock_with) for id_,b in self.breakers.items()}
        for t in self.transfers:
            self.interlocks[t.primary_breaker_id].add(t.reserve_breaker_id);self.interlocks[t.reserve_breaker_id].add(t.primary_breaker_id)

    def position(self,b):
        m=self.model
        if b.target_kind=='line':return m.lines.closed[b.target_id]
        if b.target_kind=='source':return m.source_connected
        return bool(m.gen_connected[self.gen_index[b.target_id]])

    def set_position(self,b,closed):
        m=self.model
        if b.target_kind=='line':m.lines.closed[b.target_id]=closed
        elif b.target_kind=='source':m.source_connected=closed
        else:m.gen_connected[self.gen_index[b.target_id]]=closed

    def port(self,kind,id_,state,voltage):
        m=self.model
        if kind=='node':return voltage[self.bus_index[m.topology.node_to_bus[id_]]],None
        if kind=='source':
            v=m.source_e*m.source_scale;current=m.source_y*(v-voltage[m.source_bus]);ll=m.topology.buses[m.bus_names[m.source_bus]].nominal_ll_v
            return v,current*m.network.s_base_va/(math.sqrt(3)*ll)
        if kind=='generator':
            i=self.gen_index[id_];return m.generator_terminal_voltage(state,voltage)[i],m.generator_currents(state,voltage)[i]*m.gen_passports[i].rated_va/(math.sqrt(3)*m.generators[i].ll_v)
        a,b,y,shunt,ll=m.lines.parameters[id_];current=((voltage[a]-voltage[b])*y+voltage[a]*shunt/2) if m.lines.closed[id_] else 0j
        return voltage[a],current*m.network.s_base_va/(math.sqrt(3)*ll)

    def measurement(self,p,state,voltage):
        if not self.available[p.id]:return 0.,False
        v,i=self.port(p.measurement_kind,p.measurement_id,state,voltage)
        if p.kind=='undervoltage':return float(abs(v)),True
        return float(abs(i)),True

    def margin(self,i,state,voltage,*,active=None):
        p=self.relays[i];value,valid=self.measurement(p,state,voltage)
        if not valid:return -1.
        on=self.active[i] if active is None else active
        if p.kind=='overcurrent':
            margin=value-p.pickup*(p.dropout_ratio if on else 1.)
            if p.directional:
                v,current=self.port(p.measurement_kind,p.measurement_id,state,voltage)
                factor=p.dropout_ratio if on else 1.;cosine=float((v*np.conj(current)).real/max(abs(v)*abs(current),1e-20))
                margin=min(margin,(cosine-p.minimum_forward_power_factor*factor)*p.pickup,(abs(v)-p.minimum_directional_voltage_pu*factor)*p.pickup)
            return margin
        return p.pickup/(p.dropout_ratio if on else 1.)-value

    def supply(self,port,state,voltage):return float(abs(self.port(port.kind,port.id,state,voltage)[0]))

    def initialize(self,state):self.initialized=True;self.refresh(0.,state,[])

    def refresh(self,time,state,event_log):
        if not self.initialized:return
        voltage=self.model.electrical(state)[0]
        for i,p in enumerate(self.relays):
            value=self.margin(i,state,voltage)
            eps=64*np.finfo(float).eps*max(1.,p.pickup)
            on=self.active[i]
            if (not on and value>eps) or (on and value<-eps):
                self.active[i]=not on;event_log.append(dict(time_s=float(time),kind='network_relay_pickup' if not on else 'network_relay_dropout',equipment_id=p.id))
        for i,p in enumerate(self.transfers):
            if self.ats_latched[i]:continue
            value=self.supply(p.primary_voltage,state,voltage);threshold=p.pickup_pu if self.ats_active[i] else p.dropout_pu
            if (not self.ats_active[i] and value<threshold-1e-12) or (self.ats_active[i] and value>threshold+1e-12):
                self.ats_active[i]=not self.ats_active[i];event_log.append(dict(time_s=float(time),kind='transfer_voltage_lost' if self.ats_active[i] else 'transfer_voltage_restored',equipment_id=p.id))

    def derivatives(self,time,state,voltage):
        result=np.zeros(self.end-self.offset)
        if not self.initialized:return result
        for i,p in enumerate(self.relays):
            if self.tripped[i]:continue
            if self.active[i]:
                value,valid=self.measurement(p,state,voltage)
                if valid:result[i]=1/p.time_multiplier_s if p.kind=='undervoltage' else 1/iec_operate_time(value,p.pickup,p.curve,p.time_multiplier_s)
            else:result[i]=-state[self.offset+i]/p.reset_s
        for i,p in enumerate(self.transfers):
            if not self.ats_latched[i]:result[len(self.relays)+i]=1/p.detection_delay_s if self.ats_active[i] else -state[self.offset+len(self.relays)+i]/p.detection_delay_s
        return result

    def guards(self,state):
        if not self.initialized:return [],[]
        ids=[];guards=[]
        def add(id_,fn,direction):fn.direction=direction;fn.terminal=True;ids.append(id_);guards.append(fn)
        for i,p in enumerate(self.relays):
            def pickup(time,x,i=i):return self.margin(i,x,self.model.electrical(x)[0])
            add(('network_pickup',i),pickup,-1 if self.active[i] else 1)
            if self.active[i] and not self.tripped[i]:
                def trip(time,x,i=i):return x[self.offset+i]-1.
                add(('network_trip',i),trip,1)
        for i,p in enumerate(self.transfers):
            if self.ats_latched[i]:continue
            threshold=p.pickup_pu if self.ats_active[i] else p.dropout_pu
            def voltage(time,x,i=i,p=p,threshold=threshold):return threshold-self.supply(p.primary_voltage,x,self.model.electrical(x)[0])
            add(('transfer_pickup',i),voltage,-1 if self.ats_active[i] else 1)
            if self.ats_active[i]:
                def due(time,x,i=i):return x[self.offset+len(self.relays)+i]-1.
                add(('transfer_trip',i),due,1)
        return ids,guards

    def enqueue(self,time,kind,id_,cause):
        key=(float(time),kind,id_)
        if key in self.keys:return
        self.keys.add(key);priority={'open':0,'failure':1,'transfer':2,'close':3}[kind];heapq.heappush(self.queue,(float(time),priority,kind,id_,cause))

    def request(self,id_,closed,time,cause,event_log):
        b=self.breakers[id_];delay=b.closing_delay_s if closed else b.opening_delay_s
        if cause=='manual' and not closed:self.manual_inhibit.add(id_)
        self.enqueue(time+delay,'close' if closed else 'open',id_,cause)
        event_log.append(dict(time_s=float(time),kind='breaker_close_requested' if closed else 'breaker_open_requested',equipment_id=id_,cause=cause,due_s=float(time+delay)))

    def apply(self,roots,time,state,event_log):
        for key in sorted(roots):
            if key[0] not in {'network_pickup','network_trip','transfer_pickup','transfer_trip'}:continue
            kind,i=key
            if kind=='network_pickup':
                self.active[i]=not self.active[i];event_log.append(dict(time_s=float(time),kind='network_relay_pickup' if self.active[i] else 'network_relay_dropout',equipment_id=self.relays[i].id))
            elif kind=='network_trip' and not self.tripped[i]:
                p=self.relays[i];self.tripped[i]=True;state[self.offset+i]=1.
                event_log.append(dict(time_s=float(time),kind='network_relay_trip',equipment_id=p.id,breaker_id=p.breaker_id));self.request(p.breaker_id,False,time,'relay:'+p.id,event_log)
            elif kind=='transfer_pickup':self.ats_active[i]=not self.ats_active[i]
            elif kind=='transfer_trip' and not self.ats_latched[i]:
                p=self.transfers[i];self.ats_latched[i]=True;state[self.offset+len(self.relays)+i]=1.
                event_log.append(dict(time_s=float(time),kind='transfer_started',equipment_id=p.id));self.request(p.primary_breaker_id,False,time,'transfer:'+p.id,event_log)
                self.enqueue(time+self.breakers[p.primary_breaker_id].opening_delay_s+p.dead_time_s,'transfer',p.id,'transfer:'+p.id)

    def next_deadline(self,limit):return min(float(limit),self.queue[0][0]) if self.queue else float(limit)
    def due(self,time):return bool(self.queue and self.queue[0][0]<=time+64*np.finfo(float).eps*max(1.,abs(time)))

    def check_close(self,b,time,state):
        for other in self.interlocks[b.id]:
            if self.position(self.breakers[other]):return dict(allowed=False,reason='interlocked_breaker_closed',interlock_id=other)
        if time-self.opened_at.get(b.id,-math.inf)<b.minimum_open_s-1e-12:return dict(allowed=False,reason='minimum_open_time')
        m=self.model;voltage=m.electrical(state)[0];dv,f=voltage_derivatives(m,time,state);fn=m.network.frequency_hz
        def frequency(v,change):return float(fn+(change/v).imag/(2*math.pi)) if abs(v)>=b.synchronism.minimum_live_voltage_pu else None
        if b.target_kind=='source':left=m.source_e*m.source_scale;right=voltage[m.source_bus];lf=fn;rf=frequency(right,dv[m.source_bus])
        elif b.target_kind=='generator':
            i=self.gen_index[b.target_id]
            if m.generator_thermal.latched.get(i,False) or m.field_thermal.latched.get(i,False):return dict(allowed=False,reason='generator_thermal_latch')
            left=m.generator_internal(state)[i];right=voltage[m.gen_buses[i]];de=generator_emf_derivative(m,state,f)[i];lf=frequency(left,de);rf=frequency(right,dv[m.gen_buses[i]])
        else:
            a,c,*_=m.lines.parameters[b.target_id];left=voltage[a];right=voltage[c];lf=frequency(left,dv[a]);rf=frequency(right,dv[c])
        return synchronism(left,right,lf,rf,b.synchronism,incoming_supply=b.target_kind!='line',nominal_hz=fn)

    def target_current(self,b,state):return float(abs(self.port(b.target_kind,b.target_id,state,self.model.electrical(state)[0])[1]))

    def service(self,time,state,event_log):
        if self.in_service:return
        self.in_service=True;operations=0
        try:
            while self.due(time):
                operations+=1
                if operations>256:raise ValueError('network automation cascade budget exceeded')
                deadline,_,kind,id_,cause=heapq.heappop(self.queue);self.keys.discard((deadline,kind,id_))
                if kind=='transfer':
                    p=next(p for p in self.transfers if p.id==id_)
                    if self.position(self.breakers[p.primary_breaker_id]):event_log.append(dict(time_s=float(time),kind='transfer_blocked',equipment_id=id_,reason='primary_not_open'));continue
                    voltage=self.model.electrical(state)[0]
                    if self.supply(p.reserve_voltage,state,voltage)<p.pickup_pu:event_log.append(dict(time_s=float(time),kind='transfer_blocked',equipment_id=id_,reason='reserve_unavailable'));continue
                    self.request(p.reserve_breaker_id,True,time,cause,event_log);continue
                b=self.breakers[id_]
                if kind=='failure':
                    if id_ not in self.failure_announced and self.position(b) and self.target_current(b,state)>b.breaker_failure_current_a:
                        self.failure_announced.add(id_);event_log.append(dict(time_s=float(time),kind='breaker_failure_confirmed',equipment_id=id_))
                        for backup in sorted(b.backup_breaker_ids):self.request(backup,False,time,'breaker_failure:'+id_,event_log)
                    continue
                closed=kind=='close'
                if closed and cause!='manual' and id_ in self.manual_inhibit:
                    event_log.append(dict(time_s=float(time),kind='breaker_close',equipment_id=id_,outcome='blocked',reason='manual_inhibit',cause=cause));continue
                if self.position(b)==closed:
                    if closed and cause=='manual':self.manual_inhibit.discard(id_)
                    event_log.append(dict(time_s=float(time),kind='breaker_'+kind,equipment_id=id_,outcome='already_in_requested_state',cause=cause));continue
                if closed:
                    self.close_attempts[id_]+=1;check=self.check_close(b,time,state)
                    if not check['allowed']:
                        event_log.append(dict(time_s=float(time),kind='breaker_close',equipment_id=id_,outcome='blocked',synchronism=check,cause=cause));continue
                else:self.open_attempts[id_]+=1
                failed=b.fail_close if closed else b.fail_open
                if failed:
                    event_log.append(dict(time_s=float(time),kind='breaker_'+kind,equipment_id=id_,outcome='failed_to_operate',cause=cause))
                    if not closed:self.enqueue(time+b.breaker_failure_delay_s,'failure',id_,cause)
                    continue
                previous=self.position(b);self.set_position(b,closed)
                try:self.model.factorize()
                except (ValueError,RuntimeError,ArithmeticError):self.set_position(b,previous);self.model.factorize();raise
                event_log.append(dict(time_s=float(time),kind='breaker_'+kind,equipment_id=id_,outcome='applied',cause=cause,**({'synchronism':check} if closed else {})))
                if not closed:
                    self.opened_at[id_]=float(time)
                    if cause.startswith('relay:') and self.shots[id_]<b.maximum_reclose_shots:
                        self.shots[id_]+=1;self.enqueue(time+max(b.reclose_dead_s,b.minimum_open_s),'close',id_,'automatic_reclose')
                else:
                    if cause=='manual':self.manual_inhibit.discard(id_)
                    self.failure_announced.discard(id_)
                    for i,p in enumerate(self.relays):
                        if p.breaker_id==id_:self.tripped[i]=False;self.active[i]=False;state[self.offset+i]=0.
                self.model.after_discrete_commands(time,state,event_log)
            self.refresh(time,state,event_log)
        finally:self.in_service=False

    def command(self,event,time,state,event_log):
        kind=event.kind;id_=event.equipment_id
        if kind in {'breaker_open','breaker_close'}:
            self.request(id_,kind=='breaker_close',time,'manual',event_log);return dict(outcome='queued')
        if kind in {'line_open','line_close','generator_open','generator_close','source_open','source_close'}:
            target,action=kind.split('_');breaker=self.targets.get((target,id_))
            if breaker is not None:self.request(breaker,action=='close',time,'manual',event_log);return dict(outcome='queued',breaker_id=breaker)
        if kind in {'relay_measurement_lost','relay_measurement_restored'}:
            self.available[id_]=kind=='relay_measurement_restored';return dict(outcome='applied',measurement_available=self.available[id_])
        if kind=='relay_reset':
            i=next(i for i,p in enumerate(self.relays) if p.id==id_);self.tripped[i]=False;self.active[i]=False;state[self.offset+i]=0.;return dict(outcome='applied')
        if kind=='ats_reset':
            i=next(i for i,p in enumerate(self.transfers) if p.id==id_);self.ats_latched[i]=False;self.ats_active[i]=False;state[self.offset+len(self.relays)+i]=0.;return dict(outcome='applied')
        return None

    def channels(self):
        names=[]
        for p in self.relays:names.extend(p.id+'.'+k for k in ['progress','active','latched','measurement','measurement_valid'])
        for id_ in self.breakers:names.extend(id_+'.'+k for k in ['closed','open_attempts','close_attempts','reclose_shots','manual_inhibit'])
        for p in self.transfers:names.extend(p.id+'.'+k for k in ['progress','active','latched'])
        names.extend([self.model.network.sources[0].id+'.connected','network.zero_reference_components'])
        return names

    def signals(self,time,state,voltage):
        out={}
        for i,p in enumerate(self.relays):
            value,valid=self.measurement(p,state,voltage)
            for key,x in [('progress',state[self.offset+i]),('active',self.active[i]),('latched',self.tripped[i]),('measurement',value),('measurement_valid',valid)]:out[p.id+'.'+key]=float(x)
        for id_,p in self.breakers.items():
            for key,x in [('closed',self.position(p)),('open_attempts',self.open_attempts[id_]),('close_attempts',self.close_attempts[id_]),('reclose_shots',self.shots[id_]),('manual_inhibit',id_ in self.manual_inhibit)]:out[id_+'.'+key]=float(x)
        for i,p in enumerate(self.transfers):
            for key,x in [('progress',state[self.offset+len(self.relays)+i]),('active',self.ats_active[i]),('latched',self.ats_latched[i])]:out[p.id+'.'+key]=float(x)
        out[self.model.network.sources[0].id+'.connected']=float(self.model.source_connected);out['network.zero_reference_components']=float(len(self.model.zero_reference_buses))
        return out


class DeenergizedNodalSolve:
    """Zero solution of unexcited passive components; original Y is retained.

    This is a coordinate restriction, not an added physical shunt. A nonzero
    injection into an eliminated component is rejected rather than discarded.
    """
    def __init__(self,matrix,dead):
        from scipy.sparse.linalg import splu
        self.shape=matrix.shape;self.dtype=matrix.dtype;self.dead=np.asarray(sorted(dead),int)
        self.live=np.asarray([i for i in range(matrix.shape[0]) if i not in set(dead)],int)
        self.factor=splu(matrix[self.live,:][:,self.live]) if len(self.live) else None

    def solve(self,rhs,*args,**kwargs):
        rhs=np.asarray(rhs)
        if rhs.ndim not in (1,2) or rhs.shape[0]!=self.shape[0] or not np.isfinite(rhs).all():raise ValueError('invalid reduced nodal RHS')
        if np.any(rhs[self.dead]!=0):raise ValueError('nonzero injection into deenergized passive component')
        out=np.zeros(rhs.shape,dtype=np.result_type(rhs.dtype,self.dtype))
        if self.factor is not None:out[self.live]=self.factor.solve(rhs[self.live],*args,**kwargs)
        return out
