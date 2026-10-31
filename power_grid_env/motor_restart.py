"""Finite, preregistered DOL restart timing candidates with conservative gates.

No optimization, monotonicity, interval safety or real-equipment qualification.
Every controlled motor gets explicit absolute timings in every candidate.
"""
from pathlib import Path
import math
import time
from typing import Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.study_spec import StudySpec,STUDY_SCHEMA
from power_grid_env.results import SolveOptions
from power_grid_env.induction_network import validate_induction
from power_grid_env.motor_sensitivity import MotorAssessment,run_frozen_motor_points,_encoded
from power_grid_env.topology import compile_topology


class RestartTiming(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    motor_id: str=Field(min_length=1,max_length=128)
    pickup_delay_s: float=Field(ge=1e-6,le=60)
    minimum_off_time_s: float=Field(ge=0,le=60)


class RestartCandidate(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    id: str=Field(pattern='^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    timings: list[RestartTiming]=Field(min_length=1,max_length=32)

    @model_validator(mode='after')
    def unique(self):
        if len({t.motor_id for t in self.timings})!=len(self.timings):raise ValueError('duplicate timed motor')
        return self


class RestartVoltageWindow(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    id: str=Field(pattern='^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    node_id: str=Field(min_length=1,max_length=128)
    from_s: float=Field(ge=0,le=60)
    to_s: float=Field(gt=0,le=60)
    minimum_voltage_pu: float=Field(ge=0)
    maximum_voltage_pu: float=Field(gt=0)

    @model_validator(mode='after')
    def interval(self):
        if self.to_s<=self.from_s or self.maximum_voltage_pu<self.minimum_voltage_pu:raise ValueError('invalid voltage window')
        return self


class RestartRepeatability(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    time_absolute_s: float=Field(default=1e-6,gt=0)
    pu_absolute: float=Field(default=1e-6,gt=0)
    power_absolute_va: float=Field(default=1.,gt=0)
    angle_absolute_rad: float=Field(default=1e-6,gt=0)
    thermal_absolute: float=Field(default=1e-6,gt=0)


class MotorRestartProtocol(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    schema_version: Literal['power-grid-env.motor-restart/v1']='power-grid-env.motor-restart/v1'
    id: str=Field(min_length=1,max_length=128)
    baseline_candidate_id: str=Field(min_length=1,max_length=64)
    repeatability: RestartRepeatability=Field(default_factory=RestartRepeatability)
    candidates: list[RestartCandidate]=Field(min_length=2,max_length=32)
    observations: list[MotorAssessment]=Field(min_length=1,max_length=32)
    voltage_windows: list[RestartVoltageWindow]=Field(min_length=1,max_length=16)
    per_case_timeout_s: float=Field(default=30.,gt=0,le=600)
    total_timeout_s: float=Field(default=300.,gt=0,le=3600)
    maximum_output_bytes: int=Field(default=128_000_000,ge=8_000_000,le=512_000_000)

    @model_validator(mode='after')
    def distinct(self):
        ids=[c.id for c in self.candidates]
        if len(set(ids))!=len(ids) or self.baseline_candidate_id not in ids:raise ValueError('unique candidates and declared baseline required')
        if len({o.motor_id for o in self.observations})!=len(self.observations):raise ValueError('duplicate observation')
        if len({w.id for w in self.voltage_windows})!=len(self.voltage_windows):raise ValueError('duplicate voltage window')
        if len(_encoded(self.model_dump(mode='json')))>100_000:raise ValueError('restart protocol metadata budget')
        return self


def build_restart_points(spec,protocol):
    spec=StudySpec.model_validate(spec.model_dump(mode='json'))
    protocol=MotorRestartProtocol.model_validate(protocol.model_dump(mode='json') if isinstance(protocol,MotorRestartProtocol) else protocol)
    if spec.schema_version!=STUDY_SCHEMA or spec.study!='motor_start' or spec.backend!='scipy' or spec.induction is None or spec.coupled is not None or spec.rms is not None or spec.scenario is not None or spec.requested_motors or spec.options!=SolveOptions() or len(spec.id)>256:
        raise ValueError('plain bounded motor_start/scipy study required')
    if len(_encoded(spec.model_dump(mode='json')))>32_000_000:raise ValueError('restart study byte budget')
    scope=validate_induction(spec.network,spec.induction)
    if not scope['ok']:raise ValueError('unsupported base study: '+str(scope['errors']))
    motors={m.id:m for m in spec.induction.motors};controlled={m.id for m in motors.values() if m.contactor}
    if not controlled:raise ValueError('explicit voltage-contactor passports required')
    if {o.motor_id for o in protocol.observations}!=set(motors):raise ValueError('criteria must cover every motor')
    if any(o.recovery is None or not o.require_connected_at_end for o in protocol.observations):raise ValueError('every motor requires connected-at-end and sampled recovery criteria')
    for o in protocol.observations:
        r=o.recovery
        if r.from_s+r.maximum_delay_s>spec.induction.duration_s+1e-12 or spec.induction.step_s>r.dwell_s/2:raise ValueError('recovery must fit horizon and contain at least two output steps')
    topology=compile_topology(spec.network)
    fixed=np.r_[0.,np.arange(math.ceil(spec.induction.duration_s/spec.induction.step_s))*spec.induction.step_s,spec.induction.duration_s,[e.time_s for e in spec.induction.events]]
    for w in protocol.voltage_windows:
        if w.node_id not in topology.node_to_bus:raise ValueError('unknown voltage-window node')
        if w.to_s>spec.induction.duration_s or w.to_s-w.from_s<2*spec.induction.step_s:raise ValueError('voltage window must fit horizon and contain two output steps')
        for endpoint in [w.from_s,w.to_s]:
            if min(abs(fixed-endpoint))>64*np.finfo(float).eps*max(1.,abs(endpoint)):raise ValueError('window endpoints must be on the fixed output/event grid')
    ordered=sorted(protocol.candidates,key=lambda c:c.id!=protocol.baseline_candidate_id)
    points=[]
    for i,c in enumerate(ordered):
        if {t.motor_id for t in c.timings}!=controlled:raise ValueError('every candidate must explicitly time every controlled motor')
        candidate=spec.model_copy(deep=True);candidate.id=spec.id+'::restart-'+c.id
        for timing in c.timings:
            motor=next(m for m in candidate.induction.motors if m.id==timing.motor_id)
            motor.contactor.pickup_delay_s=timing.pickup_delay_s;motor.contactor.minimum_off_time_s=timing.minimum_off_time_s
        candidate=StudySpec.model_validate(candidate.model_dump(mode='json'))
        if len(_encoded(candidate.model_dump(mode='json')))>32_000_000:raise ValueError('restart candidate byte budget')
        checked=validate_induction(candidate.network,candidate.induction)
        if not checked['ok']:raise ValueError('unsupported candidate: '+str(checked['errors']))
        points.append(dict(id=f'case-{i:03d}',nominal=i==0,candidate_id=c.id,absolute_timings=[t.model_dump(mode='json') for t in c.timings],study=candidate))
    return points


def _voltage_assessment(spec,report,protocol):
    times=np.asarray(report['time_s']);sides=np.asarray(report['sample_side']);topology=compile_topology(spec.network);rows={}
    for w in protocol.voltage_windows:
        bus=topology.node_to_bus[w.node_id];voltage=np.asarray(report['channels'].get(bus+'.v',[]),dtype=float)
        if voltage.shape!=times.shape or not np.isfinite(voltage).all() or np.any(voltage<0):raise ValueError('invalid network voltage channel')
        epsilon=64*np.finfo(float).eps*max(1.,w.to_s)
        if not all(np.any(abs(times-t)<=epsilon) for t in [w.from_s,w.to_s]):raise ValueError('missing sampled voltage-window boundary')
        selected=(times>=w.from_s-epsilon)&(times<=w.to_s+epsilon)&~((abs(times-w.from_s)<=epsilon)&(sides=='pre_event'))
        values=voltage[selected]
        if len(np.unique(times[selected]))<3:raise ValueError('insufficient sampled voltage window')
        minimum=float(min(values));maximum=float(max(values))
        rows[w.id]=dict(node_id=w.node_id,bus_id=bus,minimum_sampled_voltage_pu=minimum,maximum_sampled_voltage_pu=maximum,samples=int(sum(selected)),within_limits=minimum>=w.minimum_voltage_pu and maximum<=w.maximum_voltage_pu)
    return dict(voltage_windows=rows,additional_within_limits=all(r['within_limits'] for r in rows.values()))


def _selection(rows,complete):
    passing=[r['candidate_id'] for r in rows if r['numerically_completed'] and r['within_limits']] if complete else []
    return dict(candidates_within_sampled_limits=passing,engineering_assessment='finite_candidates_within_limits' if complete and passing else 'no_declared_candidate_within_limits' if complete else 'inconclusive',automatic_recommendation=False,global_optimum_claim=False,continuous_extrema_certified=False)


def run_motor_restart(spec,protocol,out,*,cancelled=None):
    started=time.monotonic()
    spec=StudySpec.model_validate(spec.model_dump(mode='json'))
    protocol=MotorRestartProtocol.model_validate(protocol.model_dump(mode='json') if isinstance(protocol,MotorRestartProtocol) else protocol)
    points=build_restart_points(spec,protocol)
    from power_grid_env.motor_bundle import runtime_identity
    environment=runtime_identity()
    return run_frozen_motor_points(spec,protocol,Path(out),points,cancelled=cancelled,result_kind='motor-restart',extra_assess=_voltage_assessment,extra_snapshot=_selection,metadata_budget=2_000_000,started=started,environment_snapshot=environment,environment_provider=runtime_identity)
