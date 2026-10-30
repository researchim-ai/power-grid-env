"""Canonical balanced network -> ANDES classical RMS adapter.

This is a declared positive-sequence synchronous-machine subset, not a general industrial
RMS certificate. Unsupported equipment must not silently disappear.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from power_grid_env.model import Network, parse_vector_group
from power_grid_env.rms_controls import TGOV1Passport, SEXSPassport, IEEESTPassport
from power_grid_env.topology import compile_topology


class ClassicalMachine(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    model: Literal['GENCLS'] = 'GENCLS'
    generator_id: str
    rated_va: float = Field(gt=0)
    inertia_s: float = Field(gt=0)
    x_d_prime_pu: float = Field(gt=0)
    damping_pu: float = Field(default=0, ge=0)
    governor: TGOV1Passport | None = None
    exciter: SEXSPassport | None = None
    stabilizer: IEEESTPassport | None = None

    @model_validator(mode='after')
    def dependencies(self):
        if self.stabilizer is not None and self.exciter is None:
            raise ValueError('stabilizer requires an exciter')
        return self


class RoundRotorMachine(ClassicalMachine):
    model: Literal['GENROU'] = 'GENROU'
    x_d_pu: float = Field(gt=0)
    x_q_pu: float = Field(gt=0)
    x_d_subtransient_pu: float = Field(gt=0)
    x_q_prime_pu: float = Field(gt=0)
    x_q_subtransient_pu: float = Field(gt=0)
    leakage_pu: float = Field(ge=0)
    armature_r_pu: float = Field(ge=0)
    td_prime_s: float = Field(gt=0)
    td_subtransient_s: float = Field(gt=0)
    tq_prime_s: float = Field(gt=0)
    tq_subtransient_s: float = Field(gt=0)
    saturation: Literal['disabled','quadratic']
    saturation_s10: float | None = Field(default=None,gt=0)
    saturation_s12: float | None = Field(default=None,gt=0)

    @model_validator(mode='after')
    def physical_ordering(self):
        if not (self.x_d_pu>self.x_d_prime_pu>self.x_d_subtransient_pu>self.leakage_pu
                and self.x_q_pu>self.x_q_prime_pu>self.x_q_subtransient_pu>self.leakage_pu):
            raise ValueError('require X > X-prime > X-double-prime > leakage')
        if not math.isclose(self.x_d_subtransient_pu,self.x_q_subtransient_pu,rel_tol=1e-10):
            raise ValueError('this ANDES GENROU mapping requires equal subtransient d/q reactances')
        if self.td_prime_s<=self.td_subtransient_s or self.tq_prime_s<=self.tq_subtransient_s:
            raise ValueError('transient time constants must exceed subtransient constants')
        if self.saturation=='disabled':
            if self.saturation_s10 is not None or self.saturation_s12 is not None:
                raise ValueError('disabled saturation cannot contain fitted factors')
        elif self.saturation_s10 is None or self.saturation_s12 is None or self.saturation_s12<=self.saturation_s10:
            raise ValueError('quadratic saturation requires 0 < S10 < S12')
        return self


class RMSEvent(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    time_s: float = Field(gt=0)
    kind: Literal['line_trip', 'generator_trip', 'load_scale', 'mechanical_step', 'governor_step', 'voltage_reference_step']
    equipment_id: str
    value: float | None = None


class RMSFault(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    node_id: str
    apply_s: float = Field(gt=0)
    clear_s: float = Field(gt=0)
    r_ohm: float = Field(ge=0)
    x_ohm: float = Field(ge=0)

    @model_validator(mode='after')
    def limits(self):
        if self.clear_s <= self.apply_s or self.r_ohm+self.x_ohm <= 0:
            raise ValueError('fault requires positive finite impedance and apply < clear')
        return self


class RMSSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    duration_s: float = Field(default=1, gt=0, le=60)
    step_s: float = Field(default=.005, gt=0)
    tolerance: float = Field(default=1e-8, gt=0, le=1e-4)
    machines: list[ClassicalMachine | RoundRotorMachine]
    events: list[RMSEvent] = Field(default_factory=list)
    faults: list[RMSFault] = Field(default_factory=list)

    @model_validator(mode='after')
    def limits(self):
        if self.step_s > self.duration_s or math.ceil(self.duration_s/self.step_s) > 200000:
            raise ValueError('invalid RMS sampling budget')
        if any(e.time_s >= self.duration_s for e in self.events):
            raise ValueError('events must lie strictly inside the simulation horizon')
        if any(f.clear_s >= self.duration_s for f in self.faults):
            raise ValueError('fault must clear strictly before horizon')
        ordered=sorted(self.faults,key=lambda f:f.apply_s)
        if any(a.clear_s>=b.apply_s for a,b in zip(ordered,ordered[1:])):
            raise ValueError('overlapping faults are not yet supported')
        if len(self.faults)>100:
            raise ValueError('too many faults')
        if len(self.events) > 10000:
            raise ValueError('too many RMS events')
        return self


def positive_sequence(matrix) -> complex:
    a = np.exp(2j*np.pi/3)
    vector = np.array([1, a*a, a])
    array = np.asarray(matrix, dtype=complex)
    if array.shape != (3, 3) or not np.isfinite(array).all():
        raise ValueError('RMS requires finite 3x3 ABC matrices')
    z = complex(np.vdot(vector, array @ vector)/3)
    # Both symmetry and cyclic symmetry are required; no ignored coupling.
    if not np.allclose(array, array.T, atol=1e-10, rtol=1e-9) or not np.allclose(
            array, np.roll(np.roll(array, 1, axis=0), 1, axis=1), atol=1e-10, rtol=1e-9):
        raise ValueError('unbalanced/non-transposed matrix is not in this RMS subset')
    return z


def transformer_parameters(transformer, topology, base_va):
    """Positive-sequence two-winding equivalent on canonical bus bases.

    ANDES puts the complex tap on winding 0. Its series impedance must be
    referred through winding-1 tap so changing either tap preserves the physical
    winding representation. Clock shift is phi = -angle(LV/HV).
    """
    if len(transformer.windings)!=2 or transformer.phases!=3:
        raise ValueError('only two-winding three-phase transformers supported')
    if transformer.ltc is not None:
        raise ValueError('RMS LTC dynamics not implemented')
    if transformer.imag_pct!=0 or transformer.no_load_loss_pct!=0:
        raise ValueError('explicit zero magnetization/no-load loss required')
    a,b=transformer.windings
    if a.phase is not None or b.phase is not None or a.angle_deg!=0 or b.angle_deg!=0:
        raise ValueError('phase-specific or explicit winding angle unsupported; use vector group')
    if not math.isclose(a.rated_va,b.rated_va):
        raise ValueError('unequal winding ratings require explicit conversion')
    if any(not math.isfinite(v) or v<=0 for v in (a.tap_pu,b.tap_pu,a.rated_va,a.nominal_ll_v,b.nominal_ll_v)):
        raise ValueError('invalid winding passport')
    if (a.r_pct is None)!=(b.r_pct is None):
        raise ValueError('supply both winding resistances or total load loss')
    resistance=transformer.load_loss_pct if a.r_pct is None else a.r_pct+b.r_pct
    if not math.isfinite(resistance) or resistance<0 or not math.isfinite(transformer.x_hl_pct) or transformer.x_hl_pct<=0:
        raise ValueError('invalid leakage impedance')
    parsed=parse_vector_group(transformer.vector_group)
    if 'series' in parsed.connections:
        raise ValueError('autotransformer/series winding unsupported')
    hv=topology.buses[topology.terminal_bus[a.terminal_id]].nominal_ll_v
    lv=topology.buses[topology.terminal_bus[b.terminal_id]].nominal_ll_v
    scale=base_va/a.rated_va*(a.nominal_ll_v/hv)**2*b.tap_pu**2/100
    phi=(parsed.clock_hours[1]*math.pi/6+math.pi)%(2*math.pi)-math.pi
    return {'r':resistance*scale,'x':transformer.x_hl_pct*scale,
            'tap':(a.nominal_ll_v/b.nominal_ll_v)/(hv/lv)*a.tap_pu/b.tap_pu,
            'phi':phi,'Vn1':hv/1000,'Vn2':lv/1000}


def validate_rms(network: Network, settings: RMSSettings) -> dict:
    errors = []
    topology = compile_topology(network)
    errors.extend(topology.errors)
    samples=math.ceil(settings.duration_s/settings.step_s)+3*(len(settings.events)+2*len(settings.faults))+1
    if samples*(2*len(topology.buses)+32*len(network.generators)) > 2_000_000:
        errors.append('RMS output exceeds the two-million-scalar report budget')
    if len(topology.islands) != 1 or len(network.sources) != 1 or not network.sources[0].is_slack:
        errors.append('one connected island and one external slack are required')
    if any([network.inverters, network.storages, network.shunts,
            network.neutral_grounds, network.study_faults, network.harmonics, network.omissions]):
        errors.append('DER/shunts/neutral/faults/harmonics/omissions are unsupported')
    if any(s.representation != 'topological' or set(s.phases) != set('ABC') for s in network.switches):
        errors.append('only fixed three-phase topological switches are supported')
    for transformer in network.transformers:
        try:
            transformer_parameters(transformer,topology,network.s_base_va)
        except (ValueError,KeyError) as exc:
            errors.append(f'{transformer.id}: {exc}')
    if network.s_base_va <= 0 or not math.isfinite(network.s_base_va):
        errors.append('invalid system power base')
    if network.frequency_hz <= 0 or not math.isfinite(network.frequency_hz):
        errors.append('invalid frequency')
    for source in network.sources:
        if source.phase_pu is not None:
            errors.append(f'{source.id}: explicit phase source not supported')
        if not all(math.isfinite(v) for v in (source.r_ohm, source.x_ohm, source.ll_v, source.angle_a_rad)) or source.r_ohm < 0 or source.x_ohm <= 0:
            errors.append(f'{source.id}: positive inductive source impedance required')
    for line in network.lines:
        try:
            left=topology.buses[topology.terminal_bus[line.terminal_from]]
            right=topology.buses[topology.terminal_bus[line.terminal_to]]
            if left.nominal_ll_v!=right.nominal_ll_v:
                raise ValueError('line connects different voltage bases without transformer')
            if list(line.phases) != list('ABC'):
                raise ValueError('ABC line required')
            z = positive_sequence(np.array(line.r_ohm) + 1j*np.array(line.x_ohm))
            positive_sequence(line.b_siemens)
            if z.real < -1e-12 or z.imag <= 0:
                raise ValueError('inductive positive-sequence line required')
        except (ValueError, TypeError) as exc:
            errors.append(f'{line.id}: {exc}')
    for load in network.loads:
        if set(load.phases) != set('ABC') or load.model != 'constant_pq':
            errors.append(f'{load.id}: balanced constant_pq load required')
        for values in (load.p_w, load.q_var):
            if set(values) != set('ABC') or not np.allclose(list(values.values()), next(iter(values.values()), 0), rtol=1e-10, atol=1e-6):
                errors.append(f'{load.id}: unequal phase powers')
    for machine in settings.machines:
        if machine.stabilizer is not None and machine.exciter is None:
            errors.append('stabilizer requires an exciter')
    names = [m.generator_id for m in settings.machines]
    if not names or len(set(names)) != len(names) or set(names) != {g.id for g in network.generators}:
        errors.append('exactly one explicit machine passport per generator required')
    for gen in network.generators:
        bus_nominal=topology.buses[topology.terminal_bus[gen.terminal_id]].nominal_ll_v
        if not math.isclose(gen.ll_v,bus_nominal,rel_tol=1e-9):
            errors.append(f'{gen.id}: generator voltage base differs from connected bus')
        if gen.participation is not None or not all(math.isfinite(v) for v in (gen.p_w, gen.q_var, gen.ll_v, gen.v_set_pu or 0)):
            errors.append(f'{gen.id}: invalid data or unsupported distributed slack')
        if gen.mode != 'pv' or set(gen.phases) != set('ABC') or gen.v_set_pu is None:
            errors.append(f'{gen.id}: three-phase PV initialization required')
        machine = next((m for m in settings.machines if m.generator_id == gen.id), None)
        if isinstance(machine,RoundRotorMachine) and gen.x_d_pu is not None and not math.isclose(machine.x_d_pu,gen.x_d_pu):
            errors.append(f'{gen.id}: inconsistent synchronous reactance passports')
        if machine and gen.inertia_s is not None and not math.isclose(machine.inertia_s, gen.inertia_s):
            errors.append(f'{gen.id}: inconsistent inertia passports')
    groups = {'line_trip': {x.id for x in network.lines}, 'generator_trip': set(names),
              'mechanical_step': {m.generator_id for m in settings.machines if m.governor is None},
              'governor_step': {m.generator_id for m in settings.machines if m.governor is not None},
              'voltage_reference_step': {m.generator_id for m in settings.machines if m.exciter is not None},
              'load_scale': {x.id for x in network.loads}}
    tripped=set()
    active_lines={line.id for line in network.lines}
    for event in sorted(settings.events, key=lambda e:e.time_s):
        if event.kind.endswith('trip'):
            key=(event.kind,event.equipment_id)
            if key in tripped:
                errors.append('repeated trip would toggle the device back on')
            tripped.add(key)
        if event.kind=='line_trip':
            active_lines.discard(event.equipment_id)
            if topology.buses:
                reachable={next(iter(topology.buses))}
                changed=True
                while changed:
                    changed=False
                    edges=[(line.terminal_from,line.terminal_to) for line in network.lines if line.id in active_lines]
                    edges.extend((t.windings[0].terminal_id,t.windings[1].terminal_id) for t in network.transformers if len(t.windings)==2)
                    for left,right in edges:
                        ends={topology.terminal_bus[left], topology.terminal_bus[right]}
                        if ends & reachable and not ends <= reachable:
                            reachable.update(ends); changed=True
                if reachable != set(topology.buses):
                    errors.append('islanding line trips are outside this RMS subset')
        if event.equipment_id not in groups[event.kind]:
            errors.append(f'{event.equipment_id}: unknown event target')
        if event.kind in {'load_scale', 'mechanical_step', 'governor_step', 'voltage_reference_step'} and (event.value is None or not math.isfinite(event.value)):
            errors.append('event value required and finite')
        if event.kind == 'load_scale' and event.value is not None and event.value < 0:
            errors.append('load scale must be nonnegative')
        if event.kind.endswith('trip') and event.value is not None:
            errors.append('trip event takes no value')
    node_ids={n.id for n in network.connectivity_nodes}
    for fault in settings.faults:
        if fault.node_id not in node_ids:
            errors.append(f'{fault.node_id}: unknown fault node')
    return {'ok': not errors, 'errors': errors, 'model': 'balanced_synchronous_machines',
            'limitations': ['positive sequence only', 'GENCLS is reduced order; GENROU uses transient/subtransient rotor states with algebraic stator',
                            'load is constant PQ during TDS', 'no switch operation or island qualification',
                            'no motor or converter dynamics', 'no field validation']}


def build_system(network, settings):
    from power_grid_env.andes_rms import ensure_andes_importable
    if not ensure_andes_importable():
        raise ImportError('ANDES extra is not installed')
    import andes
    topology = compile_topology(network)
    cache = Path(tempfile.gettempdir())/'power-grid-env-andes-pycode'
    cache.mkdir(exist_ok=True)
    system = andes.System(no_output=True, default_config=True, pycode_path=str(cache))
    system.config.mva = network.s_base_va/1e6
    system.config.freq = network.frequency_hz
    buses = {key: i+1 for i, key in enumerate(sorted(topology.buses))}
    ids = {'buses': buses, 'lines': {}, 'loads': {}, 'machines': {}, 'machine_models': {}, 'static_generators': {}, 'transformers': {}, 'governors': {}, 'exciters': {}, 'stabilizers': {}}
    source = network.sources[0]
    kv = topology.buses[topology.terminal_bus[source.terminal_id]].nominal_ll_v/1000
    base = network.s_base_va
    zbase = (kv*1000)**2/base
    # Seed angles with physical transformer clock shifts. A flat zero-angle
    # guess is far from a 150-degree transformer solution and can defeat PF.
    adjacency={bus:[] for bus in buses}
    edges=[(topology.terminal_bus[line.terminal_from],topology.terminal_bus[line.terminal_to],0.) for line in network.lines]
    for transformer in network.transformers:
        a,b=transformer.windings
        edges.append((topology.terminal_bus[a.terminal_id],topology.terminal_bus[b.terminal_id],
                      -transformer_parameters(transformer,topology,base)['phi']))
    for left,right,shift in edges:
        adjacency[left].append((right,shift));adjacency[right].append((left,-shift))
    source_bus=topology.terminal_bus[source.terminal_id]
    angles={source_bus:source.angle_a_rad};pending=[source_bus]
    for left in pending:
        for right,shift in adjacency[left]:
            if right not in angles:
                angles[right]=angles[left]+shift;pending.append(right)
    for bus, idx in buses.items():
        system.add('Bus', idx=idx, Vn=topology.buses[bus].nominal_ll_v/1000, v0=1., a0=angles[bus])
    source = network.sources[0]
    internal = len(buses)+1
    system.add('Bus', idx=internal, Vn=kv, v0=source.ll_v/(kv*1000))
    system.add('Slack', idx='external', bus=internal, Sn=base/1e6, Vn=kv,
               v0=source.ll_v/(kv*1000), a0=source.angle_a_rad)
    system.add('Line', idx='source_z', bus1=internal, bus2=buses[topology.terminal_bus[source.terminal_id]],
               Sn=base/1e6, fn=network.frequency_hz, Vn1=kv, Vn2=kv,
               r=source.r_ohm/zbase, x=source.x_ohm/zbase, b=0.)
    for i, line in enumerate(network.lines):
        token=f'line_{i}'; ids['lines'][line.id]=token
        z=positive_sequence(np.array(line.r_ohm)+1j*np.array(line.x_ohm))
        local_kv=topology.buses[topology.terminal_bus[line.terminal_from]].nominal_ll_v/1000
        local_zbase=(local_kv*1000)**2/base
        b=positive_sequence(line.b_siemens).real*local_zbase
        system.add('Line', idx=token, bus1=buses[topology.terminal_bus[line.terminal_from]],
                   bus2=buses[topology.terminal_bus[line.terminal_to]], Sn=base/1e6,
                   fn=network.frequency_hz, Vn1=local_kv, Vn2=local_kv, r=z.real/local_zbase, x=z.imag/local_zbase, b=b)
    for i, transformer in enumerate(network.transformers):
        token=f'transformer_{i}';ids['transformers'][transformer.id]=token
        a,b=transformer.windings
        system.add('Line',idx=token,bus1=buses[topology.terminal_bus[a.terminal_id]],
                   bus2=buses[topology.terminal_bus[b.terminal_id]],Sn=base/1e6,
                   fn=network.frequency_hz,b=0.,trans=1,
                   **transformer_parameters(transformer,topology,base))
    for i, load in enumerate(network.loads):
        token=f'load_{i}'; ids['loads'][load.id]=token
        load_kv=topology.buses[topology.terminal_bus[load.terminal_id]].nominal_ll_v/1000
        system.add('PQ', idx=token, bus=buses[topology.terminal_bus[load.terminal_id]], Vn=load_kv,
                   p0=sum(load.p_w.values())/base, q0=sum(load.q_var.values())/base, vmin=1e-4, vmax=1e6)
    for i, gen in enumerate(network.generators):
        token=f'pv_{i}'; machine=f'machine_{i}'
        ids['static_generators'][gen.id]=token; ids['machines'][gen.id]=machine
        passport=next(m for m in settings.machines if m.generator_id==gen.id)
        bus=buses[topology.terminal_bus[gen.terminal_id]]
        gen_kv=topology.buses[topology.terminal_bus[gen.terminal_id]].nominal_ll_v/1000
        system.add('PV', idx=token, bus=bus, Sn=base/1e6, Vn=gen_kv, p0=gen.p_w/base, v0=gen.v_set_pu)
        machine_model=passport.model
        ids['machine_models'][gen.id]=machine_model
        parameters={'ra':0.,'xd1':passport.x_d_prime_pu,'xl':0.}
        if isinstance(passport,RoundRotorMachine):
            parameters.update(ra=passport.armature_r_pu,xl=passport.leakage_pu,
                xd=passport.x_d_pu,xq=passport.x_q_pu,xd2=passport.x_d_subtransient_pu,
                xq1=passport.x_q_prime_pu,xq2=passport.x_q_subtransient_pu,
                Td10=passport.td_prime_s,Td20=passport.td_subtransient_s,
                Tq10=passport.tq_prime_s,Tq20=passport.tq_subtransient_s,
                S10=0. if passport.saturation=='disabled' else passport.saturation_s10,
                S12=1. if passport.saturation=='disabled' else passport.saturation_s12)
        system.add(machine_model, idx=machine, bus=bus, gen=token, Sn=passport.rated_va/1e6,
                   Vn=gen_kv, fn=network.frequency_hz, M=2*passport.inertia_s, D=passport.damping_pu,
                   **parameters)
        if passport.governor:
            token_g=f'gov_{i}';ids['governors'][gen.id]=token_g
            system.add('TGOV1',idx=token_g,syn=machine,Tn=passport.rated_va/1e6,wref0=1.,
                       **passport.governor.model_dump(exclude={'model'}))
        if passport.exciter:
            token_e=f'avr_{i}';ids['exciters'][gen.id]=token_e
            system.add('SEXS',idx=token_e,syn=machine,**passport.exciter.model_dump(exclude={'model'}))
        if passport.stabilizer:
            token_p=f'pss_{i}';ids['stabilizers'][gen.id]=token_p
            system.add('IEEEST',idx=token_p,avr=ids['exciters'][gen.id],
                       **passport.stabilizer.model_dump(exclude={'model'}))
    for i, event in enumerate(settings.events):
        if event.kind in {'line_trip', 'generator_trip'}:
            group='Line' if event.kind=='line_trip' else ids['machine_models'][event.equipment_id]
            target=ids['lines' if group=='Line' else 'machines'][event.equipment_id]
            system.add('Toggle', idx=f'event_{i}', model=group, dev=target, t=event.time_s)
        elif event.kind in {'governor_step','voltage_reference_step'}:
            group='TGOV1' if event.kind=='governor_step' else 'SEXS'
            target=ids['governors' if group=='TGOV1' else 'exciters'][event.equipment_id]
            machine=next(m for m in settings.machines if m.generator_id==event.equipment_id)
            amount=event.value*machine.rated_va/base if group=='TGOV1' else event.value
            system.add('Alter',idx=f'event_{i}',model=group,dev=target,t=event.time_s,
                       src='pref0' if group=='TGOV1' else 'vref0',attr='v',method='+',amount=amount)
        else:
            group='PQ' if event.kind=='load_scale' else ids['machine_models'][event.equipment_id]
            target=ids['loads' if group=='PQ' else 'machines'][event.equipment_id]
            for j, src in enumerate(('Ppf', 'Qpf') if group=='PQ' else ('tm0',)):
                system.add('Alter', idx=f'event_{i}_{j}', model=group, dev=target, t=event.time_s,
                           src=src, attr='v', method='*' if group=='PQ' else '+', amount=(event.value if group=='PQ' else event.value * next(m.rated_va for m in settings.machines if m.generator_id==event.equipment_id)/base))
    for i,fault in enumerate(settings.faults):
        bus_id=topology.node_to_bus[fault.node_id]
        z_base=topology.buses[bus_id].nominal_ll_v**2/base
        system.add('Fault',idx=f'fault_{i}',bus=buses[bus_id],tf=fault.apply_s,tc=fault.clear_s,
                   rf=fault.r_ohm/z_base,xf=fault.x_ohm/z_base)
    system.setup()
    system.PQ.config.p2p=1.; system.PQ.config.p2z=0.; system.PQ.config.p2i=0.
    system.PQ.config.q2q=1.; system.PQ.config.q2z=0.; system.PQ.config.q2i=0.
    system.PFlow.config.tol=1e-10
    system.TDS.config.tol=settings.tolerance
    system.TDS.config.tstep=settings.step_s
    system.TDS.config.tf=settings.duration_s
    system.TDS.config.criteria=0
    return system, ids


def initialize_dynamics(network, settings):
    scope=validate_rms(network, settings)
    if not scope['ok']:
        return None, {'solved': False, 'completed': False, 'status': 'rejected', 'reason': 'rms_scope', 'scope': scope}
    system, ids=build_system(network, settings)
    if not system.PFlow.run():
        return None, {'solved': True, 'completed': False, 'status': 'initialization_failed', 'reason': 'power_flow', 'scope': scope}
    initial = {'bus_voltage_pu': {key: float(system.Bus.v.v[idx-1]) for key,idx in ids['buses'].items()},
               'bus_angle_rad': {key: float(system.Bus.a.v[idx-1]) for key,idx in ids['buses'].items()}}
    # Do not initialize an explicitly limited generator outside its passport.
    for i, gen in enumerate(network.generators):
        q=float(system.PV.q.v[i])*network.s_base_va
        if (gen.q_min_var is not None and q < gen.q_min_var-1e-3) or (gen.q_max_var is not None and q > gen.q_max_var+1e-3):
            return None, {'solved': True, 'completed': False, 'status': 'initialization_failed', 'reason': 'generator_q_limit', 'scope': scope}
    system.TDS.init()
    residual=float(np.max(np.abs(system.dae.fg)))
    if not system.TDS.test_ok or not math.isfinite(residual) or residual > max(settings.tolerance*10, 1e-7):
        return None, {'solved': True, 'completed': False, 'status': 'initialization_failed', 'init_residual': residual if math.isfinite(residual) else None, 'scope': scope}
    return (system,ids,scope,initial,residual),None


def run_rms(network, settings):
    initialized,error=initialize_dynamics(network,settings)
    if error is not None:
        return error
    system,ids,scope,initial,residual=initialized
    # ANDES defaults to a fixed 1e-4 s bracket around events. Scale the
    # bracket with h² so that event treatment does not impose an error floor
    # when verifying integration convergence.
    event_bracket = min(1e-6, settings.step_s**2*.01)
    if settings.events or settings.faults:
        instants=sorted({0., settings.duration_s, *(e.time_s for e in settings.events), *(f.apply_s for f in settings.faults), *(f.clear_s for f in settings.faults)})
        event_bracket=min(event_bracket, min(b-a for a,b in zip(instants,instants[1:]))/4)
    # store_switch_times merges into switch_dict; discard the default bracket
    # installed by init before rebuilding, otherwise times are not sorted.
    system.switch_dict.clear()
    system.store_switch_times(system.exist.tds, eps=event_bracket)
    system.TDS.run()
    time=np.asarray(system.dae.ts.t).ravel()
    x=np.asarray(system.dae.ts.x); y=np.asarray(system.dae.ts.y)
    complete=bool(not system.TDS.busted and system.TDS.converged and len(time) and np.isfinite(time).all() and np.all(np.diff(time)>0) and abs(time[-1]-settings.duration_s)<1e-8 and np.isfinite(x).all() and np.isfinite(y).all())
    if not complete:
        return {'solved': True, 'completed': False, 'status': 'numerically_not_converged',
                'scope': scope, 'init_residual': residual, 'samples': len(time),
                'solver_message': system.TDS.err_msg, 'industrial_profile_qualified': False}
    channels={}
    for gen in network.generators:
        model_name=ids['machine_models'][gen.id]
        model=getattr(system,model_name)
        index=list(model.idx.v).index(ids['machines'][gen.id])
        def series(name):
            variable=getattr(model,name)
            address=int(np.asarray(variable.a).ravel()[index])
            return (x if variable.v_code=='x' else y)[:,address]
        names=['delta','omega','vd','vq','Id','Iq','tm','te','vf']
        if model_name=='GENROU':
            names.extend(['e1q','e1d','e2d','e2q','psi2d','psi2q','Se'])
        signals={name:series(name) for name in names}
        channels[f'{gen.id}.p_w']=((signals['vd']*signals['Id']+signals['vq']*signals['Iq'])*network.s_base_va).tolist()
        channels[f'{gen.id}.q_var']=((signals['vq']*signals['Id']-signals['vd']*signals['Iq'])*network.s_base_va).tolist()
        for name in names:
            if name not in ('vd','vq','Id','Iq'):
                channels[f'{gen.id}.{name}']=signals[name].tolist()
    for group, mapping, variables in [('TGOV1','governors',('pout','LAG_y','pref')),
                                      ('SEXS','exciters',('vout','vi','vref')),
                                      ('IEEEST','stabilizers',('vsout','sig','WO_y'))]:
        model=getattr(system,group)
        model_ids=list(model.idx.v)
        for generator,token in ids[mapping].items():
            index=model_ids.index(token)
            for name in variables:
                variable=getattr(model,name)
                address=int(np.asarray(variable.a).ravel()[index])
                data=x if variable.v_code=='x' else y
                channels[f'{generator}.{group}.{name}']=data[:,address].tolist()
    for key,idx in ids['buses'].items():
        for name in ('v','a'):
            address=int(np.asarray(getattr(system.Bus,name).a).ravel()[idx-1])
            channels[f'{key}.{name}']=y[:,address].tolist()
    controller_limits={}
    violations=[]
    for machine in settings.machines:
        gen=machine.generator_id
        trip=min((e.time_s for e in settings.events if e.kind=='generator_trip' and e.equipment_id==gen),default=math.inf)
        active=time<=trip
        checks=[]
        if machine.governor:
            factor=machine.rated_va/network.s_base_va
            checks.append(('TGOV1.LAG_y',machine.governor.VMIN*factor,machine.governor.VMAX*factor))
        if machine.exciter:
            checks.append(('SEXS.vout',machine.exciter.EMIN,machine.exciter.EMAX))
        if machine.stabilizer:
            checks.append(('IEEEST.vsout',machine.stabilizer.LSMIN,machine.stabilizer.LSMAX))
        for variable,lower,upper in checks:
            data=np.asarray(channels[f'{gen}.{variable}'])[active]
            if not len(data):continue
            controller_limits[f'{gen}.{variable}']={'lower':lower,'upper':upper,
                'minimum':float(min(data)),'maximum':float(max(data)),
                'samples_at_lower':int(np.count_nonzero(abs(data-lower)<1e-7)),
                'samples_at_upper':int(np.count_nonzero(abs(data-upper)<1e-7))}
            if min(data)<lower-1e-7 or max(data)>upper+1e-7:
                violations.append(f'{gen}.{variable}')
    complete=complete and not violations
    import andes
    from power_grid_env.evidence import package_fingerprint
    from power_grid_env.model import canonical_hash
    return {'code_sha256': package_fingerprint(), 'network_sha256': canonical_hash(network), 'settings': settings.model_dump(mode='json'), 'solved': True, 'completed': complete, 'status': 'completed' if complete else 'controller_limits_violated',
            'controller_limits':controller_limits,'control_limit_violations':violations,
            'scope': scope, 'backend': 'andes', 'backend_version': andes.__version__, 'mapping': ids,
            'initial': initial, 'init_residual': residual, 'event_bracket_s': event_bracket, 'time_s': time.tolist(), 'channels': channels,
            'units': {'delta': 'rad', 'omega': 'pu synchronous speed', 'v': 'pu line voltage', 'a': 'rad', 'p_w': 'W', 'q_var': 'var', 'tm': 'pu system-base torque', 'te': 'pu system-base torque', 'vf': 'pu voltage'},
            'machine_state_units': {'e1q':'pu transient rotor voltage','e1d':'pu transient rotor voltage',
                'e2d':'pu subtransient auxiliary rotor state','e2q':'pu subtransient auxiliary rotor state',
                'psi2d':'pu air-gap flux','psi2q':'pu air-gap flux','Se':'dimensionless saturation factor'},
            'controller_units': {'TGOV1.pout':'pu torque on system MVA base',
                'TGOV1.LAG_y':'pu valve output on system MVA base','TGOV1.pref':'pu speed-equivalent internal reference',
                'SEXS.vout':'pu machine field voltage','SEXS.vi':'pu voltage','SEXS.vref':'pu voltage',
                'IEEEST.sig':'pu synchronous speed deviation','IEEEST.WO_y':'pu stabilizing voltage','IEEEST.vsout':'pu stabilizing voltage'},
            'industrial_profile_qualified': False, 'field_validated': False}
