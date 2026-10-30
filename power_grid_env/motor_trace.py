"""Strict SI CSV comparison; passing a trace never releases an industrial profile."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from pathlib import Path
from typing import Literal
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash

COLUMNS = ('time_s', 'current_a', 'voltage_ll_v', 'speed_pu')
MAX_BYTES = 32_000_000
MAX_ROWS = 100_000


class TraceTolerances(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    current_a: float = Field(gt=0)
    voltage_ll_v: float = Field(gt=0)
    speed_pu: float = Field(gt=0)


class MotorTraceProtocol(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    schema_version: Literal['power-grid-env.motor-trace/v1'] = 'power-grid-env.motor-trace/v1'
    study_id: str = Field(min_length=1)
    motor_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    source_type: Literal['synthetic_reference', 'published_reference', 'field_measurement']
    csv_sha256: str = Field(pattern='^[0-9a-f]{64}$')
    used_for_parameter_fitting: bool
    time_offset_s: float = 0.
    maximum_sample_gap_s: float = Field(gt=0)
    tolerances: TraceTolerances


def _read_csv(path, expected_sha):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES:
        raise ValueError('trace byte budget exceeded')
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError('trace checksum mismatch')
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), strict=True)
    if reader.fieldnames is None or len(reader.fieldnames) != len(COLUMNS) or set(reader.fieldnames) != set(COLUMNS):
        raise ValueError('exact SI columns required: '+','.join(COLUMNS))
    rows = []
    try:
        for row in reader:
            if len(rows) >= MAX_ROWS:
                raise ValueError('trace row budget exceeded')
            if set(row) != set(COLUMNS) or any(row[c] is None or not row[c].strip() for c in COLUMNS):
                raise ValueError('missing or extra trace cell')
            rows.append([float(row[c]) for c in COLUMNS])
    except csv.Error as exc:
        raise ValueError('malformed CSV: '+str(exc)) from exc
    values = np.asarray(rows, dtype=float)
    if len(rows) < 3 or not np.isfinite(values).all():
        raise ValueError('at least three finite trace samples required')
    if np.any(np.diff(values[:, 0]) <= 0):
        raise ValueError('measurement timestamps must strictly increase')
    if np.any(values[:, 1:3] < 0):
        raise ValueError('RMS current/voltage cannot be negative')
    return values


def validate_motor_result(spec, report, motor_id):
    """Validate identity, horizon and channels; return time and SI I/U/speed."""
    if spec.study != 'motor_start' or spec.backend != 'scipy' or spec.induction is None or spec.coupled is not None:
        raise ValueError('motor_start/scipy study required')
    if not isinstance(report, dict) or report.get('completed') is not True or report.get('study') != 'motor_start' or report.get('backend') != 'scipy':
        raise ValueError('completed motor simulation required')
    if report.get('study_id') != spec.id or report.get('network_sha256') != canonical_hash(spec.network) or report.get('settings') != spec.induction.model_dump(mode='json'):
        raise ValueError('simulation identity/settings mismatch')
    if report.get('code_sha256') != package_fingerprint():
        raise ValueError('stale simulation code')
    motor = next((m for m in spec.induction.motors if m.id == motor_id), None)
    if motor is None:
        raise ValueError('unknown motor ID')
    time = np.asarray(report.get('time_s', []), dtype=float)
    sides = report.get('sample_side', [])
    if time.ndim != 1 or len(time) < 2 or len(sides) != len(time) or not np.isfinite(time).all() or np.any(np.diff(time) < 0):
        raise ValueError('invalid simulation time grid')
    if abs(time[0]) > 1e-9 or abs(time[-1]-spec.induction.duration_s) > 1e-9:
        raise ValueError('incomplete simulation horizon')
    for i in range(len(time)-1):
        if time[i] == time[i+1] and (sides[i] != 'pre_event' or sides[i+1] != 'post_event'):
            raise ValueError('ambiguous duplicate simulation timestamp')
    ibase = motor.rated_va/(math.sqrt(3)*motor.rated_ll_v)
    channels = report.get('channels', {})
    if not isinstance(channels, dict):
        raise ValueError('simulation channels must be a mapping')
    arrays = []
    for suffix, factor in [('current_pu', ibase), ('bus_voltage_pu', motor.rated_ll_v), ('speed_pu', 1.)]:
        values = np.asarray(channels.get(motor_id+'.'+suffix, []), dtype=float)
        if values.shape != time.shape or not np.isfinite(values).all():
            raise ValueError('missing/nonfinite simulation channel: '+suffix)
        if suffix in {'current_pu','bus_voltage_pu'} and np.any(values<0):
            raise ValueError('negative simulated RMS channel: '+suffix)
        with np.errstate(over='ignore', invalid='ignore'):
            scaled = values*factor
        if not np.isfinite(scaled).all():
            raise ValueError('simulation channel conversion overflow')
        arrays.append(scaled)
    return time, np.column_stack(arrays)


def _right_continuous_interpolate(time, values, target):
    # Last entry at an event time is its post-event limit. Between events use
    # the previous post-event and next pre-event samples, never average a jump.
    left = np.searchsorted(time, target, side='right')-1
    right = np.minimum(left+1, len(time)-1)
    span = time[right]-time[left]
    weight = np.divide(target-time[left], span, out=np.zeros_like(target), where=span > 0)
    return values[left]+weight[:, None]*(values[right]-values[left])


def compare_motor_trace(spec, report, csv_path, protocol):
    """Compare a full-horizon RMS trace with fixed limits; no lag search or imputation."""
    if not isinstance(protocol, MotorTraceProtocol):
        protocol = MotorTraceProtocol.model_validate(protocol)
    if protocol.study_id != spec.id:
        raise ValueError('protocol study ID mismatch')
    time, simulation = validate_motor_result(spec, report, protocol.motor_id)
    measured = _read_csv(csv_path, protocol.csv_sha256)
    measured[:, 0] += protocol.time_offset_s
    target = measured[:, 0]
    if abs(target[0]-time[0]) > 1e-9 or abs(target[-1]-time[-1]) > 1e-9:
        raise ValueError('trace must cover exactly the full study horizon; extrapolation/cropping forbidden')
    target[0], target[-1] = time[0], time[-1]
    gaps = np.diff(target)
    if np.any(gaps <= 0) or np.max(gaps) > protocol.maximum_sample_gap_s+1e-12:
        raise ValueError('measurement gap exceeds declared sampling budget')
    predicted = _right_continuous_interpolate(time, simulation, target)
    metrics = {}
    for i, key in enumerate(COLUMNS[1:]):
        with np.errstate(over='ignore', invalid='ignore'):
            error = predicted[:, i]-measured[:, i+1]
        if not np.isfinite(error).all():
            raise ValueError('trace comparison overflow')
        maximum = float(max(abs(error)))
        rmse = maximum*float(np.sqrt(np.mean((error/maximum)**2))) if maximum else 0.
        tolerance = getattr(protocol.tolerances, key)
        metrics[key] = {'maximum_absolute_error': maximum, 'rmse': rmse,
                        'absolute_tolerance': tolerance, 'passed': maximum <= tolerance}
    passed = all(m['passed'] for m in metrics.values())
    return {'completed': True, 'passed': passed, 'status': 'within_tolerance' if passed else 'outside_tolerance',
            'study_id': spec.id, 'motor_id': protocol.motor_id, 'samples': len(target), 'metrics': metrics,
            'code_sha256': package_fingerprint(), 'network_sha256': canonical_hash(spec.network),
            'simulation_sha256': hashlib.sha256(json.dumps(report, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest(), 'protocol_sha256': canonical_hash(protocol),
            'protocol': protocol.model_dump(mode='json'),
            'event_sampling': 'right-continuous post-event limit at exact switching timestamps',
            'provenance_authenticated': False, 'industrial_profile_qualified': False, 'field_validated': False}
