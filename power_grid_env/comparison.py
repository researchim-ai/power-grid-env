"""Bounded cross-simulator comparison contracts; no universal league table.

Normalized traces are provider outputs on a declared physical axis. Verification
recomputes errors, coverage and conclusions; it does not authenticate the provider
or rerun a third-party solver. Missing required runs can never become N/A/pass.
"""
from pathlib import Path
from typing import Literal
import math

import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator

from power_grid_env.study_artifacts import encoded,digest,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,strict=True)


class ComparisonChannel(Strict):
    name:str=Field(min_length=1,max_length=128)
    unit:str=Field(min_length=1,max_length=64)
    absolute_limit:float=Field(gt=0)

    @model_validator(mode='after')
    def labels(self):
        if not self.name.strip() or not self.unit.strip():raise ValueError('named physical channel and unit required')
        return self


class ComparisonCase(Strict):
    id:str=Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')
    track:Literal['steady_state','rms_motor','flux_network','phase_emt','agent_workflow']
    provider:Literal['pandapower_balanced','andes_motor3','motulator_network','qwen_workflow','pscad_emt']
    coverage:Literal['required','not_applicable','not_performed']='required'
    exclusion_reason:str|None=Field(default=None,min_length=1,max_length=2048)
    reference_kind:Literal['independent_implementation','authored_independent_equations','shared_upstream']
    reference_backend:str=Field(min_length=1,max_length=128)
    reference_version:str=Field(min_length=1,max_length=128)
    alignment:Literal['exact','linear_event_free']='exact'
    sample_scope:Literal['steady_state','event_free_trajectory','right_event_limits']
    axis_quantity:Literal['bus_id','time_s']='time_s'
    primary_axis:list[float]=Field(default_factory=list,max_length=16001)
    reference_axis:list[float]=Field(default_factory=list,max_length=16001)
    input:dict=Field(min_length=1)
    mappings:list[str]=Field(min_length=1,max_length=32)
    channels:list[ComparisonChannel]=Field(min_length=1,max_length=256)

    @model_validator(mode='after')
    def declared(self):
        if (self.coverage=='required')!=(self.exclusion_reason is None):raise ValueError('only excluded cases require a reason')
        if self.exclusion_reason is not None and not self.exclusion_reason.strip():raise ValueError('exclusion reason must not be blank')
        if any(not text.strip() or len(text)>4096 for text in self.mappings):raise ValueError('explicit bounded physical mappings required')
        if len({c.name for c in self.channels})!=len(self.channels):raise ValueError('unique comparison channels required')
        if self.alignment=='linear_event_free' and self.sample_scope!='event_free_trajectory':raise ValueError('interpolation may not smooth event jumps')
        if self.coverage=='required':
            if (self.track=='steady_state')!=(self.axis_quantity=='bus_id'):raise ValueError('axis quantity must match the physical track')
            for a in [self.primary_axis,self.reference_axis]:
                if not a or not np.isfinite(a).all() or a[0]<0 or np.any(np.diff(a)<=0):raise ValueError('preregistered complete finite axes required')
            if self.alignment=='exact' and self.primary_axis!=self.reference_axis:raise ValueError('exact profile requires identical declared axes')
        if len(encoded(self.input))>8000000:raise ValueError('comparison input byte budget')
        return self


class ComparisonPlan(Strict):
    schema_version:Literal['power-grid-env.comparison/v1']='power-grid-env.comparison/v1'
    id:str=Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')
    cases:list[ComparisonCase]=Field(min_length=1,max_length=32)

    @model_validator(mode='after')
    def coverage(self):
        if len({c.id for c in self.cases})!=len(self.cases):raise ValueError('unique comparison case IDs required')
        if not any(c.coverage=='required' for c in self.cases):raise ValueError('at least one required comparison')
        if len(encoded(self.model_dump(mode='json')))>24000000:raise ValueError('comparison plan byte budget')
        return self


class ComparisonTrace(Strict):
    completed:bool
    case_input_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    producer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    backend:str=Field(min_length=1,max_length=128)
    backend_version:str=Field(min_length=1,max_length=128)
    source_sha256:dict[str,str]=Field(min_length=1,max_length=256)
    axis:list[float]=Field(min_length=1,max_length=16001)
    channels:dict[str,list[float]]=Field(min_length=1,max_length=256)
    wall_s:float=Field(ge=0)
    timing_scope:Literal['validation_only_uncontrolled']='validation_only_uncontrolled'

    @model_validator(mode='after')
    def valid(self):
        if any(len(v)!=64 or any(c not in '0123456789abcdef' for c in v) for v in self.source_sha256.values()):raise ValueError('source SHA256 identities required')
        a=np.asarray(self.axis,float)
        if not np.isfinite(a).all() or a[0]<0 or np.any(np.diff(a)<=0):raise ValueError('finite strictly increasing physical axis required')
        if sum(len(v) for v in self.channels.values())>1000000:raise ValueError('normalized trace scalar budget')
        for key,values in self.channels.items():
            if not key or len(values)!=len(a) or not np.isfinite(values).all():raise ValueError('complete finite normalized channels required')
        return self


def compare_traces(case,primary,reference):
    case=ComparisonCase.model_validate(case)
    if case.coverage!='required':raise ValueError('excluded cases must not submit a run')
    a=ComparisonTrace.model_validate(primary);b=ComparisonTrace.model_validate(reference)
    identity=digest(encoded(case.input));code=package_fingerprint()
    if any(t.case_input_sha256!=identity or t.producer_sha256!=code for t in [a,b]):raise ValueError('stale producer or different case inputs')
    if not a.completed or not b.completed:raise ValueError('both physical calculations must complete')
    if b.backend!=case.reference_backend or b.backend_version!=case.reference_version:raise ValueError('reference backend/version differs from preregistration')
    if a.axis!=case.primary_axis or b.axis!=case.reference_axis:raise ValueError('trace does not cover the preregistered physical axis')
    expected={c.name for c in case.channels}
    if set(a.channels)!=expected or set(b.channels)!=expected:raise ValueError('exact preregistered channel coverage required')
    if case.reference_kind!='shared_upstream' and a.backend==b.backend:raise ValueError('same backend cannot claim independence')
    if case.alignment=='exact':
        if a.axis!=b.axis:raise ValueError('exact physical axes differ')
    elif len(a.axis)<2 or len(b.axis)<2 or a.axis[0]!=b.axis[0] or a.axis[-1]!=b.axis[-1]:
        raise ValueError('event-free interpolation requires full matching horizon without extrapolation')
    metrics={}
    for channel in case.channels:
        actual=np.asarray(a.channels[channel.name]);wanted=np.asarray(b.channels[channel.name])
        if case.alignment!='exact':wanted=np.interp(a.axis,b.axis,wanted)
        with np.errstate(over='ignore',invalid='ignore'):delta=actual-wanted
        if not np.isfinite(delta).all():raise ValueError('comparison arithmetic overflow')
        error=float(max(abs(delta)))
        # scaled norm avoids overflowing mean(delta**2) for rejected traces.
        rmse=0. if error==0 else error*float(np.sqrt(np.mean((delta/error)**2)))
        metrics[channel.name]=dict(unit=channel.unit,max_absolute_error=error,rmse=rmse,absolute_limit=channel.absolute_limit,passed=error<=channel.absolute_limit)
    return dict(id=case.id,track=case.track,status='passed' if all(m['passed'] for m in metrics.values()) else 'failed',
                reference_kind=case.reference_kind,sample_scope=case.sample_scope,metrics=metrics,
                primary_backend=a.backend,reference_backend=b.backend,primary_version=a.backend_version,reference_version=b.backend_version,
                primary_wall_s=a.wall_s,reference_wall_s=b.wall_s,timing_comparable=False,speedup_claimed=False)


def summary_for(plan,rows):
    plan=ComparisonPlan.model_validate(plan)
    if len(rows)!=len(plan.cases) or [r['id'] for r in rows]!=[c.id for c in plan.cases]:raise ValueError('complete ordered case inventory required')
    required=[r for c,r in zip(plan.cases,rows,strict=True) if c.coverage=='required']
    passed=all(r['status']=='passed' for r in required)
    return dict(schema='power-grid-env.comparison-result/v1',id=plan.id,producer_sha256=package_fingerprint(),
        required_comparisons_passed=passed,declared_cases=len(rows),required_cases=len(required),
        independent_passes=sum(r['status']=='passed' and r['reference_kind']!='shared_upstream' for r in required),
        failed_required=sum(r['status']!='passed' for r in required),
        not_applicable=sum(c.coverage=='not_applicable' for c in plan.cases),not_performed=sum(c.coverage=='not_performed' for c in plan.cases),
        full_declared_inventory_executed=all(c.coverage=='required' for c in plan.cases) and passed,
        competitive_superiority_proven=False,performance_ranking_available=False,field_validated=False,cases=rows)


def evaluate_comparison(root):
    root=Path(root);plan=ComparisonPlan.model_validate(read_json(root/'input.json'));rows=[]
    for case in plan.cases:
        path=root/'cases'/case.id
        if case.coverage!='required':
            if path.exists() and any(path.iterdir()):raise ValueError('excluded case contains undeclared runs')
            rows.append(dict(id=case.id,track=case.track,status=case.coverage,reason=case.exclusion_reason,reference_kind=case.reference_kind));continue
        outcome=read_json(path/'outcome.json')
        if outcome=={'status':'completed'}:
            row=compare_traces(case,read_json(path/'primary.json'),read_json(path/'reference.json'))
        elif set(outcome)=={'status','reason'} and outcome['status']=='failed' and isinstance(outcome['reason'],str) and outcome['reason'].strip():
            row=dict(id=case.id,track=case.track,status='execution_failed',reason=outcome['reason'],reference_kind=case.reference_kind)
        else:raise ValueError('invalid provider outcome')
        rows.append(row)
    return summary_for(plan,rows)


def finalize_comparison(root):
    root=Path(root)
    if (root/'summary.json').exists() or (root/'manifest.json').exists():raise ValueError('new unfinalized comparison required')
    summary=evaluate_comparison(root);(root/'summary.json').write_bytes(encoded(summary));seal(root);return summary


def verify_comparison(root):
    root=Path(root);identity=verify(root);expected=evaluate_comparison(root)
    if read_json(root/'summary.json')!=expected:raise ValueError('comparison summary differs from raw traces/coverage')
    return dict(verified=True,manifest_sha256=identity,required_comparisons_passed=expected['required_comparisons_passed'],
                required_cases=expected['required_cases'],competitive_superiority_proven=False)
