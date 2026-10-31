"""One algebraic network for classical generators and induction motor dynamics.

GENCLS and six-state GENROU share a nominal-speed algebraic stator/network.
Optional TGOV1/SEXS/IEEEST MODE=1 are supported; islanding is outside this profile.
"""
from __future__ import annotations
import math
from typing import Literal
from dataclasses import dataclass
from power_grid_env.coupled_lines import CoupledLines,LINE_EVENTS,validate_line_events
import numpy as np
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import splu
from pydantic import Field, model_validator
from power_grid_env.induction import InductionEvent, InductionSettings
from power_grid_env.induction_network import InductionNetwork,validate_induction,integrate_induction_model
from power_grid_env.rms_study import ClassicalMachine, RoundRotorMachine, RMSFault
from power_grid_env.coupled_generators import RoundRotorBank
from power_grid_env.coupled_controls import CoupledControllerBank
from power_grid_env.model import validate_network_rules
from power_grid_env.topology import compile_topology


class CoupledEvent(InductionEvent):
    kind: Literal['source_voltage','motor_open','motor_close','load_torque_scale','generator_mechanical_step','governor_step','voltage_reference_step','line_open','line_close','generator_open']

    @model_validator(mode='after')
    def value_kind(self):
        if self.kind in {'generator_mechanical_step','governor_step','voltage_reference_step'}:
            if self.value is None:
                raise ValueError('generator mechanical step requires an additive pu value')
            return self
        return super().value_kind()


class CoupledFault(RMSFault):
    id: str = Field(min_length=1,max_length=128)


@dataclass(frozen=True)
class FaultTransition:
    time_s: float
    kind: str
    equipment_id: str


class CoupledSettings(InductionSettings):
    faults: list[CoupledFault] = Field(default_factory=list,max_length=100)
    machines: list[ClassicalMachine | RoundRotorMachine] = Field(min_length=1,max_length=8)
    events: list[CoupledEvent] = Field(default_factory=list,max_length=100)

    @model_validator(mode='after')
    def supported_machines(self):
        if any(m.model not in {'GENCLS','GENROU'} or (m.stabilizer is not None and (m.exciter is None or m.stabilizer.MODE!=1)) or (m.exciter is not None and m.exciter.EMIN<0) for m in self.machines):
            raise ValueError('coupled profile accepts GENCLS/GENROU with optional TGOV1/SEXS/IEEEST MODE=1 and nonnegative field limit')
        if len({f.id for f in self.faults})!=len(self.faults):
            raise ValueError('duplicate coupled fault ID')
        if any(f.clear_s>=self.duration_s for f in self.faults):
            raise ValueError('coupled fault must clear strictly before horizon')
        if len({m.generator_id for m in self.machines})!=len(self.machines):
            raise ValueError('duplicate generator passport')
        return self


def validate_coupled(network,settings):
    errors=validate_network_rules(network)
    topology=compile_topology(network)
    # Reuse the network/motor gates on a declared projection. Every removed
    # generator and generator event is checked explicitly below.
    projection=network.model_copy(deep=True)
    removed={g.terminal_id for g in projection.generators}
    projection.generators=[];projection.terminals=[t for t in projection.terminals if t.id not in removed]
    motor_settings=InductionSettings.model_validate({k:v for k,v in settings.model_dump(mode='json').items() if k not in {'machines','faults'}} | {
        'events':[e.model_dump(mode='json') for e in settings.events if e.kind not in {'generator_mechanical_step','governor_step','voltage_reference_step','generator_open',*LINE_EVENTS}]})
    scope=validate_induction(projection,motor_settings,validate_rules=False)
    errors.extend(scope['errors'])
    if any(m.model not in {'GENCLS','GENROU'} or (m.stabilizer is not None and (m.exciter is None or m.stabilizer.MODE!=1)) or (m.exciter is not None and m.exciter.EMIN<0) for m in settings.machines):
        errors.append('only GENCLS/GENROU with optional TGOV1/SEXS/IEEEST MODE=1 and nonnegative field limit')
    for machine in settings.machines:
        passport=RoundRotorMachine if machine.model=='GENROU' else ClassicalMachine
        try:passport.model_validate(machine.model_dump(mode='json'))
        except ValueError as exc:errors.append(f'{machine.generator_id}: invalid machine/controller passport: {exc}')
    if not 1<=len(settings.machines)<=8 or len({m.generator_id for m in settings.machines})!=len(settings.machines):
        errors.append('one to eight unique generator passports required')
    other_ids={item.id for group in (network.sources,network.lines,network.transformers,network.loads,network.switches) for item in group}
    if any(g.id in other_ids for g in network.generators):
        errors.append('generator ID collides with another canonical device')
    generators={g.id:g for g in network.generators}
    passports={m.generator_id:m for m in settings.machines}
    if set(generators)!=set(passports):
        errors.append('one synchronous-machine passport per canonical generator required')
    if any(m.id in generators for m in settings.motors):
        errors.append('motor ID collides with canonical generator')
    buses=[]
    for gen in network.generators:
        if gen.terminal_id not in topology.terminal_bus:
            errors.append('unknown generator terminal');continue
        bus=topology.terminal_bus[gen.terminal_id];buses.append(bus)
        if gen.mode!='pv' or set(gen.phases)!=set('ABC') or gen.v_set_pu is None or gen.v_set_pu<=0:
            errors.append(f'{gen.id}: balanced PV initialization required')
        if gen.participation is not None:
            errors.append(f'{gen.id}: distributed slack unsupported')
        if not all(math.isfinite(x) for x in (gen.p_w,gen.q_var,gen.ll_v,gen.v_set_pu or 0)):
            errors.append(f'{gen.id}: nonfinite generator data')
        if not math.isclose(gen.ll_v,topology.buses[bus].nominal_ll_v,rel_tol=1e-9):
            errors.append(f'{gen.id}: generator voltage must match bus base')
        if gen.v_set_pu is not None and not gen.v_min_pu<=gen.v_set_pu<=gen.v_max_pu:
            errors.append(f'{gen.id}: PV setpoint outside generator voltage limits')
        machine=passports.get(gen.id)
        if machine and gen.inertia_s is not None and not math.isclose(machine.inertia_s,gen.inertia_s):
            errors.append(f'{gen.id}: inconsistent inertia passports')
    if len(set(buses))!=len(buses):
        errors.append('multiple PV generators on one bus require a reactive sharing model')
    for event in settings.events:
        if event.kind in {'generator_mechanical_step','governor_step','voltage_reference_step'}:
            machine=passports.get(event.equipment_id)
            if machine is None:
                errors.append('unknown generator control target')
            elif event.kind=='generator_mechanical_step' and machine.governor is not None:
                errors.append('governed generator requires governor_step instead of mechanical bypass')
            elif event.kind=='governor_step' and machine.governor is None:
                errors.append('governor_step requires TGOV1')
            elif event.kind=='voltage_reference_step' and machine.exciter is None:
                errors.append('voltage_reference_step requires SEXS')
    opened=set()
    for event in sorted(settings.events,key=lambda e:e.time_s):
        if event.kind!='generator_open':continue
        if event.equipment_id not in passports:errors.append('unknown generator breaker target')
        elif event.equipment_id in opened:errors.append('redundant generator breaker opening')
        if event.value is not None:errors.append('generator breaker opening takes no value')
        opened.add(event.equipment_id)
    known_ids=other_ids|set(generators)|{m.id for m in settings.motors}|set(topology.buses)
    if len(settings.faults)>100 or len({f.id for f in settings.faults})!=len(settings.faults):
        errors.append('at most 100 unique coupled faults required')
    for fault in settings.faults:
        if fault.id in known_ids:errors.append('fault ID collides with device/bus/channel namespace')
        if fault.node_id not in topology.node_to_bus:
            errors.append('unknown coupled fault node');continue
        if not (0<fault.apply_s<fault.clear_s<settings.duration_s):
            errors.append('coupled fault must apply/clear strictly inside horizon')
        if not all(math.isfinite(x) and x>=0 for x in (fault.r_ohm,fault.x_ohm)) or fault.r_ohm+fault.x_ohm<=0:
            errors.append('coupled fault requires finite nonnegative nonzero impedance');continue
        bus=topology.buses[topology.node_to_bus[fault.node_id]]
        with np.errstate(over='ignore',divide='ignore',invalid='ignore'):
            admittance=np.divide(bus.nominal_ll_v**2/network.s_base_va,complex(fault.r_ohm,fault.x_ohm))
        if not np.isfinite(admittance) or abs(admittance)==0:
            errors.append('coupled fault admittance is not representable')
    errors.extend(validate_line_events(network,topology,settings.events))
    samples=(2*settings.maximum_contactor_transitions if any(m.contactor for m in settings.motors) else 0)+math.ceil(settings.duration_s/settings.step_s)+2*(len(settings.events)+2*len(settings.faults)+len(settings.motors))+2
    motor_channels=sum(16+8*(m.model=='double_cage')+2*bool(m.thermal)+6*bool(m.contactor) for m in settings.motors)
    if samples*(2*len(topology.buses)+motor_channels+42*len(settings.machines)+4*len(settings.faults)+9*len(network.lines)+6)>2_000_000:
        errors.append('coupled trace exceeds two-million-scalar budget')
    return {'ok':not errors,'errors':errors,'model':'coupled_synchronous_generators_single_or_double_cage_motors',
            'limitations':['positive sequence and algebraic stators','GENCLS/GENROU with fixed field or SEXS and optional IEEEST MODE=1',
                           'constant-Z background only','external slack required; electrical generator opening only, no reclose or islanding',
                           'declared motor mechanical/thermal subset','no field qualification']}


class CoupledNetwork(InductionNetwork):
    def __init__(self,network,settings):
        self.generator_ready=False
        super().__init__(network,settings)
        lookup={bus:i for i,bus in enumerate(self.bus_names)}
        passports={m.generator_id:m for m in settings.machines}
        self.generators=list(network.generators)
        self.gen_passports=[passports[g.id] for g in self.generators]
        self.gen_buses=np.array([lookup[self.topology.terminal_bus[g.terminal_id]] for g in self.generators])
        self.gen_z=np.array([complex(m.armature_r_pu,m.x_d_subtransient_pu) if m.model=='GENROU' else 1j*m.x_d_prime_pu for m in self.gen_passports])
        self.gen_factors=np.array([m.rated_va/network.s_base_va for m in self.gen_passports])
        self.gen_y=self.gen_factors/self.gen_z
        self.gen_connected=np.ones(len(self.generators),dtype=bool)
        self.gen_voltage=np.ones(len(self.generators))
        self.gen_mechanical=np.zeros(len(self.generators))
        self.gen_offset=self.motor_state_end
        self.rotor_end=self.gen_offset+2*len(self.generators)
        self.round_rotors=RoundRotorBank(self.gen_passports,self.rotor_end)
        self.controllers=CoupledControllerBank(self.gen_passports,self.round_rotors.end)
        self.fault_passports={f.id:f for f in settings.faults}
        self.fault_buses={f.id:lookup[self.topology.node_to_bus[f.node_id]] for f in settings.faults}
        self.active_faults=set()
        self.fault_y=np.zeros(len(self.bus_names),dtype=complex)
        self.lines=CoupledLines(network,self.topology,self.base_y,self.bus_names)
        self.generator_ready=True
        self.factorize()

    def factorize(self):
        if self.generator_ready:self.base_y=self.lines.passive_matrix()
        super().factorize()
        if not self.generator_ready:return
        matrix=self.matrix.tolil(copy=True)
        self.active_gen_y=np.where(self.gen_connected,self.gen_y,0j)
        for bus,y in zip(self.gen_buses,self.active_gen_y):matrix[bus,bus]+=y
        for bus,y in enumerate(self.fault_y):matrix[bus,bus]+=y
        self.matrix=csc_matrix(matrix);self.lu=splu(self.matrix)

    def generator_internal(self,state):
        rotor=state[self.gen_offset:self.rotor_end].reshape(-1,2)
        return self.round_rotors.internal(state,rotor,self.controllers.field(state,self.gen_voltage))

    def generator_currents(self,state,voltage):
        internal=self.generator_internal(state)
        return np.where(self.gen_connected,(internal-voltage[self.gen_buses])/self.gen_z,0j)

    def generator_terminal_voltage(self,state,voltage):
        return np.where(self.gen_connected,voltage[self.gen_buses],self.generator_internal(state))

    def electrical(self,state):
        values=np.asarray(state)[:self.electrical_size].reshape(-1,3)
        internal=values[:,0]+1j*values[:,1]
        rhs=np.zeros(len(self.bus_names),dtype=complex)
        rhs[self.source_bus]=self.source_y*self.source_e*self.source_scale
        np.add.at(rhs,self.motor_buses,self.motor_y*internal)
        np.add.at(rhs,self.gen_buses,self.active_gen_y*self.generator_internal(state))
        voltage=self.lu.solve(rhs)
        current=np.where(self.connected,(voltage[self.motor_buses]-internal)/self.zmotor,0j)
        torque=(internal*np.conj(current)).real
        return voltage,current,torque,float(max(abs(self.matrix@voltage-rhs)))

    def rhs(self,time,state):
        voltage,current,torque,_=self.electrical(state)
        motor=self.motor_derivatives(state,current,torque)
        internal=self.generator_internal(state)
        generator_current=self.generator_currents(state,voltage)
        electrical=(internal*np.conj(generator_current)).real
        rotor=state[self.gen_offset:self.rotor_end].reshape(-1,2)
        mechanical=self.controllers.mechanical(state,rotor[:,1],self.gen_mechanical)
        derivative=np.array([[2*math.pi*self.network.frequency_hz*(rotor[i,1]-1),
            (mechanical[i]-electrical[i]-m.damping_pu*(rotor[i,1]-1))/(2*m.inertia_s)]
            for i,m in enumerate(self.gen_passports)])
        return np.r_[motor,derivative.ravel(),
            self.round_rotors.derivatives(state,rotor,generator_current,self.controllers.field(state,self.gen_voltage)),
            self.controllers.derivatives(state,rotor[:,1],self.generator_terminal_voltage(state,voltage))]

    def static_electrical(self,speed,generator_internal,*,steady):
        response=self.cages.response(self.constants,speed) if steady else np.zeros(len(speed),dtype=complex)
        equivalent=self.zmotor+response
        ym=np.where(self.connected,self.factors/equivalent,0j)
        matrix=self.base_y.tolil(copy=True)
        for bus,y in zip(self.motor_buses,ym):matrix[bus,bus]+=y
        for bus,y in zip(self.gen_buses,self.gen_y):matrix[bus,bus]+=y
        rhs=np.zeros(len(self.bus_names),dtype=complex);rhs[self.source_bus]=self.source_y*self.source_e
        np.add.at(rhs,self.gen_buses,self.gen_y*generator_internal)
        voltage=splu(csc_matrix(matrix)).solve(rhs)
        current=np.where(self.connected,voltage[self.motor_buses]/equivalent,0j)
        ig=(generator_internal-voltage[self.gen_buses])/self.gen_z
        return voltage,current,response*current,ig

    def initial_state(self):
        return np.r_[np.zeros(self.electrical_size),[m.thermal.initial_level for m in self.settings.motors if m.thermal],np.zeros(self.motor_state_end-self.electrical_size-len(self.thermal_indices)),
                     np.tile([0.,1.],len(self.generators)),np.zeros(self.controllers.end-self.rotor_end)]

    def scheduled_events(self):
        transitions=[FaultTransition(t,kind,f.id) for f in self.settings.faults
                     for t,kind in [(f.apply_s,'fault_apply'),(f.clear_s,'fault_clear')]]
        # Commands and faults at a boundary are applied before one refactor/record.
        return [*self.settings.events,*transitions]

    def additional_passive_current(self,voltage):
        return self.fault_y*voltage

    def additional_channels(self):
        channels = {g.id+'.'+key:[] for g in self.generators for key in ('delta','omega','rotor_frequency_hz','p_w','q_var','current_pu',
            'bus_voltage_pu','electrical_power_pu','mechanical_power_pu','connected','terminal_voltage_pu','terminal_angle_rad')}
        channels.update({key:[] for key in self.controllers.channel_names()+self.round_rotors.channel_names()})
        channels.update({key:[] for key in self.lines.channels()})
        channels.update({f.id+'.'+key:[] for f in self.settings.faults for key in ['active','current_a','p_w','q_var']})
        return channels

    def additional_signals(self,state,voltage):
        rotor=state[self.gen_offset:self.rotor_end].reshape(-1,2)
        current=self.generator_currents(state,voltage)
        power=voltage[self.gen_buses]*np.conj(current)*np.array([m.rated_va for m in self.gen_passports])
        electrical=(self.generator_internal(state)*np.conj(current)).real
        mechanical=self.controllers.mechanical(state,rotor[:,1],self.gen_mechanical)
        terminal=self.generator_terminal_voltage(state,voltage)
        signals=self.controllers.signals(state,rotor[:,1],terminal,self.gen_mechanical)
        signals.update(self.round_rotors.signals(state,self.controllers.field(state,self.gen_voltage)))
        signals.update(self.lines.signals(voltage))
        for fault in self.settings.faults:
            bus=self.fault_buses[fault.id]
            ll=self.topology.buses[self.bus_names[bus]].nominal_ll_v
            v=voltage[bus]*ll/math.sqrt(3)
            fault_current=v/complex(fault.r_ohm,fault.x_ohm) if fault.id in self.active_faults else 0j
            power_fault=3*v*np.conj(fault_current)
            signals.update({fault.id+'.'+key:float(value) for key,value in dict(
                active=fault.id in self.active_faults,current_a=abs(fault_current),p_w=power_fault.real,q_var=power_fault.imag).items()})
        for i,g in enumerate(self.generators):
            values=dict(delta=rotor[i,0],omega=rotor[i,1],rotor_frequency_hz=rotor[i,1]*self.network.frequency_hz,
                p_w=power[i].real,q_var=power[i].imag,current_pu=abs(current[i]),bus_voltage_pu=abs(voltage[self.gen_buses[i]]),
                electrical_power_pu=electrical[i],mechanical_power_pu=mechanical[i],connected=float(self.gen_connected[i]),
                terminal_voltage_pu=abs(terminal[i]),terminal_angle_rad=np.angle(terminal[i]))
            signals.update({g.id+'.'+k:float(v) for k,v in values.items()})
        return signals,complex(sum(power))

    def apply_additional_event(self,event):
        if event.kind=='generator_open':
            i=next(i for i,g in enumerate(self.generators) if g.id==event.equipment_id)
            self.gen_connected[i]=False
            return {'outcome':'applied','connected':False,'semantics':'electrical breaker only; rotor, governor, field and PSS continue; sensing on machine side'}

        switched=self.lines.apply(event)
        if switched is not None:return switched
        if event.kind in {'fault_apply','fault_clear'}:
            fault=self.fault_passports[event.equipment_id]
            if event.kind=='fault_apply':self.active_faults.add(fault.id)
            else:self.active_faults.remove(fault.id)
            self.fault_y[:]=0.
            for id_ in sorted(self.active_faults):
                f=self.fault_passports[id_];bus=self.fault_buses[id_]
                ll=self.topology.buses[self.bus_names[bus]].nominal_ll_v
                self.fault_y[bus]+=ll**2/self.network.s_base_va/complex(f.r_ohm,f.x_ohm)
            return {'outcome':'applied','node_id':fault.node_id,'r_ohm':fault.r_ohm,'x_ohm':fault.x_ohm}

        if event.kind not in {'generator_mechanical_step','governor_step','voltage_reference_step'}:return None
        i=next(i for i,g in enumerate(self.generators) if g.id==event.equipment_id)
        if event.kind=='governor_step':self.controllers.pref[i]+=event.value
        elif event.kind=='voltage_reference_step':self.controllers.vref[i]+=event.value
        else:self.gen_mechanical[i]+=event.value
        return {'outcome':'applied','value':event.value,'units':'additive pu voltage' if event.kind=='voltage_reference_step' else 'additive pu on generator VA base'}

    def integration_jacobian(self):
        # Full synchronous-machine/controller Jacobian is a separate implementation.
        return None

    def observe_accepted_states(self,states):
        self.controllers.observe_accepted_states(states)

    def validate_additional_channels(self,channels):
        return self.controllers.validate_channels(channels,self.settings.rtol,self.settings.atol)

    def additional_metadata(self):
        return {'initialization':'PV-initialized synchronous generators; '+('loaded motor equilibrium' if self.settings.operating_point else 'cold motor flux and speed'),
                'generator_mapping':{g.id:self.bus_names[int(bus)] for g,bus in zip(self.generators,self.gen_buses)},
                'generator_units':{'delta':'rad','omega':'pu synchronous speed','rotor_frequency_hz':'rotor electrical Hz, not bus frequency',
                    'terminal_voltage_pu':'machine-side terminal pu; equals open-circuit EMF when breaker open','terminal_angle_rad':'wrapped machine-side phase radians',
                    'electrical_power_pu':'pu own generator VA base','mechanical_power_pu':'pu own generator VA base'},
                'generator_model':'GENCLS/GENROU nominal-speed algebraic stator; fixed field or explicit SEXS',
                'machine_models':{g.id:m.model for g,m in zip(self.generators,self.gen_passports)},
                'fault_units':{'current_a':'RMS phase A','p_w':'three-phase W','q_var':'three-phase var','active':'0/1'},
                'generator_breaker_semantics':'electrical disconnection only; controls sense machine-side terminal; no turbine/field trip or reclose',
                'controller_units':{'TGOV1':'own generator VA-base power/valve; speed droop input pu','SEXS':'pu voltage/field'}}


def run_coupled(network,settings):
    scope=validate_coupled(network,settings)
    if not scope['ok']:
        return {'solved':False,'completed':False,'status':'rejected','reason':'coupled_scope','scope':scope}
    try:
        model=CoupledNetwork(network,settings)
        from power_grid_env.coupled_initialization import initialize_coupled
        state,initial=initialize_coupled(model)
    except (ValueError,RuntimeError,ArithmeticError,np.linalg.LinAlgError) as exc:
        return {'solved':False,'completed':False,'status':'initialization_failed','reason':'coupled_initialization','error':str(exc)}
    if state is None:
        return {'solved':False,'completed':False,'status':'initialization_failed','reason':initial['reason'],'operating_point':initial,'scope':scope}
    return integrate_induction_model(model,scope,state,initial,study='coupled_rms')
