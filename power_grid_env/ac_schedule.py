"""Explicit bounded Pg schedule preparation, separate from electrical feasibility.

Minimize sum((Pg - original_Pg)**2 / (2*weight)) over declared adjustable
units, preserving the requested total. This geometric projection has no costs,
network equations, loss estimate or claim of an electrically feasible dispatch.
Original cases are never changed in place; equipment limits are never widened.
"""
from dataclasses import replace
from pathlib import Path
import hashlib
import math
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.ac_dispatch import Participant
from power_grid_env.balanced_ac import case_document,case_from_document
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify


class ScheduleOptions(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    participants:list[Participant]=Field(min_length=1,max_length=4096)
    target_total_mw:float|None=None
    tolerance_mw:float=Field(default=1e-8,gt=0,le=1e-6)

    @model_validator(mode='after')
    def unique(self):
        if len({p.generator_index for p in self.participants})!=len(self.participants):raise ValueError('duplicate schedule participant')
        return self


def prepare_schedule(case,options):
    """Project explicit schedules; an infeasible total produces no prepared case."""
    case=case_from_document(case_document(case));o=ScheduleOptions.model_validate(options)
    if any(g.pg is None for g in case.generators):raise ValueError('explicit Pg schedules required')
    n=len(case.generators);weights=np.zeros(n)
    for p in o.participants:
        if p.generator_index>=n:raise ValueError('unknown schedule participant')
        weights[p.generator_index]=p.weight
    active=weights>0;weights/=max(weights)
    if np.any(weights[active]==0):raise ValueError('schedule weight dynamic range exhausted')
    original=np.array([g.pg for g in case.generators]);lo=np.array([g.pmin for g in case.generators]);hi=np.array([g.pmax for g in case.generators])
    if any((not active[i]) and not g.pmin<=g.pg<=g.pmax for i,g in enumerate(case.generators)):raise ValueError('nonadjustable Pg schedule outside P bounds')
    total=math.fsum(original);target=total if o.target_total_mw is None else o.target_total_mw
    fixed=math.fsum(original[~active]);lower=math.fsum([fixed,*lo[active]]);upper=math.fsum([fixed,*hi[active]])
    common=dict(schema='power-grid-env.ac-schedule/v1',producer_sha256=package_fingerprint(),input_case_sha256=hashlib.sha256(encoded(case_document(case))).hexdigest(),options=o.model_dump(mode='json'),original_total_mw=total,target_total_mw=target,admissible_total_mw=[lower,upper],electrical_feasibility_proven=False,ac_power_flow_executed=False,economic_dispatch=False,equipment_limits_changed=False)
    if target<lower or target>upper:return dict(common,completed=False,reason='target_outside_aggregate_P_bounds',prepared_case=None,changes=[],projection=None)
    left=float(min((lo[active]-original[active])/weights[active]));right=float(max((hi[active]-original[active])/weights[active]))
    def values(shift):
        result=original.copy();result[active]=np.clip(original[active]+weights[active]*shift,lo[active],hi[active]);return result
    if not math.isfinite(left) or not math.isfinite(right) or not math.isfinite(right-left):raise ValueError('schedule projection numerical range exceeded')
    answer=None;shift=0.;iterations=0
    for iterations in range(201):
        shift=0. if iterations==0 and left<=0<=right else left+(right-left)/2
        q=values(shift);error=math.fsum(q)-target
        if abs(error)<=o.tolerance_mw:answer=q;break
        if error<0:left=shift
        else:right=shift
    if answer is None:raise ValueError('schedule projection precision budget exhausted')
    prepared=replace(case,generators=[replace(g,pg=float(q)) for g,q in zip(case.generators,answer,strict=True)])
    changes=[dict(generator_index=i,bus_id=g.bus,original_pg_mw=g.pg,prepared_pg_mw=float(answer[i]),change_mw=float(answer[i]-g.pg),pmin_mw=g.pmin,pmax_mw=g.pmax,adjustable=bool(active[i]),mode='fixed' if not active[i] else 'lower' if answer[i]==lo[i] else 'upper' if answer[i]==hi[i] else 'interior') for i,g in enumerate(case.generators)]
    return dict(common,completed=True,reason='schedule_prepared',prepared_case=case_document(prepared),changes=changes,projection=dict(policy='weighted Euclidean projection of declared adjustable Pg, normalized weights; no costs or network constraints',normalized_shift_mw=shift,iterations=iterations,total_residual_mw=math.fsum(answer)-target,weighted_distance_mw2=math.fsum((answer[active]-original[active])**2/weights[active])/2))


def run_schedule(document,out):
    if set(document)-{'case','options','provenance'}:raise ValueError('unknown schedule input field')
    result=prepare_schedule(case_from_document(document['case']),document.get('options',{}));out=Path(out);out.mkdir(parents=True,exist_ok=False)
    (out/'input.json').write_bytes(encoded(document));(out/'result.json').write_bytes(encoded(result));seal(out);return result


def verify_schedule(out):
    out=Path(out);identity=verify(out);d=read_json(out/'input.json');r=read_json(out/'result.json')
    if set(d)-{'case','options','provenance'}:raise ValueError('unknown schedule input field')
    expected=prepare_schedule(case_from_document(d['case']),d.get('options',{}))
    if encoded(r)!=encoded(expected):raise ValueError('schedule projection or provenance differs')
    return dict(verified=True,completed=r['completed'],reason=r['reason'],manifest_sha256=identity,electrical_feasibility_proven=False)
