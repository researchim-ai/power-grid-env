"""One algebraic network for classical generators and induction motor dynamics.

GENCLS and six-state GENROU share a nominal-speed algebraic stator/network.
Optional TGOV1/SEXS/IEEEST MODE=1 are supported; islanding is outside this profile.
"""
from __future__ import annotations
import math
from typing import Literal
from dataclasses import dataclass
from power_grid_env.coupled_lines import CoupledLines,LINE_EVENTS,validate_line_events,line_graph
from power_grid_env.network_automation import NetworkAutomation,NetworkAutomationBank,AUTOMATION_EVENTS,validate_automation,DeenergizedNodalSolve
import numpy as np
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import splu
from pydantic import BaseModel,ConfigDict,Field,model_validator,model_serializer
from power_grid_env.induction import InductionEvent, InductionSettings
from power_grid_env.induction_network import InductionNetwork,validate_induction,integrate_induction_model
from power_grid_env.rms_study import ClassicalMachine, RoundRotorMachine, RMSFault
from power_grid_env.coupled_generators import RoundRotorBank
from power_grid_env.coupled_controls import CoupledControllerBank
from power_grid_env.controller_boundaries import ControllerBoundaries
from power_grid_env.generator_field_thermal import GeneratorFieldThermalProtection,FieldThermalBank
from power_grid_env.generator_excitation import OverexcitationLimiter,ExcitationLimiterBank
from power_grid_env.generator_underexcitation import UnderexcitationLimiter,MinimumExcitationBank
from power_grid_env.excitation_takeover import TakeoverOELPassport,TakeoverOELBank
from power_grid_env.exciter_esst1a import ESST1APassport,ESST1ABank,PORTS as ESST_PORTS
from power_grid_env.generator_oel2c import OEL2CPassport,OEL2CBank,CHANNELS as OEL2C_CHANNELS
from power_grid_env.generator_thermal import GeneratorThermalProtection,GeneratorThermalBank
from power_grid_env.model import validate_network_rules
from power_grid_env.topology import compile_topology


class CoupledEvent(InductionEvent):
    kind: Literal['source_voltage','motor_open','motor_close','load_torque_scale','generator_mechanical_step','governor_step','voltage_reference_step','line_open','line_close','generator_open','generator_close','source_open','source_close','breaker_open','breaker_close','relay_reset','ats_reset','relay_measurement_lost','relay_measurement_restored']

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


class ESST1APortEvent(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    time_s: float=Field(gt=0)
    kind: Literal['esst1a_port_set']='esst1a_port_set'
    equipment_id: str=Field(min_length=1)
    port: Literal['oel_ceiling_pu','uel_error_floor_pu','uel_field_floor_pu','uel_sum_pu','supplementary_voltage_pu','supplementary_field_pu']
    value: float

    @model_validator(mode='after')
    def absolute_field_inputs(self):
        if self.port in ('oel_ceiling_pu','uel_field_floor_pu') and self.value<0:raise ValueError('absolute takeover field limits must be nonnegative')
        return self


class CoupledSettings(InductionSettings):
    network_automation: NetworkAutomation | None=None
    monitored_lines: list[str] | None=Field(default=None,max_length=20000)
    takeover_oels: list[TakeoverOELPassport] = Field(default_factory=list,max_length=32)
    esst1a_exciters: list[ESST1APassport] = Field(default_factory=list,max_length=32)
    generator_field_thermal: list[GeneratorFieldThermalProtection] = Field(default_factory=list,max_length=32)
    oel2c_limiters: list[OEL2CPassport] = Field(default_factory=list,max_length=32)
    underexcitation_limiters: list[UnderexcitationLimiter] = Field(default_factory=list,max_length=32)
    overexcitation_limiters: list[OverexcitationLimiter] = Field(default_factory=list,max_length=32)
    generator_thermal: list[GeneratorThermalProtection] = Field(default_factory=list,max_length=32)
    faults: list[CoupledFault] = Field(default_factory=list,max_length=100)
    machines: list[ClassicalMachine | RoundRotorMachine] = Field(min_length=1,max_length=32)
    events: list[CoupledEvent | ESST1APortEvent] = Field(default_factory=list,max_length=512)

    @model_serializer(mode='wrap')
    def canonical_coupled_settings(self,handler):
        data=super().canonical_solver_settings(handler)
        if self.monitored_lines is None:data.pop('monitored_lines',None)
        if self.network_automation is None:data.pop('network_automation',None)
        return data

    @model_validator(mode='after')
    def supported_machines(self):
        limit=8 if self.capacity_profile=='standard' else 32
        if len(self.machines)>limit:raise ValueError('generator capacity exceeds selected profile')
        for items in [self.takeover_oels,self.esst1a_exciters,self.generator_field_thermal,self.oel2c_limiters,self.underexcitation_limiters,self.overexcitation_limiters,self.generator_thermal]:
            if len(items)>limit:raise ValueError('controller/protection capacity exceeds selected profile')
        if any(m.model not in {'GENCLS','GENROU'} or (m.stabilizer is not None and ((m.exciter is None and m.stabilizer_connection!='external_ESST1A') or m.stabilizer.MODE!=1)) or (m.exciter is not None and m.exciter.EMIN<0) for m in self.machines):
            raise ValueError('coupled profile accepts GENCLS/GENROU with optional TGOV1/SEXS/IEEEST MODE=1 and nonnegative field limit')
        if self.monitored_lines is not None and len(set(self.monitored_lines))!=len(self.monitored_lines):raise ValueError('unique monitored line IDs required')
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
    motor_settings=InductionSettings.model_validate({k:v for k,v in settings.model_dump(mode='json').items() if k not in {'network_automation','monitored_lines','machines','faults','generator_thermal','overexcitation_limiters','generator_field_thermal','underexcitation_limiters','oel2c_limiters','esst1a_exciters','takeover_oels'}} | {
        'events':[e.model_dump(mode='json') for e in settings.events if e.kind not in {'generator_mechanical_step','governor_step','voltage_reference_step','generator_open','esst1a_port_set',*AUTOMATION_EVENTS,*LINE_EVENTS}]})
    scope=validate_induction(projection,motor_settings,validate_rules=False)
    errors.extend(scope['errors'])
    if any(m.model not in {'GENCLS','GENROU'} or (m.stabilizer is not None and ((m.exciter is None and m.stabilizer_connection!='external_ESST1A') or m.stabilizer.MODE!=1)) or (m.exciter is not None and m.exciter.EMIN<0) for m in settings.machines):
        errors.append('only GENCLS/GENROU with optional TGOV1/SEXS/IEEEST MODE=1 and nonnegative field limit')
    for machine in settings.machines:
        passport=RoundRotorMachine if machine.model=='GENROU' else ClassicalMachine
        try:passport.model_validate(machine.model_dump(mode='json'))
        except ValueError as exc:errors.append(f'{machine.generator_id}: invalid machine/controller passport: {exc}')
    generator_limit=8 if settings.capacity_profile=='standard' else 32
    if not 1<=len(settings.machines)<=generator_limit or len({m.generator_id for m in settings.machines})!=len(settings.machines):
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
    esst_ids={p.generator_id for p in settings.esst1a_exciters}
    if len(esst_ids)!=len(settings.esst1a_exciters):errors.append('unique ESST1A targets required')
    for p in settings.esst1a_exciters:
        machine=passports.get(p.generator_id)
        if machine is None or machine.model!='GENROU' or machine.exciter is not None:errors.append('ESST1A requires GENROU without simultaneous SEXS')
        if p.generator_id in {x.generator_id for x in settings.oel2c_limiters+settings.overexcitation_limiters}:errors.append('summation OEL passports cannot bind to the absolute ESST1A takeover port')
    for m in settings.machines:
        if m.stabilizer is not None and m.stabilizer_connection=='external_ESST1A' and (m.generator_id not in esst_ids or m.exciter is not None):errors.append('external ESST1A stabilizer requires an exclusive ESST1A target')
        if m.stabilizer is not None and m.generator_id in esst_ids and m.stabilizer_connection!='external_ESST1A':errors.append('ESST1A stabilizer connection must be explicit')
    takeover_ids=[p.generator_id for p in settings.takeover_oels]
    if len(set(takeover_ids))!=len(takeover_ids) or not set(takeover_ids)<=esst_ids:errors.append('unique takeover OEL targets must have ESST1A')
    for e in settings.events:
        if e.kind=='esst1a_port_set' and e.equipment_id not in esst_ids:errors.append('ESST1A port event requires an ESST1A target')
    field_ids=[]
    oel_by_id={p.generator_id:p for p in settings.overexcitation_limiters}
    for p in settings.generator_field_thermal:
        try:GeneratorFieldThermalProtection.model_validate(p.model_dump())
        except ValueError as exc:errors.append('invalid field thermal passport: '+str(exc))
        field_ids.append(p.generator_id);m=passports.get(p.generator_id)
        if m is None or m.model!='GENROU':errors.append('field thermal protection requires an existing GENROU')
        if p.initialization=='specified' and p.initial_level>=p.trip_level:errors.append('initial field thermal level must be below trip')
        other=oel_by_id.get(p.generator_id)
        if other and not math.isclose(other.rated_field_reaction_pu,p.rated_field_reaction_pu,rel_tol=1e-12):errors.append('OEL and rotor thermal rated field bases must agree')
    if len(set(field_ids))!=len(field_ids) or len(field_ids)>generator_limit:errors.append('unique field thermal passports required')
    oel_ids=[]
    for p in settings.overexcitation_limiters:
        try:OverexcitationLimiter.model_validate(p.model_dump())
        except ValueError as exc:errors.append('invalid OEL passport: '+str(exc))
        oel_ids.append(p.generator_id)
        m=passports.get(p.generator_id)
        if m is None or m.model!='GENROU' or m.exciter is None:errors.append('OEL requires an existing GENROU with SEXS')
    if len(set(oel_ids))!=len(oel_ids) or len(oel_ids)>generator_limit:errors.append('unique OEL passports required')
    uel_ids=[]
    for p in settings.underexcitation_limiters:
        try:UnderexcitationLimiter.model_validate(p.model_dump())
        except ValueError as exc:errors.append('invalid UEL passport: '+str(exc))
        uel_ids.append(p.generator_id);m=passports.get(p.generator_id)
        if m is None or m.model!='GENROU' or (m.exciter is None and p.generator_id not in esst_ids):errors.append('UEL requires an existing GENROU with SEXS')
    if len(set(uel_ids))!=len(uel_ids) or len(uel_ids)>generator_limit:errors.append('unique UEL passports required')
    if settings.operating_point and any(p.initialization!='inactive_equilibrium' for p in settings.underexcitation_limiters):errors.append('UEL loaded operating point requires inactive_equilibrium initialization')
    oel2c_ids=[]
    for p in settings.oel2c_limiters:
        try:OEL2CPassport.model_validate(p.model_dump())
        except ValueError as exc:errors.append('invalid OEL2C passport: '+str(exc))
        oel2c_ids.append(p.generator_id);m=passports.get(p.generator_id)
        if m is None or m.model!='GENROU' or m.exciter is None:errors.append('OEL2C requires an existing GENROU with SEXS')
        if p.generator_id in oel_by_id:errors.append('one OEL implementation per generator required')
        field=next((q for q in settings.generator_field_thermal if q.generator_id==p.generator_id),None)
        if field and not math.isclose(p.rated_field_reaction_pu,field.rated_field_reaction_pu,rel_tol=1e-12):errors.append('OEL2C and field thermal rated field bases must agree')
    if len(set(oel2c_ids))!=len(oel2c_ids) or len(oel2c_ids)>generator_limit:errors.append('unique OEL2C passports required')
    if settings.operating_point and any(p.initialization=='dynawo_startup' for p in settings.oel2c_limiters):errors.append('OEL2C dynawo_startup is not a full loaded equilibrium profile')
    if not settings.operating_point and any(p.initialization in ('active_limited_equilibrium','current_regulating_equilibrium','selected_equilibrium') for p in settings.oel2c_limiters):errors.append('active OEL2C equilibrium requires an explicit loaded operating point')
    thermal_ids=[]
    for p in settings.generator_thermal:
        try:GeneratorThermalProtection.model_validate(p.model_dump())
        except ValueError as exc:errors.append('invalid generator thermal passport: '+str(exc))
        thermal_ids.append(p.generator_id)
        if p.generator_id not in generators:errors.append('unknown generator thermal target')
        if p.initialization=='specified' and p.initial_level>=p.trip_level:errors.append('initial generator thermal level must be below trip')
    if len(set(thermal_ids))!=len(thermal_ids) or len(thermal_ids)>generator_limit:errors.append('unique generator thermal passports required')
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
            elif event.kind=='voltage_reference_step' and machine.exciter is None and event.equipment_id not in esst_ids:
                errors.append('voltage_reference_step requires SEXS')
    opened=set()
    for event in sorted(settings.events,key=lambda e:e.time_s):
        if event.kind!='generator_open':continue
        if event.equipment_id not in passports:errors.append('unknown generator breaker target')
        elif settings.network_automation is None and event.equipment_id in opened:errors.append('redundant generator breaker opening')
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
    errors.extend(validate_line_events(network,topology,settings.events,allow_islands=bool(settings.network_automation and settings.network_automation.allow_islands)))
    errors.extend(validate_automation(network,topology,settings))
    known_lines={line.id for line in network.lines}
    if settings.monitored_lines is not None and (not set(settings.monitored_lines)<=known_lines or not {e.equipment_id for e in settings.events if e.kind in LINE_EVENTS}<=set(settings.monitored_lines)):errors.append('monitored lines must exist and cover all operated lines')
    scheduled_times={e.time_s for e in settings.events}|{t for f in settings.faults for t in (f.apply_s,f.clear_s)}
    thermal_crossings=sum(m.thermal is not None for m in settings.motors)+len(settings.generator_thermal)+len(settings.generator_field_thermal)
    samples=(2*settings.maximum_contactor_transitions if any(m.contactor for m in settings.motors) else 0)+math.ceil(settings.duration_s/settings.step_s)+2*(len(scheduled_times)+thermal_crossings)+2
    motor_channels=sum(16+8*(m.model=='double_cage')+2*bool(m.thermal)+6*bool(m.contactor) for m in settings.motors)
    if samples*(2*len(topology.buses)+motor_channels+42*len(settings.machines)+2*len(settings.generator_thermal)+(len(OEL2C_CHANNELS)+2)*len(settings.oel2c_limiters)+40*len(settings.esst1a_exciters)+8*len(settings.underexcitation_limiters)+6*len(settings.overexcitation_limiters)+4*len(settings.generator_field_thermal)+4*len(settings.faults)+9*(len(network.lines) if settings.monitored_lines is None else len(settings.monitored_lines))+6)>2_000_000:
        errors.append('coupled trace exceeds two-million-scalar budget')
    return {'ok':not errors,'errors':errors,'model':'coupled_synchronous_generators_single_or_double_cage_motors',
            'limitations':['positive sequence and algebraic stators','GENCLS/GENROU with fixed field, SEXS, or declared five-state ESST1A; IEEEST MODE=1 with SEXS or explicitly connected ESST1A',
                           'constant-Z background only','external slack required; electrical generator opening only, no reclose or islanding' if settings.network_automation is None else 'grid-connected initialization; dynamic balanced islands, residual-flux coast and synchrochecked reconnection; no line or breaker EMT',
                           'declared motor mechanical/thermal subset','no field qualification']}


class CoupledNetwork(InductionNetwork):
    def __init__(self,network,settings):
        self.generator_ready=False;self.source_connected=True;self.automation=None;self.zero_reference_buses=[]
        super().__init__(network,settings)
        self._original_source_y=self.source_y
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
        self.generator_thermal=GeneratorThermalBank(self,self.controllers.end)
        self.excitation_limiters=ExcitationLimiterBank(self,self.generator_thermal.end)
        self.field_thermal=FieldThermalBank(self,self.excitation_limiters.end)
        self.underexcitation_limiters=MinimumExcitationBank(self,self.field_thermal.end)
        self.oel2c=OEL2CBank(self,self.underexcitation_limiters.end)
        self.esst1a=ESST1ABank(self,self.oel2c.end)
        self.takeover_oel=TakeoverOELBank(self,self.esst1a.end)
        self.controller_boundaries=ControllerBoundaries(self)
        self.fault_passports={f.id:f for f in settings.faults}
        self.fault_buses={f.id:lookup[self.topology.node_to_bus[f.node_id]] for f in settings.faults}
        self.active_faults=set()
        self.fault_y=np.zeros(len(self.bus_names),dtype=complex)
        self.lines=CoupledLines(network,self.topology,self.base_y,self.bus_names,allow_islands=bool(settings.network_automation and settings.network_automation.allow_islands))
        if settings.network_automation is not None:self.automation=NetworkAutomationBank(self,self.takeover_oel.end)
        self.dynamic_end=self.automation.end if self.automation is not None else self.takeover_oel.end
        self.generator_ready=True
        self.factorize()

    def factorize(self):
        if self.generator_ready and self.automation is not None:
            return self.factorize_islands()
        if self.generator_ready:self.base_y=self.lines.passive_matrix()
        super().factorize()
        if not self.generator_ready:return
        matrix=self.matrix.tolil(copy=True)
        self.active_gen_y=np.where(self.gen_connected,self.gen_y,0j)
        for bus,y in zip(self.gen_buses,self.active_gen_y):matrix[bus,bus]+=y
        for bus,y in enumerate(self.fault_y):matrix[bus,bus]+=y
        self.matrix=csc_matrix(matrix);self.lu=splu(self.matrix)

    def factorize_islands(self):
        if not self.settings.network_automation.allow_islands and not self.source_connected:raise ValueError('source disconnection requires the island profile')
        self.base_y=self.lines.passive_matrix()
        base=self.base_y.tolil(copy=True)
        self.source_y=self._original_source_y if self.source_connected else 0j
        if not self.source_connected:base[self.source_bus,self.source_bus]-=self._original_source_y
        self.base_y=base.tocsc();matrix=base.copy()
        self.motor_y=np.where(self.connected,self.factors/self.zmotor,0j)
        self.active_gen_y=np.where(self.gen_connected,self.gen_y,0j)
        for bus,y in zip(self.motor_buses,self.motor_y):matrix[bus,bus]+=y
        for bus,y in zip(self.gen_buses,self.active_gen_y):matrix[bus,bus]+=y
        for bus,y in enumerate(self.fault_y):matrix[bus,bus]+=y
        graph=line_graph(self.network,self.topology,self.lines.closed);remaining=set(graph);self.zero_reference_buses=[];dead=[]
        driven={self.bus_names[int(bus)] for bus,on in zip(self.gen_buses,self.gen_connected) if on}|{self.bus_names[int(bus)] for bus,on in zip(self.motor_buses,self.connected) if on}
        if self.source_connected:driven.add(self.bus_names[self.source_bus])
        while remaining:
            seed=min(remaining);component={seed};todo=[seed]
            while todo:
                for bus in graph[todo.pop()]-component:component.add(bus);todo.append(bus)
            remaining-=component
            if not component&driven:
                self.zero_reference_buses.append(min(component));dead.extend(self.bus_names.index(bus) for bus in component)
        self.matrix=matrix.tocsc();self.lu=DeenergizedNodalSolve(self.matrix,dead) if dead else splu(self.matrix);self._motor_current_response=None

    def excitation_bias(self,state):
        return self.excitation_limiters.bias(state)-self.underexcitation_limiters.output(state)-self.oel2c.output(state)

    def field_voltage(self,state,voltage=None,current=None):
        field=self.controllers.field(state,self.gen_voltage)
        if self.esst1a.entries:
            for i,value in self.esst1a.output(state,voltage,current).items():field[i]=value
        return field*self.field_thermal.field_enabled

    def generator_internal(self,state):
        rotor=state[self.gen_offset:self.rotor_end].reshape(-1,2)
        return self.round_rotors.internal(state,rotor,self.controllers.field(state,self.gen_voltage))

    def generator_currents(self,state,voltage):
        internal=self.generator_internal(state)
        return np.where(self.gen_connected,(internal-voltage[self.gen_buses])/self.gen_z,0j)

    def generator_terminal_voltage(self,state,voltage):
        return np.where(self.gen_connected,voltage[self.gen_buses],self.generator_internal(state))

    def network_injection(self,state):
        values=np.asarray(state)[:self.electrical_size].reshape(-1,3)
        internal=values[:,0]+1j*values[:,1]
        rhs=np.zeros(len(self.bus_names),dtype=complex)
        rhs[self.source_bus]=self.source_y*self.source_e*self.source_scale
        np.add.at(rhs,self.motor_buses,self.motor_y*internal)
        np.add.at(rhs,self.gen_buses,self.active_gen_y*self.generator_internal(state))
        return rhs

    def electrical(self,state):
        injection=self.network_injection(state)
        return self.electrical_at_voltage(state,self.lu.solve(injection),injection=injection)

    def rhs(self,time,state):
        voltage,current,torque,_=self.electrical(state)
        return self._differential(time,state,voltage,current,torque)

    def differential_at_voltage(self,time,state,voltage):
        voltage,current,torque,_=self.electrical_at_voltage(state,voltage)
        return self._differential(time,state,voltage,current,torque)

    def physical_rhs(self,time,state):
        voltage,current,torque,_=self.electrical(state)
        return self._differential(time,state,voltage,current,torque,include_automation=False)

    def _differential(self,time,state,voltage,current,torque,*,include_automation=True):
        motor=self.motor_derivatives(state,current,torque)
        internal=self.generator_internal(state)
        generator_current=self.generator_currents(state,voltage)
        electrical=(internal*np.conj(generator_current)).real
        rotor=state[self.gen_offset:self.rotor_end].reshape(-1,2)
        mechanical=self.controllers.mechanical(state,rotor[:,1],self.gen_mechanical)
        derivative=np.array([[2*math.pi*self.network.frequency_hz*(rotor[i,1]-1),
            (mechanical[i]-electrical[i]-m.damping_pu*(rotor[i,1]-1))/(2*m.inertia_s)]
            for i,m in enumerate(self.gen_passports)])
        result=self.controller_boundaries.derivative(np.r_[motor,derivative.ravel(),
            self.round_rotors.derivatives(state,rotor,generator_current,self.field_voltage(state,voltage,generator_current)),
            self.controllers.derivatives(state,rotor[:,1],self.generator_terminal_voltage(state,voltage),self.excitation_bias(state)),
            self.generator_thermal.derivatives(state,generator_current),
            self.excitation_limiters.derivatives(state,generator_current),
            self.field_thermal.derivatives(state,generator_current),self.underexcitation_limiters.derivatives(state,voltage,generator_current),self.oel2c.derivatives(state,generator_current),self.esst1a.derivatives(state,voltage,generator_current),self.takeover_oel.derivatives(state,generator_current)])
        if self.automation is not None:
            extra=self.automation.derivatives(time,state,voltage) if include_automation else np.zeros(self.automation.end-self.automation.offset)
            return np.r_[result,extra]
        return result

    def static_electrical(self,speed,generator_internal,*,steady):
        response=self.cages.response(self.constants,speed) if steady else np.zeros(len(speed),dtype=complex)
        equivalent=self.zmotor+response
        ym=np.where(self.connected,self.factors/equivalent,0j)
        matrix=self.base_y.tolil(copy=True)
        for bus,y in zip(self.motor_buses,ym):matrix[bus,bus]+=y
        for bus,y in zip(self.gen_buses,np.where(self.gen_connected,self.gen_y,0j)):matrix[bus,bus]+=y
        rhs=np.zeros(len(self.bus_names),dtype=complex);rhs[self.source_bus]=self.source_y*self.source_e
        np.add.at(rhs,self.gen_buses,np.where(self.gen_connected,self.gen_y,0j)*generator_internal)
        voltage=splu(csc_matrix(matrix)).solve(rhs)
        current=np.where(self.connected,voltage[self.motor_buses]/equivalent,0j)
        ig=np.where(self.gen_connected,(generator_internal-voltage[self.gen_buses])/self.gen_z,0j)
        return voltage,current,response*current,ig

    def initial_state(self):
        return np.r_[np.zeros(self.electrical_size),[m.thermal.initial_level for m in self.settings.motors if m.thermal],np.zeros(self.motor_state_end-self.electrical_size-len(self.thermal_indices)),
                     np.tile([0.,1.],len(self.generators)),np.zeros(getattr(self,'dynamic_end',self.takeover_oel.end)-self.rotor_end)]

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
        channels.update({key:[] for key in self.controllers.channel_names()+self.round_rotors.channel_names()+self.generator_thermal.channel_names()+self.excitation_limiters.channel_names()+self.field_thermal.channel_names()+self.underexcitation_limiters.channel_names()+self.oel2c.channel_names()+self.esst1a.channel_names()+self.takeover_oel.channel_names()})
        channels.update({key:[] for key in self.lines.channels(self.settings.monitored_lines)})
        channels.update({f.id+'.'+key:[] for f in self.settings.faults for key in ['active','current_a','p_w','q_var']})
        if self.automation is not None:channels.update({key:[] for key in self.automation.channels()})
        return channels

    def additional_signals(self,state,voltage):
        rotor=state[self.gen_offset:self.rotor_end].reshape(-1,2)
        current=self.generator_currents(state,voltage)
        power=voltage[self.gen_buses]*np.conj(current)*np.array([m.rated_va for m in self.gen_passports])
        electrical=(self.generator_internal(state)*np.conj(current)).real
        mechanical=self.controllers.mechanical(state,rotor[:,1],self.gen_mechanical)
        terminal=self.generator_terminal_voltage(state,voltage)
        signals=self.controllers.signals(state,rotor[:,1],terminal,self.gen_mechanical,self.excitation_bias(state))
        signals.update(self.round_rotors.signals(state,self.field_voltage(state,voltage,current)))
        signals.update(self.esst1a.signals(state,voltage,current))
        signals.update(self.takeover_oel.signals(state,current))
        signals.update(self.lines.signals(voltage,self.settings.monitored_lines))
        signals.update(self.generator_thermal.signals(state))
        signals.update(self.excitation_limiters.signals(state,current))
        signals.update(self.field_thermal.signals(state,current))
        signals.update(self.underexcitation_limiters.signals(state))
        signals.update(self.oel2c.signals(state,current))
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
        if self.automation is not None:signals.update(self.automation.signals(0.,state,voltage))
        return signals,complex(sum(power))

    def after_discrete_commands(self,time,state,event_log):
        self.esst1a.refresh(time,state,event_log)
        self.oel2c.apply(set(),time,state,event_log)
        self.underexcitation_limiters.refresh(time,state,event_log)
        self.controller_boundaries.refresh(time,state,event_log)
        if self.automation is not None:self.automation.service(time,state,event_log)

    def next_deadline(self,limit):return self.automation.next_deadline(limit) if self.automation is not None else limit

    def events_due(self,time):return self.automation.due(time) if self.automation is not None else False

    def additional_guards(self,state=None):
        ids,guards=self.generator_thermal.guards()
        field_ids,field_guards=self.field_thermal.guards()
        more,extra=self.controller_boundaries.guards(state)
        uel_ids,uel_guards=self.underexcitation_limiters.guards(state)
        oel_ids,oel_guards=self.oel2c.guards(state)
        esst_ids,esst_guards=self.esst1a.guards(state)
        net_ids,net_guards=self.automation.guards(state) if self.automation is not None else ([],[])
        return [('thermal',i) for i in ids]+field_ids+more+uel_ids+oel_ids+esst_ids+net_ids,guards+field_guards+extra+uel_guards+oel_guards+esst_guards+net_guards

    def apply_guard_events(self,ids,time,state,event_log):
        reaction=None
        if self.field_thermal.entries and ids:
            v=self.electrical(state)[0];current=self.generator_currents(state,v)
            reaction=self.field_thermal.reaction(state,current)
        self.generator_thermal.apply({key[1] for key in ids if key[0]=='thermal'},time,state,event_log)
        if reaction is not None:self.field_thermal.apply(ids,time,state,event_log,reaction)
        self.esst1a.apply({key for key in ids if key[0]=='esst1a'},time,state,event_log)
        self.oel2c.apply({key for key in ids if key[0]=='oel2c'},time,state,event_log)
        self.underexcitation_limiters.apply({key for key in ids if key[0]=='uel'},time,state,event_log)
        self.controller_boundaries.apply({key for key in ids if key[0]=='controller'},time,state,event_log)
        if self.automation is not None:self.automation.apply(ids,time,state,event_log)

    def apply_additional_event(self,event,*,time=None,state=None,event_log=None):
        if self.automation is not None:
            result=self.automation.command(event,time,state,event_log if event_log is not None else [])
            if result is not None:return result
        if event.kind=='esst1a_port_set':
            i=next(i for i,g in enumerate(self.generators) if g.id==event.equipment_id)
            self.esst1a.ports[i][event.port]=event.value
            return dict(outcome='applied',port=event.port,value=event.value,units='pu field voltage' if event.port in ('oel_ceiling_pu','uel_field_floor_pu','supplementary_field_pu') else 'pu voltage error')
        if event.kind=='generator_open':
            i=next(i for i,g in enumerate(self.generators) if g.id==event.equipment_id)
            was_closed=bool(self.gen_connected[i]);self.gen_connected[i]=False
            return {'outcome':'applied' if was_closed else 'already_in_requested_state','connected':False,'semantics':'electrical breaker only; rotor, governor, field and PSS continue; sensing on machine side'}

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
        elif event.kind=='voltage_reference_step':
            if i in self.esst1a.entries:self.esst1a.reference_step(i,event.value)
            else:self.controllers.vref[i]+=event.value
        else:self.gen_mechanical[i]+=event.value
        return {'outcome':'applied','value':event.value,'units':'additive pu voltage' if event.kind=='voltage_reference_step' else 'additive pu on generator VA base'}

    def integration_jacobian(self):
        if self.automation is not None:return None
        from power_grid_env.coupled_jacobian import callback
        classical = callback(self)
        if classical is not None:return classical
        from power_grid_env.genrou_jacobian import callback as round_callback
        round_rotor = round_callback(self)
        if round_rotor is not None:return round_rotor
        from power_grid_env.pss_droop_jacobian import callback as excitation_callback
        excitation = excitation_callback(self)
        if excitation is not None:return excitation
        from power_grid_env.esst1a_jacobian import callback as esst_callback
        esst = esst_callback(self)
        if esst is not None:return esst
        from power_grid_env.protection_jacobian import callback as protection_callback
        protection=protection_callback(self)
        if protection is not None:return protection
        from power_grid_env.oel2c_jacobian import callback as oel_callback
        return oel_callback(self)

    def observe_accepted_states(self,states):
        self.controllers.observe_accepted_states(states)
        self.excitation_limiters.observe(states)
        self.underexcitation_limiters.observe(states)
        self.oel2c.observe(states)

    def validate_additional_channels(self,channels):
        result=self.controllers.validate_channels(channels,self.settings.rtol,self.settings.atol)
        oel=self.excitation_limiters.validate(channels,self.settings.rtol,self.settings.atol)
        result['overexcitation_limits']=oel['limits'];result['ok']=result['ok'] and oel['ok']
        uel=self.underexcitation_limiters.validate(channels,self.settings.rtol,self.settings.atol)
        result['underexcitation_limits']=uel['limits'];result['ok']=result['ok'] and uel['ok']
        oel2c=self.oel2c.validate(channels,self.settings.rtol,self.settings.atol)
        result['oel2c_limits']=oel2c['limits'];result['ok']=result['ok'] and oel2c['ok']
        esst=self.esst1a.validate(channels,self.settings.rtol,self.settings.atol)
        result['esst1a_limits']=esst['limits'];result['ok']=result['ok'] and esst['ok']
        takeover=self.takeover_oel.validate(channels)
        result['takeover_oel_limits']=takeover['limits'];result['ok']=result['ok'] and takeover['ok']
        return result

    def additional_metadata(self):
        metadata={'takeover_OEL_model':'declared absolute field-current PI with sensor lag and back-calculation; not an IEEE OEL model',
                'takeover_OEL_passports':[p.model_dump(mode='json') for p in self.settings.takeover_oels],
                'ESST1A_model':'OpenIPSL 3.1.0 five-state VOS1/UEL1 with explicit absolute takeover inputs' if self.esst1a.entries else None,
                'ESST1A_passports':[p.model_dump(mode='json') for p in self.settings.esst1a_exciters],
                'oel2c_model':'pinned Dynawo OEL2C with declared upper-cap numerical guard v1 / MSL 3.2.3 eight-state component; nonpositive SEXS input summation; explicit startup, strict inactive or active authority-limited equilibrium initialization',
                'oel2c_passports':[p.model_dump(mode='json') for p in self.settings.oel2c_limiters],
                'underexcitation_model':'MNLEX2 circle on own machine VA base; positive AVR summation; OpenIPSL 3.1.0 raw lag and reversal reset semantics',
                'underexcitation_initialization':'explicit inactive raw-lag equilibrium or zero-output raw-lag startup; active/boundary initial circle rejected',
                'underexcitation_passports':[p.model_dump(mode='json') for p in self.settings.underexcitation_limiters],
                'generator_field_thermal_model':'normalized GENROU field I-squared replica; stator opens and impressed field voltage becomes zero; flux/current memory persists; no OEM temperature claim',
                'generator_field_thermal_passports':[p.model_dump(mode='json') for p in self.settings.generator_field_thermal],
                'controller_boundary_model':'continuous entry/release guards shared across coupled profiles',
                'overexcitation_model':'GENROU Xad*Ifd divided by explicit rated reaction; filtered magnitude and bounded droop AVR error subtraction; engineering model, not IEEE/OEM OEL',
                'overexcitation_passports':[p.model_dump(mode='json') for p in self.settings.overexcitation_limiters],
                'generator_thermal_model':'own-rated-RMS-current squared first-order replica; latched electrical opening; no temperature or OEM relay equivalence',
                'generator_thermal_passports':[p.model_dump(mode='json') for p in self.settings.generator_thermal],
                'initialization':'PV-initialized synchronous generators; '+('loaded motor equilibrium' if self.settings.operating_point else 'cold motor flux and speed'),
                'generator_mapping':{g.id:self.bus_names[int(bus)] for g,bus in zip(self.generators,self.gen_buses)},
                'generator_units':{'delta':'rad','omega':'pu synchronous speed','rotor_frequency_hz':'rotor electrical Hz, not bus frequency',
                    'terminal_voltage_pu':'machine-side terminal pu; equals open-circuit EMF when breaker open','terminal_angle_rad':'wrapped machine-side phase radians',
                    'electrical_power_pu':'pu own generator VA base','mechanical_power_pu':'pu own generator VA base'},
                'generator_model':'GENCLS/GENROU nominal-speed algebraic stator; fixed field or explicit SEXS',
                'machine_models':{g.id:m.model for g,m in zip(self.generators,self.gen_passports)},
                'fault_units':{'current_a':'RMS phase A','p_w':'three-phase W','q_var':'three-phase var','active':'0/1'},
                'generator_breaker_semantics':'electrical disconnection only; controls sense machine-side terminal; no turbine/field trip or reclose',
                'controller_units':{'TGOV1':'own generator VA-base power/valve; speed droop input pu','SEXS':'pu voltage/field'}}

        if self.automation is not None:
            metadata['network_automation']={'model':'continuous measured RMS relay progress, explicit actuator delays/failures and break-before-make transfer',
                'passports':self.settings.network_automation.model_dump(mode='json'),'jacobian':'explicit finite differences; no analytic DAE relay derivatives claimed',
                'pending_actions':[dict(time_s=t,kind=kind,breaker_or_transfer=id_,cause=cause) for t,_,kind,id_,cause in sorted(self.automation.queue)],
                'source_port_voltage':'upstream Thevenin EMF; use a node port for local bus PT voltage','whole_EMT_or_OEM_qualified':False}
            metadata['generator_breaker_semantics']='electrical opening/reclosing with continuously retained rotor/field states, local phasor/frequency synchronism check and thermal latch interlock'
        return metadata


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
    if model.automation is not None:model.automation.initialize(state)
    return integrate_induction_model(model,scope,state,initial,study='coupled_rms')
