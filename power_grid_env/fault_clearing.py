"""Frozen finite fault-clearing experiments; no critical-clearing-time certificate."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Annotated,Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.study_spec import StudySpec,run_study,STUDY_SCHEMA
from power_grid_env.results import SolveOptions
from power_grid_env.coupled_rms import validate_coupled,CoupledSettings

RESERVED_BYTES=256_000


class ClearingMotorCriterion(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    motor_id: str=Field(min_length=1,max_length=128)
    minimum_speed_pu: float=Field(ge=0)
    minimum_voltage_pu: float=Field(ge=0)
    require_connected: bool=True


class FaultClearingProtocol(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    schema_version: Literal['power-grid-env.fault-clearing/v1']='power-grid-env.fault-clearing/v1'
    fault_id: str=Field(min_length=1,max_length=128)
    durations_s: list[Annotated[float,Field(gt=0,le=60)]]=Field(min_length=2,max_length=32)
    final_dwell_s: float=Field(gt=0,le=60)
    maximum_angle_excursion_rad: float=Field(gt=0)
    maximum_speed_deviation_pu: float=Field(gt=0)
    motors: list[ClearingMotorCriterion]=Field(min_length=1,max_length=32)
    per_case_timeout_s: float=Field(default=30.,gt=0,le=600)
    total_timeout_s: float=Field(default=300.,gt=0,le=3600)
    maximum_output_bytes: int=Field(default=64_000_000,ge=300_000,le=1_000_000_000)

    @model_validator(mode='after')
    def unique(self):
        if self.durations_s!=sorted(set(self.durations_s)):raise ValueError('durations must be strictly increasing and unique')
        if len({m.motor_id for m in self.motors})!=len(self.motors):raise ValueError('duplicate observed motor')
        return self


def clearing_variants(spec,protocol):
    if spec.study!='coupled_rms' or spec.backend!='scipy' or spec.coupled is None:
        raise ValueError('coupled_rms/scipy study required')
    if any(e.kind=='generator_open' for e in spec.coupled.events):raise ValueError('generator opening requires a dedicated disconnected-rotor assessment protocol')
    if len(spec.id)>256:raise ValueError('bounded study ID required')
    if spec.schema_version!=STUDY_SCHEMA or spec.rms is not None or spec.induction is not None or spec.scenario is not None or spec.requested_motors or spec.options!=SolveOptions():
        raise ValueError('plain coupled study with default execution options required')
    scope=validate_coupled(spec.network,spec.coupled)
    if not scope['ok']:raise ValueError('unsupported coupled base study: '+str(scope['errors']))
    if protocol.fault_id not in {f.id for f in spec.coupled.faults}:raise ValueError('unknown target fault')
    if any(m.motor_id not in {p.id for p in spec.coupled.motors} for m in protocol.motors):raise ValueError('unknown observed motor')
    if spec.coupled.step_s>protocol.final_dwell_s/2:raise ValueError('final dwell requires at least two output steps')
    variants=[]
    for duration in protocol.durations_s:
        data=spec.model_dump(mode='json')
        fault=next(f for f in data['coupled']['faults'] if f['id']==protocol.fault_id)
        fault['clear_s']=fault['apply_s']+duration
        if max(f['clear_s'] for f in data['coupled']['faults'])>=spec.coupled.duration_s-protocol.final_dwell_s:
            raise ValueError('every fault must clear before final assessment window')
        variants.append(StudySpec.model_validate(data))
    return variants


def assess_clearing(spec,report,protocol):
    """Finite sampled limits, requiring identity, full horizon and fault audit."""
    if report.get('completed') is not True or report.get('status')!='completed' or report.get('study')!='coupled_rms' or report.get('backend')!='scipy' or report.get('code_sha256')!=package_fingerprint():raise ValueError('incomplete or stale result')
    if report.get('network_sha256')!=canonical_hash(spec.network) or canonical_hash(CoupledSettings.model_validate(report.get('settings')))!=canonical_hash(spec.coupled):raise ValueError('result study mismatch')
    times=np.asarray(report['time_s'],dtype=float);s=spec.coupled
    if times.ndim!=1 or len(times)<3 or not np.isfinite(times).all() or times[0]!=0 or abs(times[-1]-s.duration_s)>1e-12 or np.any(np.diff(times)<0):raise ValueError('invalid full-horizon time axis')
    if np.max(np.diff(times))>s.step_s*(1+1e-8):raise ValueError('missing output samples')
    expected=sorted((t,kind,f.id) for f in s.faults for t,kind in [(f.apply_s,'fault_apply'),(f.clear_s,'fault_clear')])
    actual=sorted((e['time_s'],e['kind'],e['equipment_id']) for e in report['event_log'] if e['kind'] in {'fault_apply','fault_clear'})
    if expected!=actual:raise ValueError('fault event audit mismatch')
    channels=report['channels']
    def channel(key):
        values=np.asarray(channels[key],dtype=float)
        if values.shape!=times.shape or not np.isfinite(values).all():raise ValueError('invalid channel '+key)
        return values
    angle=max(float(np.max(abs(channel(m.generator_id+'.delta')-channel(m.generator_id+'.delta')[0]))) for m in s.machines)
    speed=max(float(np.max(abs(channel(m.generator_id+'.omega')-1.))) for m in s.machines)
    start=s.duration_s-protocol.final_dwell_s
    # Include the preceding sample for conservative coverage of the left edge.
    first=max(0,int(np.searchsorted(times,start,side='right'))-1)
    observations={}
    for criterion in protocol.motors:
        prefix=criterion.motor_id+'.'
        low_speed=float(min(channel(prefix+'speed_pu')[first:]));low_voltage=float(min(channel(prefix+'bus_voltage_pu')[first:]))
        connected=bool(np.all(channel(prefix+'connected')[first:]==1.))
        passed=low_speed>=criterion.minimum_speed_pu and low_voltage>=criterion.minimum_voltage_pu and (connected or not criterion.require_connected)
        observations[criterion.motor_id]=dict(minimum_sampled_speed_pu=low_speed,minimum_sampled_voltage_pu=low_voltage,connected_through_window=connected,within_limits=passed)
    within=angle<=protocol.maximum_angle_excursion_rad and speed<=protocol.maximum_speed_deviation_pu and all(o['within_limits'] for o in observations.values())
    return dict(within_limits=within,sampled_maximum_angle_excursion_rad=angle,sampled_maximum_speed_deviation_pu=speed,final_window_from_s=start,first_included_sample_s=float(times[first]),motors=observations)


def run_fault_clearing(spec,protocol,out,*,cancelled=None):
    protocol=FaultClearingProtocol.model_validate(protocol.model_dump(mode='json') if isinstance(protocol,FaultClearingProtocol) else protocol)
    spec=StudySpec.model_validate(spec.model_dump(mode='json'));variants=clearing_variants(spec,protocol)
    root=Path(out)
    if root.exists() and any(root.iterdir()):raise ValueError('new or empty output directory required')
    root.mkdir(parents=True,exist_ok=True);lock=(root/'.clearing.lock').open('x')
    started=time.monotonic();code=package_fingerprint();remaining=protocol.maximum_output_bytes-RESERVED_BYTES
    artifacts={};rows=[];stop=None
    def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
    def persist(name,value):
        nonlocal remaining
        data=encoded(value)
        if len(data)>remaining:return False
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_bytes(data);tmp.replace(p)
        artifacts[name]=hashlib.sha256(data).hexdigest();remaining-=len(data);return True
    def snapshot(status):
        complete=status=='completed' and len(rows)==len(variants) and all(r['numerically_completed'] for r in rows)
        within=complete and all(r['assessment']['within_limits'] for r in rows)
        return dict(schema_version='power-grid-env.fault-clearing-result/v1',status=status,completed=complete,
            engineering_assessment='within_sampled_limits' if within else 'outside_sampled_limits' if complete else 'inconclusive',
            all_cases_within_limits=within,expected_cases=len(variants),attempted_cases=len(rows),cases=rows,
            base_study_sha256=canonical_hash(spec),protocol_sha256=canonical_hash(protocol),code_sha256=code,
            elapsed_s=time.monotonic()-started,critical_clearing_time_certified=False,industrial_profile_qualified=False,field_validated=False,competitive_superiority_proven=False,
            scope_note='Finite durations and sampled finite-horizon criteria only; no monotonicity, interval safety or asymptotic stability inference')
    def finalize(status,error=None):
        result=snapshot(status)
        if error:result['error']=str(error)[:400]
        payload=encoded(result);manifest=encoded({**artifacts,'run.json':hashlib.sha256(payload).hexdigest()})
        if len(payload)+len(manifest)>RESERVED_BYTES:
            result=dict(completed=False,status='metadata_budget_exceeded',engineering_assessment='inconclusive',all_cases_within_limits=False,critical_clearing_time_certified=False,industrial_profile_qualified=False,field_validated=False,competitive_superiority_proven=False)
            payload=encoded(result);manifest=encoded({**artifacts,'run.json':hashlib.sha256(payload).hexdigest()})
        (root/'run.json.tmp').write_bytes(payload);(root/'run.json.tmp').replace(root/'run.json');(root/'manifest.json').write_bytes(manifest)
        return result
    try:
        if not persist('base-study.json',spec.model_dump(mode='json')) or not persist('protocol.json',protocol.model_dump(mode='json')):stop='output_budget_exceeded'
        for index,variant in enumerate(variants):
            if stop:break
            if cancelled is not None and cancelled():stop='cancelled';break
            available=protocol.total_timeout_s-(time.monotonic()-started)
            if available<=0:stop='total_timeout';break
            if package_fingerprint()!=code:stop='stale_code';break
            folder=f'case-{index:03d}';row=dict(duration_s=protocol.durations_s[index],study_sha256=canonical_hash(variant),numerically_completed=False,assessment=None)
            if not persist(folder+'/study.json',variant.model_dump(mode='json')):stop='output_budget_exceeded';break
            available=protocol.total_timeout_s-(time.monotonic()-started)
            if available<=0:stop='total_timeout';break
            report=run_study(variant,isolate=True,wall_timeout_s=min(available,protocol.per_case_timeout_s),cancelled=cancelled)
            if not persist(folder+'/result.json',report):
                row['status']='result_not_persisted';rows.append(row);stop='output_budget_exceeded';break
            row['status']=str(report.get('status','invalid_result'))[:128]
            if report.get('completed') is True:
                try:
                    row['assessment']=assess_clearing(variant,report,protocol);row['numerically_completed']=True
                except (ValueError,KeyError,TypeError,IndexError) as exc:row.update(status='invalid_result',error=str(exc)[:400])
            rows.append(row)
            if report.get('status')=='cancelled':stop='cancelled'
            elif time.monotonic()-started>=protocol.total_timeout_s:stop='total_timeout'
            journal=encoded(snapshot('running'))
            if len(journal)<=RESERVED_BYTES//2:
                (root/'run.json.tmp').write_bytes(journal);(root/'run.json.tmp').replace(root/'run.json')
        if package_fingerprint()!=code:stop='stale_code'
        return finalize(stop or ('completed' if all(r['numerically_completed'] for r in rows) else 'partial_failure'))
    except (OSError,ValueError,RuntimeError,ArithmeticError,TypeError) as exc:
        return finalize('orchestration_failed',exc)
    finally:
        lock.close();(root/'.clearing.lock').unlink(missing_ok=True)
