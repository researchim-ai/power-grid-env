"""One algebraic network for classical generators and induction motor dynamics.

The generator is fixed-|E| behind jXd', with the speed-independent classical
stator approximation. Optional explicit TGOV1/SEXS are supported; PSS/GENROU/islanding are outside this profile.
"""
from __future__ import annotations
import math
from typing import Literal
import numpy as np
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import splu
from pydantic import Field, model_validator
from power_grid_env.induction import InductionEvent, InductionSettings
from power_grid_env.induction_network import InductionNetwork,validate_induction,integrate_induction_model
from power_grid_env.rms_study import ClassicalMachine
from power_grid_env.coupled_controls import CoupledControllerBank
from power_grid_env.model import validate_network_rules
from power_grid_env.topology import compile_topology


class CoupledEvent(InductionEvent):
    kind: Literal['source_voltage','motor_open','motor_close','load_torque_scale','generator_mechanical_step','governor_step','voltage_reference_step']

    @model_validator(mode='after')
    def value_kind(self):
        if self.kind in {'generator_mechanical_step','governor_step','voltage_reference_step'}:
            if self.value is None:
                raise ValueError('generator mechanical step requires an additive pu value')
            return self
        return super().value_kind()


class CoupledSettings(InductionSettings):
    machines: list[ClassicalMachine] = Field(min_length=1,max_length=8)
    events: list[CoupledEvent] = Field(default_factory=list,max_length=100)

    @model_validator(mode='after')
    def classical_only(self):
        if any(m.model!='GENCLS' or m.stabilizer or (m.exciter is not None and m.exciter.EMIN<0) for m in self.machines):
            raise ValueError('coupled profile accepts GENCLS with optional TGOV1/SEXS, nonnegative field limit and no PSS')
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
    motor_settings=InductionSettings.model_validate({k:v for k,v in settings.model_dump(mode='json').items() if k!='machines'} | {
        'events':[e.model_dump(mode='json') for e in settings.events if e.kind not in {'generator_mechanical_step','governor_step','voltage_reference_step'}]})
    scope=validate_induction(projection,motor_settings,validate_rules=False)
    errors.extend(scope['errors'])
    if any(m.model!='GENCLS' or m.stabilizer or (m.exciter is not None and m.exciter.EMIN<0) for m in settings.machines):
        errors.append('only GENCLS with optional TGOV1/SEXS and nonnegative field limit; no PSS')
    if not 1<=len(settings.machines)<=8 or len({m.generator_id for m in settings.machines})!=len(settings.machines):
        errors.append('one to eight unique generator passports required')
    other_ids={item.id for group in (network.sources,network.lines,network.transformers,network.loads,network.switches) for item in group}
    if any(g.id in other_ids for g in network.generators):
        errors.append('generator ID collides with another canonical device')
    generators={g.id:g for g in network.generators}
    passports={m.generator_id:m for m in settings.machines}
    if set(generators)!=set(passports):
        errors.append('one classical passport per canonical generator required')
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
    samples=math.ceil(settings.duration_s/settings.step_s)+2*(len(settings.events)+len(settings.motors))+2
    if samples*(2*len(topology.buses)+14*len(settings.motors)+20*len(settings.machines)+6)>2_000_000:
        errors.append('coupled trace exceeds two-million-scalar budget')
    return {'ok':not errors,'errors':errors,'model':'coupled_classical_generators_single_cage_motors',
            'limitations':['positive sequence and algebraic stators','GENCLS fixed field or SEXS; no PSS/GENROU',
                           'constant-Z background only','external slack required; no generator trip/reclose or islanding',
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
        self.gen_z=np.array([1j*m.x_d_prime_pu for m in self.gen_passports])
        self.gen_factors=np.array([m.rated_va/network.s_base_va for m in self.gen_passports])
        self.gen_y=self.gen_factors/self.gen_z
        self.gen_voltage=np.ones(len(self.generators))
        self.gen_mechanical=np.zeros(len(self.generators))
        self.gen_offset=self.electrical_size+len(self.thermal_indices)
        self.controllers=CoupledControllerBank(self.gen_passports,self.gen_offset+2*len(self.generators))
        self.generator_ready=True
        self.factorize()

    def factorize(self):
        super().factorize()
        if not self.generator_ready:return
        matrix=self.matrix.tolil(copy=True)
        for bus,y in zip(self.gen_buses,self.gen_y):matrix[bus,bus]+=y
        self.matrix=csc_matrix(matrix);self.lu=splu(self.matrix)

    def generator_internal(self,state):
        rotor=state[self.gen_offset:self.controllers.offset].reshape(-1,2)
        return self.controllers.field(state,self.gen_voltage)*np.exp(1j*rotor[:,0])

    def generator_currents(self,state,voltage):
        internal=self.generator_internal(state)
        return (internal-voltage[self.gen_buses])/self.gen_z

    def electrical(self,state):
        values=np.asarray(state)[:self.electrical_size].reshape(-1,3)
        internal=values[:,0]+1j*values[:,1]
        rhs=np.zeros(len(self.bus_names),dtype=complex)
        rhs[self.source_bus]=self.source_y*self.source_e*self.source_scale
        np.add.at(rhs,self.motor_buses,self.motor_y*internal)
        np.add.at(rhs,self.gen_buses,self.gen_y*self.generator_internal(state))
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
        rotor=state[self.gen_offset:self.controllers.offset].reshape(-1,2)
        mechanical=self.controllers.mechanical(state,rotor[:,1],self.gen_mechanical)
        derivative=np.array([[2*math.pi*self.network.frequency_hz*(rotor[i,1]-1),
            (mechanical[i]-electrical[i]-m.damping_pu*(rotor[i,1]-1))/(2*m.inertia_s)]
            for i,m in enumerate(self.gen_passports)])
        return np.r_[motor,derivative.ravel(),self.controllers.derivatives(state,rotor[:,1],voltage[self.gen_buses])]

    def static_electrical(self,speed,generator_internal,*,steady):
        response=np.array([1j*c[1]/(1+1j*2*math.pi*self.network.frequency_hz*c[2]*(1-w)) for c,w in zip(self.constants,speed)]) if steady else np.zeros(len(speed),dtype=complex)
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
        return np.r_[np.zeros(self.electrical_size),[m.thermal.initial_level for m in self.settings.motors if m.thermal],
                     np.tile([0.,1.],len(self.generators)),np.zeros(self.controllers.end-self.controllers.offset)]

    def additional_channels(self):
        channels = {g.id+'.'+key:[] for g in self.generators for key in ('delta','omega','rotor_frequency_hz','p_w','q_var','current_pu',
            'bus_voltage_pu','electrical_power_pu','mechanical_power_pu','connected')}
        channels.update({key:[] for key in self.controllers.channel_names()})
        return channels

    def additional_signals(self,state,voltage):
        rotor=state[self.gen_offset:self.controllers.offset].reshape(-1,2)
        current=self.generator_currents(state,voltage)
        power=voltage[self.gen_buses]*np.conj(current)*np.array([m.rated_va for m in self.gen_passports])
        electrical=(self.generator_internal(state)*np.conj(current)).real
        mechanical=self.controllers.mechanical(state,rotor[:,1],self.gen_mechanical)
        signals=self.controllers.signals(state,rotor[:,1],voltage[self.gen_buses],self.gen_mechanical)
        for i,g in enumerate(self.generators):
            values=dict(delta=rotor[i,0],omega=rotor[i,1],rotor_frequency_hz=rotor[i,1]*self.network.frequency_hz,
                p_w=power[i].real,q_var=power[i].imag,current_pu=abs(current[i]),bus_voltage_pu=abs(voltage[self.gen_buses[i]]),
                electrical_power_pu=electrical[i],mechanical_power_pu=mechanical[i],connected=1.)
            signals.update({g.id+'.'+k:float(v) for k,v in values.items()})
        return signals,complex(sum(power))

    def apply_additional_event(self,event):
        if event.kind not in {'generator_mechanical_step','governor_step','voltage_reference_step'}:return None
        i=next(i for i,g in enumerate(self.generators) if g.id==event.equipment_id)
        if event.kind=='governor_step':self.controllers.pref[i]+=event.value
        elif event.kind=='voltage_reference_step':self.controllers.vref[i]+=event.value
        else:self.gen_mechanical[i]+=event.value
        return {'outcome':'applied','value':event.value,'units':'additive pu voltage' if event.kind=='voltage_reference_step' else 'additive pu on generator VA base'}

    def observe_accepted_states(self,states):
        self.controllers.observe_accepted_states(states)

    def validate_additional_channels(self,channels):
        return self.controllers.validate_channels(channels,self.settings.rtol,self.settings.atol)

    def additional_metadata(self):
        return {'initialization':'PV-initialized classical generators; '+('loaded motor equilibrium' if self.settings.operating_point else 'cold motor flux and speed'),
                'generator_mapping':{g.id:self.bus_names[int(bus)] for g,bus in zip(self.generators,self.gen_buses)},
                'generator_units':{'delta':'rad','omega':'pu synchronous speed','rotor_frequency_hz':'rotor electrical Hz, not bus frequency',
                    'electrical_power_pu':'pu own generator VA base','mechanical_power_pu':'pu own generator VA base'},
                'generator_model':'classical speed-independent stator, fixed field or explicit SEXS',
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
