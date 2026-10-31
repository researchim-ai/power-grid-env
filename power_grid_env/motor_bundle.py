"""Sealed motor-study artifacts and strict numerical replay outside the repository.

Checksums detect file changes; they do not authenticate field provenance.
"""
from __future__ import annotations
import hashlib
import json
import math
from importlib.metadata import version,PackageNotFoundError
import os
from pathlib import Path
import platform
from typing import Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.results import SolveOptions
from power_grid_env.induction_network import validate_induction
from power_grid_env.motor_trace import MotorTraceProtocol,compare_motor_trace,validate_motor_result

MAX_TOTAL_BYTES=128_000_000
MAX_FILE_BYTES=64_000_000
BASE_FILES={'study.json','result.json','criteria.json','environment.json'}
TRACE_FILES={'trace.csv','trace-protocol.json','comparison.json'}
RUNTIME_PACKAGES=('numpy','scipy','pydantic','pydantic_core','opendssdirect.py','dss-python','dss-python-backend','pyarrow','gymnasium','pyyaml','jsonschema','annotated-types','typing-extensions','typing-inspection','cloudpickle','farama-notifications','attrs','jsonschema-specifications','referencing','rpds-py','cffi','pycparser')


class MotorReplayCriteria(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    schema_version: Literal['power-grid-env.motor-replay/v1']='power-grid-env.motor-replay/v1'
    time_absolute_s: float=Field(gt=0)
    pu_absolute: float=Field(gt=0)
    power_absolute_va: float=Field(gt=0)
    angle_absolute_rad: float=Field(gt=0)
    thermal_absolute: float=Field(gt=0)
    per_run_timeout_s: float=Field(default=60.,gt=0,le=600)


def _encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def _sha(raw):return hashlib.sha256(raw).hexdigest()


def runtime_identity():
    try:packages={name:version(name) for name in RUNTIME_PACKAGES}
    except PackageNotFoundError as exc:raise ValueError('required runtime package metadata unavailable: '+str(exc)) from exc
    return dict(python=platform.python_version(),system=platform.system(),machine=platform.machine(),packages=packages,
        thread_settings={k:os.environ.get(k) for k in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','BLIS_NUM_THREADS']})


def _read_bounded(path,limit=MAX_FILE_BYTES):
    with Path(path).open('rb') as stream:raw=stream.read(limit+1)
    if len(raw)>limit:raise ValueError('bundle member size budget exceeded')
    return raw


def _json(raw):
    def reject(value):raise ValueError('nonfinite JSON constant: '+value)
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate JSON key: '+key)
            result[key]=value
        return result
    def finite_float(token):
        value=float(token)
        if not math.isfinite(value):raise ValueError('nonfinite JSON number: '+token[:80])
        return value
    return json.loads(raw,parse_constant=reject,parse_float=finite_float,object_pairs_hook=pairs)


def _validate_result(spec,report):
    if spec.study!='motor_start' or spec.backend!='scipy' or spec.induction is None:raise ValueError('motor_start/scipy bundle required')
    if spec.rms is not None or spec.coupled is not None or spec.scenario is not None or spec.requested_motors or spec.options!=SolveOptions():raise ValueError('plain motor study required')
    if not validate_induction(spec.network,spec.induction)['ok']:raise ValueError('unsupported motor scope')
    if report.get('status')!='completed':raise ValueError('completed numerical result required')
    if any(report.get(key) is not False for key in ['industrial_profile_qualified','field_validated']):raise ValueError('simulation must not promote qualification claims')
    for motor in spec.induction.motors:validate_motor_result(spec,report,motor.id)
    for key in ['maximum_kcl_residual_pu','maximum_power_balance_residual_pu']:
        value=report.get(key)
        if not isinstance(value,(int,float)) or not np.isfinite(value) or not 0<=value<=1e-8:raise ValueError('invalid balance residual '+key)
    channels=report.get('channels')
    if not isinstance(channels,dict):raise ValueError('channel mapping required')
    time=np.asarray(report['time_s'])
    for key,values in channels.items():
        array=np.asarray(values,dtype=float)
        if array.shape!=time.shape or not np.isfinite(array).all():raise ValueError('invalid channel '+key)
        group=_channel_group(key)
        if group=='count' and (np.any(array<0) or np.any(array!=np.floor(array))):raise ValueError('invalid count channel '+key)
        if group=='discrete' and not np.all((array==0)|(array==1)):raise ValueError('invalid discrete channel '+key)

    if any(side not in {'initial','regular','pre_event','post_event'} for side in report['sample_side']):raise ValueError('invalid sample side')
    events=report.get('event_log')
    if not isinstance(events,list):raise ValueError('event log required')
    previous=0.
    for event in events:
        t=event.get('time_s') if isinstance(event,dict) else None
        if not isinstance(t,(float,int)) or not np.isfinite(t) or not previous<=t<=spec.induction.duration_s:raise ValueError('invalid event time')
        if not isinstance(event.get('kind'),str) or not isinstance(event.get('equipment_id'),str):raise ValueError('invalid event identity')
        previous=t


def _channel_group(key):
    suffix=key.rsplit('.',1)[-1]
    if suffix in {'connected','thermal_latched','contactor_voltage_available','contactor_pending_restart','contactor_timer_active'}:return 'discrete'
    if suffix=='contactor_restart_count':return 'count'
    if suffix in {'p_w','q_var','rotor1_copper_loss_w','rotor2_copper_loss_w'}:return 'power_absolute_va'
    if suffix=='contactor_timer_remaining_s':return 'time_absolute_s'
    if suffix=='a':return 'angle_absolute_rad'
    if suffix=='thermal_level':return 'thermal_absolute'
    if suffix in {'v','speed_pu','slip','e_real_pu','e_imag_pu','cage1_e_real_pu','cage1_e_imag_pu','cage2_e_real_pu','cage2_e_imag_pu','rotor1_current_pu','rotor2_current_pu','current_pu','bus_voltage_pu','terminal_voltage_pu','terminal_e_real_pu','terminal_e_imag_pu','breaker_voltage_difference_pu','contactor_sensed_voltage_pu','te_pu','load_torque_pu','load_scale'}:return 'pu_absolute'
    raise ValueError('unsupported replay channel: '+key)


def verify_motor_bundle(directory):
    root=Path(directory)
    if root.is_symlink() or not root.is_dir():raise ValueError('bundle must be an ordinary directory')
    members=list(root.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in members):raise ValueError('bundle contains links/directories')
    if len(members)>8 or sum(p.stat().st_size for p in members)>MAX_TOTAL_BYTES:raise ValueError('bundle size budget exceeded')
    def read(name):return _read_bounded(root/name,262144 if name=='manifest.json' else MAX_FILE_BYTES)
    manifest_bytes=read('manifest.json');manifest=_json(manifest_bytes)
    if not isinstance(manifest,dict) or manifest.get('schema_version')!='power-grid-env.motor-bundle/v1':raise ValueError('unknown motor bundle schema')
    if any(manifest.get(key) is not False for key in ['provenance_authenticated','industrial_profile_qualified','field_validated']):raise ValueError('bundle must not promote provenance or qualification claims')
    hashes=manifest.get('files')
    if not isinstance(hashes,dict) or set(hashes) not in [BASE_FILES,BASE_FILES|TRACE_FILES]:raise ValueError('invalid bundle inventory')
    if {p.name for p in members}!=set(hashes)|{'manifest.json'}:raise ValueError('unlisted/missing bundle member')
    data={name:read(name) for name in hashes}
    if sum(map(len,data.values()))+len(manifest_bytes)>MAX_TOTAL_BYTES:raise ValueError('bundle size budget exceeded')
    if any(_sha(raw)!=hashes[name] for name,raw in data.items()):raise ValueError('bundle checksum mismatch')
    if manifest.get('code_sha256')!=package_fingerprint():raise ValueError('bundle code mismatch')
    if _json(data['environment.json'])!=runtime_identity():raise ValueError('bundle runtime mismatch')
    spec=StudySpec.model_validate(_json(data['study.json']));criteria=MotorReplayCriteria.model_validate(_json(data['criteria.json']))
    report=_json(data['result.json']);_validate_result(spec,report)
    comparison=None
    if TRACE_FILES<=set(hashes):
        protocol=MotorTraceProtocol.model_validate(_json(data['trace-protocol.json']))
        comparison=compare_motor_trace(spec,report,root/'trace.csv',protocol)
        if _encoded(comparison)!=_encoded(_json(data['comparison.json'])):raise ValueError('inconsistent stored measurement comparison')
    return spec,criteria,report,comparison,_sha(manifest_bytes)


def export_motor_bundle(spec,criteria,out,*,trace=None,trace_protocol=None,cancelled=None):
    spec=StudySpec.model_validate(spec.model_dump(mode='json'))
    criteria=MotorReplayCriteria.model_validate(criteria.model_dump(mode='json') if isinstance(criteria,MotorReplayCriteria) else criteria)
    if spec.study!='motor_start' or spec.backend!='scipy':raise ValueError('motor_start/scipy bundle required')
    if (trace is None)!=(trace_protocol is None):raise ValueError('trace and protocol must be supplied together')
    root=Path(out)
    if root.is_symlink() or (root.exists() and (not root.is_dir() or any(root.iterdir()))):raise ValueError('new or empty ordinary bundle directory required')
    root.mkdir(parents=True,exist_ok=True);lock=(root/'.bundle.lock').open('x')
    try:
        identity=runtime_identity();code=package_fingerprint()
        report=run_study(spec,isolate=True,wall_timeout_s=criteria.per_run_timeout_s,cancelled=cancelled)
        if not report.get('completed'):raise ValueError('cannot seal incomplete study: '+str(report.get('status')))
        _validate_result(spec,report)
        files={'study.json':_encoded(spec.model_dump(mode='json')),'criteria.json':_encoded(criteria.model_dump(mode='json')),
               'environment.json':_encoded(identity),'result.json':_encoded(report)}
        comparison=None
        if trace is not None:
            with Path(trace).open('rb') as stream:raw=stream.read(32_000_001)
            if len(raw)>32_000_000:raise ValueError('trace byte budget exceeded')
            cfg=MotorTraceProtocol.model_validate(trace_protocol.model_dump(mode='json') if isinstance(trace_protocol,MotorTraceProtocol) else trace_protocol)
            # Compare exactly the bytes to be sealed, rather than rereading a moving input file.
            (root/'trace.csv').write_bytes(raw)
            comparison=compare_motor_trace(spec,report,root/'trace.csv',cfg)
            files.update({'trace.csv':raw,'trace-protocol.json':_encoded(cfg.model_dump(mode='json')),'comparison.json':_encoded(comparison)})
        manifest=dict(schema_version='power-grid-env.motor-bundle/v1',code_sha256=code,files={name:_sha(raw) for name,raw in files.items()},
            provenance_authenticated=False,industrial_profile_qualified=False,field_validated=False)
        if max(map(len,files.values()))>MAX_FILE_BYTES or sum(map(len,files.values()))+len(_encoded(manifest))>MAX_TOTAL_BYTES:raise ValueError('bundle size budget exceeded')
        for name,raw in files.items():
            temporary=root/(name+'.tmp');temporary.write_bytes(raw);temporary.replace(root/name)
        if code!=package_fingerprint() or identity!=runtime_identity():raise ValueError('code/runtime changed while sealing')
        (root/'manifest.json.tmp').write_bytes(_encoded(manifest));(root/'manifest.json.tmp').replace(root/'manifest.json')
        return dict(completed=True,status='sealed',files=len(files),code_sha256=code,
            measurement_comparison_passed=None if comparison is None else comparison['passed'],
            provenance_authenticated=False,industrial_profile_qualified=False,field_validated=False)
    finally:lock.close();(root/'.bundle.lock').unlink(missing_ok=True)


def _event_matches(actual,expected,criteria,key=''):
    """Compare the full event audit, including nested reclose diagnostics."""
    if isinstance(expected,dict):
        return isinstance(actual,dict) and set(actual)==set(expected) and all(_event_matches(actual[k],v,criteria,k) for k,v in expected.items())
    if isinstance(expected,list):
        return isinstance(actual,list) and len(actual)==len(expected) and all(_event_matches(a,b,criteria,key) for a,b in zip(actual,expected))
    if expected is None or isinstance(expected,(str,bool)):
        return type(actual) is type(expected) and actual==expected
    if isinstance(expected,(int,float)):
        if isinstance(actual,bool) or not isinstance(actual,(int,float)) or not np.isfinite([actual,expected]).all():return False
        if key=='restart_count':return type(actual) is int and type(expected) is int and actual==expected
        if key=='phase_defined_above_voltage_pu':return actual==expected
        limit=criteria.time_absolute_s if key=='time_s' else criteria.thermal_absolute if key in {'level','threshold'} else criteria.angle_absolute_rad if key.endswith('_rad') else criteria.pu_absolute
        error=actual-expected
        if key.endswith('_rad'):error=float(np.angle(np.exp(1j*error)))
        return abs(error)<=limit
    return False


def replay_motor_bundle(directory,*,cancelled=None):
    spec,criteria,expected,comparison,manifest_sha=verify_motor_bundle(directory)
    actual=run_study(spec,isolate=True,wall_timeout_s=criteria.per_run_timeout_s,cancelled=cancelled)
    if not actual.get('completed'):return dict(completed=False,repeatable=False,status=str(actual.get('status')),industrial_profile_qualified=False,field_validated=False)
    _validate_result(spec,actual)
    if _sha(_read_bounded(Path(directory)/'manifest.json',262144))!=manifest_sha:raise ValueError('bundle changed during replay')
    axes_match=actual['sample_side']==expected['sample_side'] and len(actual['time_s'])==len(expected['time_s'])
    if not axes_match:raise ValueError('replay sample-side/time-grid structure differs')
    if set(actual['channels'])!=set(expected['channels']):raise ValueError('replay channel inventory differs')
    time_error=float(max(abs(np.asarray(actual['time_s'])-expected['time_s'])))
    metrics={}
    for key,values in expected['channels'].items():
        error=np.asarray(actual['channels'][key])-values
        group=_channel_group(key)
        if group=='angle_absolute_rad':error=np.angle(np.exp(1j*error))
        limit=0. if group in {'discrete','count'} else getattr(criteria,group)
        maximum=float(max(abs(error)))
        metrics[key]=dict(maximum_absolute_error=maximum,absolute_tolerance=limit,passed=maximum<=limit)
    events_match=_event_matches(actual['event_log'],expected['event_log'],criteria)
    passed=time_error<=criteria.time_absolute_s and events_match and all(row['passed'] for row in metrics.values())
    return dict(completed=True,repeatable=passed,status='repeatable' if passed else 'outside_repeatability_limits',
        maximum_time_error_s=time_error,event_audit_matches=events_match,metrics=metrics,code_sha256=package_fingerprint(),
        measurement_comparison_passed=None if comparison is None else comparison['passed'],
        bundle_manifest_sha256=manifest_sha,
        provenance_authenticated=False,industrial_profile_qualified=False,field_validated=False)
