"""Bounded deterministic motor parameter experiments; no statistical qualification.

Every point reinitializes and executes its own frozen StudySpec in a killable
worker. Failed or omitted points make the finite-sample assessment inconclusive.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from pathlib import Path
import time
from typing import Annotated, Literal
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.motor_trace import validate_motor_result
from power_grid_env.induction_network import validate_induction
from power_grid_env.study_spec import STUDY_SCHEMA, StudySpec, run_study
from power_grid_env.results import SolveOptions

RESERVED_METADATA_BYTES = 256_000
Factor = Annotated[float, Field(gt=0, le=1000)]
Parameter = Literal['inertia_s', 'stator_r_pu', 'stator_x_pu', 'rotor_r_pu', 'rotor_x_pu',
                    'magnetizing_x_pu', 'mechanical_torque', 'thermal_time_constant_s',
                    'thermal_trip_level', 'source_series_impedance', 'line_series_impedance']


class ParameterAxis(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=128)
    parameter: Parameter
    equipment_id: str = Field(min_length=1, max_length=128)
    factors: list[Factor] = Field(min_length=2, max_length=9)

    @model_validator(mode='after')
    def nominal_required(self):
        if 1. not in self.factors or len(set(self.factors)) != len(self.factors):
            raise ValueError('unique positive factors including nominal 1.0 required')
        return self


class RecoveryWindow(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    from_s: float = Field(ge=0)
    minimum_speed_pu: float = Field(ge=0)
    dwell_s: float = Field(gt=0)
    maximum_delay_s: float = Field(gt=0)

    @model_validator(mode='after')
    def useful_window(self):
        if self.maximum_delay_s < self.dwell_s:
            raise ValueError('recovery delay must include its dwell interval')
        return self


class MotorAssessment(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    motor_id: str = Field(min_length=1, max_length=128)
    minimum_final_speed_pu: float = Field(ge=0)
    minimum_bus_voltage_pu: float = Field(ge=0)
    maximum_current_pu: float = Field(gt=0)
    require_connected_at_end: bool = True
    recovery: RecoveryWindow | None = None


class MotorSensitivityProtocol(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    schema_version: Literal['power-grid-env.motor-sensitivity/v1'] = 'power-grid-env.motor-sensitivity/v1'
    id: str = Field(min_length=1, max_length=128)
    design: Literal['one_at_a_time', 'cartesian'] = 'one_at_a_time'
    axes: list[ParameterAxis] = Field(min_length=1, max_length=8)
    observations: list[MotorAssessment] = Field(min_length=1, max_length=32)
    per_case_timeout_s: float = Field(default=30., gt=0, le=600)
    total_timeout_s: float = Field(default=300., gt=0, le=3600)
    maximum_output_bytes: int = Field(default=128_000_000, ge=1_000_000, le=512_000_000)

    @model_validator(mode='after')
    def unique_and_bounded(self):
        if len({a.id for a in self.axes}) != len(self.axes) or len({(a.parameter,a.equipment_id) for a in self.axes}) != len(self.axes):
            raise ValueError('duplicate axis or parameter target')
        if len({o.motor_id for o in self.observations}) != len(self.observations):
            raise ValueError('duplicate observed motor')
        count = 1+sum(len(a.factors)-1 for a in self.axes) if self.design == 'one_at_a_time' else math.prod(len(a.factors) for a in self.axes)
        if count > 32:
            raise ValueError('at most 32 parameter points supported')
        return self


def _target(spec, axis):
    if axis.parameter == 'source_series_impedance':
        items = spec.network.sources
    elif axis.parameter == 'line_series_impedance':
        items = spec.network.lines
    else:
        items = spec.induction.motors
    target = next((item for item in items if item.id == axis.equipment_id), None)
    if target is None:
        raise ValueError('unknown parameter target: '+axis.id)
    if axis.parameter.startswith('thermal_') and target.thermal is None:
        raise ValueError('thermal parameter requires an explicit thermal passport')
    return target


def _apply(spec, axis, factor):
    target = _target(spec, axis)
    if axis.parameter == 'mechanical_torque':
        if target.mechanical_load is None:
            target.fan_torque_pu *= factor
        else:
            for key in ('viscous_torque_pu','quadratic_torque_pu','friction_torque_pu'):
                setattr(target.mechanical_load,key,getattr(target.mechanical_load,key)*factor)
    elif axis.parameter == 'source_series_impedance':
        for key in ('r_ohm','x_ohm','r0_ohm','x0_ohm'):
            if getattr(target,key) is not None:
                setattr(target,key,getattr(target,key)*factor)
    elif axis.parameter == 'line_series_impedance':
        target.r_ohm = (np.asarray(target.r_ohm)*factor).tolist()
        target.x_ohm = (np.asarray(target.x_ohm)*factor).tolist()
    elif axis.parameter.startswith('thermal_'):
        key = axis.parameter.removeprefix('thermal_')
        setattr(target.thermal,key,getattr(target.thermal,key)*factor)
    else:
        setattr(target,axis.parameter,getattr(target,axis.parameter)*factor)


def build_parameter_points(spec, protocol):
    """Validate all targets before execution; points always include nominal first."""
    protocol = MotorSensitivityProtocol.model_validate(protocol.model_dump(mode='json') if isinstance(protocol,MotorSensitivityProtocol) else protocol)
    spec = StudySpec.model_validate(spec.model_dump(mode='json'))
    if (spec.schema_version != STUDY_SCHEMA or spec.study != 'motor_start' or spec.backend != 'scipy'
            or spec.induction is None or spec.rms is not None or spec.coupled is not None or spec.scenario is not None
            or spec.requested_motors or spec.options != SolveOptions() or len(spec.id)>256):
        raise ValueError('plain motor_start/scipy StudySpec with bounded ID required')
    scope = validate_induction(spec.network,spec.induction)
    if not scope['ok']:
        raise ValueError('base study is outside motor scope: '+str(scope['errors']))
    for axis in protocol.axes:
        _target(spec,axis)
    for observation in protocol.observations:
        if observation.motor_id not in {m.id for m in spec.induction.motors}:
            raise ValueError('unknown observed motor')
        recovery = observation.recovery
        if recovery and recovery.from_s+recovery.maximum_delay_s > spec.induction.duration_s+1e-12:
            raise ValueError('recovery assessment must fit the study horizon')
    nominal = tuple(1. for _ in protocol.axes)
    if protocol.design == 'cartesian':
        combinations = [nominal, *(point for point in itertools.product(*(a.factors for a in protocol.axes)) if point != nominal)]
    else:
        combinations = [nominal]
        for i, axis in enumerate(protocol.axes):
            for factor in axis.factors:
                if factor != 1.:
                    point = list(nominal); point[i] = factor; combinations.append(tuple(point))
    points = []
    for i, factors in enumerate(combinations):
        point = {'id':f'case-{i:03d}', 'nominal':i==0, 'factors':dict(zip((a.id for a in protocol.axes),factors))}
        candidate = spec.model_copy(deep=True)
        candidate.id = spec.id+'::'+point['id']
        try:
            for axis, factor in zip(protocol.axes,factors):
                if factor != 1.:
                    _apply(candidate,axis,factor)
            candidate = StudySpec.model_validate(candidate.model_dump(mode='json'))
            checked = validate_induction(candidate.network,candidate.induction)
            if not checked['ok']:
                raise ValueError(str(checked['errors']))
            point['study'] = candidate
        except (ValueError, ArithmeticError) as exc:
            point['study'] = None
            point['error'] = str(exc)[:400]
        points.append(point)
    return points


def _assess(spec, report, observation):
    times, physical = validate_motor_result(spec,report,observation.motor_id)
    motor = next(m for m in spec.induction.motors if m.id==observation.motor_id)
    ibase = motor.rated_va/(np.sqrt(3)*motor.rated_ll_v)
    if not math.isfinite(ibase) or ibase<=0:
        raise ValueError('nonrepresentable motor current base')
    current = physical[:,0]/ibase
    voltage = physical[:,1]/motor.rated_ll_v
    speed = physical[:,2]
    connected = np.asarray(report['channels'].get(observation.motor_id+'.connected',[]),dtype=float)
    if connected.shape != times.shape or not np.isfinite(connected).all() or np.any((connected!=0)&(connected!=1)):
        raise ValueError('missing/invalid contactor channel')
    metrics = {'final_speed_pu':float(speed[-1]), 'minimum_sampled_bus_voltage_pu':float(min(voltage)),
               'peak_sampled_current_pu':float(max(current)), 'connected_at_end':bool(connected[-1])}
    checks = {'final_speed':metrics['final_speed_pu']>=observation.minimum_final_speed_pu,
              'bus_voltage':metrics['minimum_sampled_bus_voltage_pu']>=observation.minimum_bus_voltage_pu,
              'current':metrics['peak_sampled_current_pu']<=observation.maximum_current_pu,
              'connected':not observation.require_connected_at_end or metrics['connected_at_end']}
    recovery = observation.recovery
    if recovery:
        start = confirmed = None
        # Recovery is right-continuous at contactor events, unlike extrema
        # which deliberately include both event sides.
        keep = np.r_[np.diff(times)!=0, True]
        for t, w, on in zip(times[keep],speed[keep],connected[keep]):
            if t < recovery.from_s:
                continue
            if not on or w < recovery.minimum_speed_pu:
                start = None
            elif start is None:
                start = float(t)
            if start is not None and t-start >= recovery.dwell_s-1e-12:
                confirmed = float(t); break
        delay = None if confirmed is None else confirmed-recovery.from_s
        metrics.update(recovery_window_start_s=start if confirmed is not None else None,
                       recovery_confirmed_s=confirmed,recovery_delay_s=delay)
        checks['recovery'] = delay is not None and delay<=recovery.maximum_delay_s+1e-12
    return {'metrics':metrics,'checks':checks,'within_limits':all(checks.values())}


def _encoded(payload):
    return json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')


def run_motor_sensitivity(spec, protocol, out, *, cancelled=None):
    """Run frozen points sequentially with per-point/total deadlines and disk budget."""
    started = time.monotonic()
    protocol = MotorSensitivityProtocol.model_validate(protocol.model_dump(mode='json') if isinstance(protocol,MotorSensitivityProtocol) else protocol)
    spec = StudySpec.model_validate(spec.model_dump(mode='json'))
    points = build_parameter_points(spec,protocol)
    root = Path(out)
    if root.exists() and any(root.iterdir()):
        raise ValueError('sensitivity output directory must be new or empty')
    root.mkdir(parents=True,exist_ok=True)
    lock = (root/'.sensitivity.lock').open('x')
    remaining = protocol.maximum_output_bytes-RESERVED_METADATA_BYTES
    artifacts, rows = {}, []
    code = package_fingerprint()
    stop = None
    def persist(relative,payload):
        nonlocal remaining
        data = _encoded(payload)
        if len(data)>remaining:
            return False
        path = root/relative; path.parent.mkdir(parents=True,exist_ok=True)
        temporary = path.with_suffix(path.suffix+'.tmp')
        temporary.write_bytes(data); temporary.replace(path)
        remaining -= len(data); artifacts[relative] = hashlib.sha256(data).hexdigest()
        return True
    def snapshot(status):
        complete = len(rows)==len(points) and all(r['numerically_completed'] for r in rows) and status=='completed'
        within = complete and all(r['within_limits'] for r in rows)
        engineering = 'within_sampled_limits' if within else 'outside_sampled_limits' if complete else 'inconclusive'
        return {'schema_version':'power-grid-env.motor-sensitivity-result/v1','completed':complete,'status':status,
                'all_cases_within_limits':within,'engineering_assessment':engineering,
                'expected_cases':len(points),'attempted_cases':len(rows),'cases':rows,
                'code_sha256':code,'base_study_sha256':canonical_hash(spec),'protocol_sha256':canonical_hash(protocol),
                'protocol':protocol.model_dump(mode='json'),'elapsed_s':time.monotonic()-started,
                'scope_note':'Finite declared samples only; no probability, interval-wide or global robustness guarantee',
                'industrial_profile_qualified':False,'field_validated':False}
    try:
        if not persist('base-study.json',spec.model_dump(mode='json')) or not persist('protocol.json',protocol.model_dump(mode='json')):
            stop = 'output_budget_exceeded'
        for point in points:
            if stop:
                break
            if cancelled is not None and cancelled():
                stop='cancelled'; break
            available = protocol.total_timeout_s-(time.monotonic()-started)
            if available<=0:
                stop='total_timeout'; break
            if package_fingerprint()!=code:
                stop='stale_code'; break
            row = {k:point[k] for k in ('id','nominal','factors')}
            row.update(numerically_completed=False,within_limits=False,observations=None,delta_from_baseline=None)
            variant = point['study']
            if variant is None:
                row.update(status='invalid_variant',error=point['error']); rows.append(row); continue
            row['study_sha256'] = canonical_hash(variant)
            folder = point['id']
            if not persist(folder+'/study.json',variant.model_dump(mode='json')):
                stop='output_budget_exceeded'; break
            available = protocol.total_timeout_s-(time.monotonic()-started)
            if available<=0:
                stop='total_timeout'; break
            report = run_study(variant,isolate=True,wall_timeout_s=min(available,protocol.per_case_timeout_s),cancelled=cancelled)
            if not persist(folder+'/result.json',report):
                row['status']='result_not_persisted'; rows.append(row); stop='output_budget_exceeded'; break
            row['status'] = str(report.get('status','invalid_result'))[:128]
            if report.get('completed') is True:
                try:
                    if report.get('code_sha256')!=code:
                        raise ValueError('result code changed during experiment')
                    assessed = {o.motor_id:_assess(variant,report,o) for o in protocol.observations}
                    row.update(numerically_completed=True,observations=assessed,
                               within_limits=all(o['within_limits'] for o in assessed.values()))
                except (ValueError,TypeError,KeyError) as exc:
                    row.update(status='invalid_result',error=str(exc)[:400])
            else:
                row['reason'] = str(report.get('reason',report.get('status','unknown')))[:400]
            rows.append(row)
            if report.get('status')=='cancelled':
                stop='cancelled'
            elif time.monotonic()-started>=protocol.total_timeout_s:
                stop='total_timeout'
            if rows[0]['numerically_completed'] and row['numerically_completed']:
                row['delta_from_baseline'] = {}
                for motor, observation in row['observations'].items():
                    baseline = rows[0]['observations'][motor]['metrics']
                    row['delta_from_baseline'][motor] = {key:float(value-baseline[key]) for key,value in observation['metrics'].items()
                        if type(value) in (float,int) and type(baseline.get(key)) in (float,int)}
            journal = _encoded(snapshot('running'))
            if len(journal)<=RESERVED_METADATA_BYTES//2:
                temporary=root/'run.json.tmp';temporary.write_bytes(journal);temporary.replace(root/'run.json')
        if stop is None:
            stop='completed' if all(r['numerically_completed'] for r in rows) else 'partial_failure'
        if package_fingerprint()!=code:
            stop='stale_code'
        result = snapshot(stop)
        envelope = {}
        for o in protocol.observations:
            values = [r['observations'][o.motor_id]['metrics'] for r in rows if r['numerically_completed']]
            envelope[o.motor_id] = {key:{'minimum':min(v[key] for v in values if type(v.get(key)) in (float,int)),
                                        'maximum':max(v[key] for v in values if type(v.get(key)) in (float,int)),
                                        'available_cases':sum(type(v.get(key)) in (float,int) for v in values)}
                for key in (values[0] if values else []) if any(type(v.get(key)) in (float,int) for v in values)}
        result['sampled_envelope'] = envelope
        manifest = {**artifacts,'run.json':hashlib.sha256(_encoded(result)).hexdigest()}
        payload, manifest_bytes = _encoded(result), _encoded(manifest)
        if len(payload)+len(manifest_bytes)>RESERVED_METADATA_BYTES:
            result = {'completed':False,'status':'metadata_budget_exceeded','all_cases_within_limits':False,'engineering_assessment':'inconclusive'}
            payload=_encoded(result);manifest_bytes=_encoded({**artifacts,'run.json':hashlib.sha256(payload).hexdigest()})
        (root/'run.json.tmp').write_bytes(payload);(root/'run.json.tmp').replace(root/'run.json')
        (root/'manifest.json').write_bytes(manifest_bytes)
        return result
    except (ValueError, RuntimeError, OSError, ArithmeticError, TypeError) as exc:
        result = snapshot('orchestration_failed')
        result['error'] = str(exc)[:400]
        try:
            payload = _encoded(result)
            manifest_bytes = _encoded({**artifacts,'run.json':hashlib.sha256(payload).hexdigest()})
            if len(payload)+len(manifest_bytes)<=RESERVED_METADATA_BYTES:
                (root/'run.json.tmp').write_bytes(payload);(root/'run.json.tmp').replace(root/'run.json')
                (root/'manifest.json').write_bytes(manifest_bytes)
        except OSError:
            pass
        return result
    finally:
        lock.close();(root/'.sensitivity.lock').unlink(missing_ok=True)
