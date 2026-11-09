"""Finite parameter-design engineering checks, never probabilistic certification."""
import itertools
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from power_grid_env.industrial_dol import IndustrialDOLCase
from power_grid_env.motor_engineering import MotorEngineeringPlan, MotorAuditLimits, run_engineering, verify_engineering
from power_grid_env.motor_sensitivity import ParameterAxis, _apply, _target
from power_grid_env.study_artifacts import encoded, read_json, seal, verify
from power_grid_env.evidence import package_fingerprint


class MotorRobustnessPlan(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    schema_version:Literal['power-grid-env.motor-robustness/v1']='power-grid-env.motor-robustness/v1'
    id:str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    base:IndustrialDOLCase
    design:Literal['one_at_a_time','cartesian']='cartesian'
    axes:list[ParameterAxis]=Field(min_length=1,max_length=4)
    bounds_provenance:str=Field(min_length=1,max_length=4096)
    limits:MotorAuditLimits=Field(default_factory=MotorAuditLimits)
    per_run_timeout_s:float=Field(default=30,gt=0,le=300)
    total_timeout_s:float=Field(default=600,gt=0,le=3600)

    @model_validator(mode='after')
    def bounded(self):
        if not self.bounds_provenance.strip():raise ValueError('explicit parameter-bound provenance required')
        if self.base.expectation!='within_limits':raise ValueError('robustness criteria must express a desired operating regime')
        if len({a.id for a in self.axes})!=len(self.axes) or len({(a.equipment_id,a.parameter) for a in self.axes})!=len(self.axes):
            raise ValueError('unique axes and targets required')
        count=1+sum(len(a.factors)-1 for a in self.axes) if self.design=='one_at_a_time' else math.prod(len(a.factors) for a in self.axes)
        if count>8:raise ValueError('at most eight fully audited parameter points')
        for axis in self.axes:_target(self.base.study,axis)
        return self


def expand_plan(document):
    plan=MotorRobustnessPlan.model_validate(document)
    nominal=tuple(1. for _ in plan.axes)
    if plan.design=='cartesian':
        factors=[nominal,*[v for v in itertools.product(*(a.factors for a in plan.axes)) if v!=nominal]]
    else:
        factors=[nominal]
        for i,axis in enumerate(plan.axes):
            for value in axis.factors:
                if value!=1.:
                    row=list(nominal);row[i]=value;factors.append(tuple(row))
    cases=[];points=[]
    for i,values in enumerate(factors):
        case=plan.base.model_copy(deep=True);case.id=f'point-{i:03d}'
        case.study.id=plan.base.study.id+'::'+case.id
        for axis,value in zip(plan.axes,values,strict=True):_apply(case.study,axis,value)
        case.parameter_provenance+='\nFinite design bounds: '+plan.bounds_provenance
        case=IndustrialDOLCase.model_validate(case.model_dump(mode='json'))
        cases.append(case);points.append(dict(id=case.id,nominal=i==0,factors=dict(zip((a.id for a in plan.axes),values))))
    study=MotorEngineeringPlan(id=plan.id,purpose='engineering',cases=cases,limits=plan.limits,
        per_run_timeout_s=plan.per_run_timeout_s,total_timeout_s=plan.total_timeout_s)
    return plan,study,points


def summarize(plan,points,study):
    rows=study.get('cases',[])
    if [r.get('id') for r in rows]!=[p['id'] for p in points]:raise ValueError('full ordered parameter-point coverage required')
    qualified=bool(study.get('completed') and all(r.get('numerically_qualified') for r in rows))
    failures=[r['id'] for r in rows if r.get('numerically_qualified') and not r.get('engineering_acceptable')]
    status='inconclusive' if not qualified else 'violations_found' if failures else 'all_sampled_points_within_limits'
    return dict(schema='power-grid-env.motor-robustness-result/v1',id=plan.id,producer_sha256=study['producer_sha256'],
        status=status,all_points_qualified=qualified,all_sampled_points_within_limits=qualified and not failures,
        violating_point_ids=failures,unqualified_point_ids=[r['id'] for r in rows if not r.get('numerically_qualified')],
        nominal_within_limits=bool(rows[0].get('engineering_acceptable')) if rows[0].get('numerically_qualified') else None,
        points=[dict(**p,numerically_qualified=bool(r.get('numerically_qualified')),engineering_acceptable=bool(r.get('engineering_acceptable'))) for p,r in zip(points,rows,strict=True)],
        design=plan.design,bounds_provenance=plan.bounds_provenance,
        continuous_range_proven=False,probability_of_success=None,field_validated=False,
        scope='finite deterministic parameter design; each point independently initialized and audited on three grids; no interpolation between points')


def run_robustness(document,out,*,cancelled=None):
    plan,study,points=expand_plan(document);out=Path(out)
    out.mkdir(parents=True,exist_ok=False)
    (out/'input.json').write_bytes(encoded(plan.model_dump(mode='json')))
    result=run_engineering(study.model_dump(mode='json'),out/'engineering',cancelled=cancelled)
    summary=summarize(plan,points,result)
    (out/'summary.json').write_bytes(encoded(summary));seal(out)
    return summary


def verify_robustness(root):
    root=Path(root);identity=verify(root)
    plan,study,points=expand_plan(read_json(root/'input.json'))
    if read_json(root/'engineering/input.json')!=study.model_dump(mode='json'):raise ValueError('parameter study differs from declared design')
    verify_engineering(root/'engineering')
    summary=summarize(plan,points,read_json(root/'engineering/summary.json'))
    if summary['producer_sha256']!=package_fingerprint() or summary!=read_json(root/'summary.json'):
        raise ValueError('robustness summary differs from recomputed evidence')
    return dict(verified=True,manifest_sha256=identity,**summary)
