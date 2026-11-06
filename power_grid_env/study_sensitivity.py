"""Audited numerical derivatives of complete RMS hybrid studies.

Every peer repeats schema validation, consistent initialization, controls, timers
and topology transitions. This numerical method does not claim analytic reset
or adjoint derivatives. Event-order changes, one-sided corners and unresolved
integration noise produce an unavailable derivative, never a zero substitute.
"""
import copy,math,time
from typing import Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint


class ParameterDirection(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    path:str
    step:float=Field(gt=0)
    unit:str=Field(min_length=1)


class SensitivityRequest(BaseModel):
    schema_version:Literal['power-grid-env.sensitivity-request/v1']='power-grid-env.sensitivity-request/v1'
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    parameters:list[ParameterDirection]=Field(min_length=1,max_length=8)
    outputs:list[str]=Field(min_length=1,max_length=256)
    derivative_rtol:float=Field(default=.005,gt=0,le=.1)
    derivative_atol:float=Field(default=1e-6,gt=0)
    maximum_wall_s:float=Field(default=600.,gt=0,le=3600.)
    worker_wall_s:float=Field(default=120.,gt=0,le=600.)
    isolate:bool=True

    @model_validator(mode='after')
    def unique(self):
        if len({p.path for p in self.parameters})!=len(self.parameters) or len(set(self.outputs))!=len(self.outputs):raise ValueError('unique input/output paths required')
        for path in [*[p.path for p in self.parameters],*self.outputs]:_tokens(path)
        return self


def _tokens(path):
    if not isinstance(path,str) or not path.startswith('/') or path=='/':raise ValueError('nonempty absolute JSON pointer required')
    tokens=path[1:].split('/')
    for token in tokens:
        if '~' in token.replace('~0','').replace('~1',''):raise ValueError('invalid JSON pointer escape')
    return [token.replace('~1','/').replace('~0','~') for token in tokens]


def _locate(document,path):
    tokens=_tokens(path);node=document
    for token in tokens[:-1]:node=node[_index(node,token)]
    return node,_index(node,tokens[-1])


def _index(node,token):
    if isinstance(node,list):
        if not token.isascii() or not token.isdigit() or (len(token)>1 and token[0]=='0'):raise ValueError('canonical nonnegative array index required')
        index=int(token)
        if index>=len(node):raise ValueError('array index outside document')
        return index
    if not isinstance(node,dict) or token not in node:raise ValueError('JSON pointer does not exist: '+token)
    return token


def _number(document,path):
    node,key=_locate(document,path);value=node[key]
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('finite numeric leaf required: '+path)
    return float(value)


def _signature(report):
    keys=('kind','equipment_id','outcome','cause','controller','bound','reason','port','transition','gate','active')
    return [tuple((k,e[k]) for k in keys if k in e and isinstance(e[k],(str,int,float,bool,type(None)))) for e in report.get('event_log',[])]


def _view(report):
    channels=report.get('channels',{})
    return dict(final={k:v[-1] for k,v in channels.items() if v},initial={k:v[0] for k,v in channels.items() if v},
                event_times=[e['time_s'] for e in report.get('event_log',[])],operating_point=report.get('operating_point',{}))


def differentiate_study(spec,request):
    """Three central steps, Richardson convergence and one-sided corner audit.

    Output JSON pointers address /final/<channel>, /initial/<channel>,
    /event_times/<index> or /operating_point/... . Units are output-unit per
    explicitly declared parameter-unit. A local finite-difference certificate
    is numerical evidence; it cannot prove global smoothness.
    """
    request=SensitivityRequest.model_validate(request) if isinstance(request,dict) else request
    spec=StudySpec.model_validate(spec.model_dump(mode='json'))
    if spec.study not in {'motor_start','coupled_rms'} or spec.backend!='scipy':raise ValueError('RMS induction/coupled study required')
    started=time.monotonic();code=package_fingerprint();document=spec.model_dump(mode='json');rows=[];runs=[]
    def solve(data):
        remaining=request.maximum_wall_s-(time.monotonic()-started)
        if remaining<=0:raise TimeoutError('sensitivity study wall budget exceeded')
        peer=StudySpec.model_validate(data)
        r=run_study(peer,isolate=request.isolate,wall_timeout_s=min(remaining,request.worker_wall_s) if request.isolate else None)
        runs.append(dict(study_sha256=canonical_hash(peer),completed=r.get('completed',False),status=r.get('status')))
        if time.monotonic()-started>=request.maximum_wall_s:raise TimeoutError('sensitivity study wall budget exceeded')
        if not r.get('completed'):raise ValueError('complete study failed: '+str(r.get('reason',r.get('status'))))
        if r.get('code_sha256')!=code:raise ValueError('sensitivity producer mismatch')
        runs[-1]['selected_outputs']=[_number(_view(r),path) for path in request.outputs]
        runs[-1]['event_sequence']=_signature(r);runs[-1]['event_times_s']=[e['time_s'] for e in r.get('event_log',[])]
        return r
    # Invalid paths and nonphysical integer/discrete inputs are rejected before
    # spending any solver work. Time settings and capacities are not parameters.
    forbidden={'duration_s','step_s','rtol','atol','integration_method','event_policy','capacity_profile','runtime_limits','dae_options'}
    for p in request.parameters:
        if any(t in forbidden for t in _tokens(p.path)):raise ValueError('solver policy is not a physical parameter')
        parent,key=_locate(document,p.path)
        if isinstance(parent[key],int):raise ValueError('integer/discrete parameters are not differentiable')
        _number(document,p.path)
    try:
        baseline=solve(document);base=np.array([_number(_view(baseline),p) for p in request.outputs]);signature=_signature(baseline)
        for parameter in request.parameters:
            value=_number(document,parameter.path);central=[];left=[];right=[];steps=[]
            for h in (parameter.step,parameter.step/2,parameter.step/4):
                if value+h==value or value-h==value:raise ValueError('parameter step is below floating-point resolution')
                outputs=[]
                for sign in (1,-1):
                    data=copy.deepcopy(document);node,key=_locate(data,parameter.path);node[key]=value+sign*h
                    peer=solve(data)
                    if _signature(peer)!=signature:raise ValueError('event_sequence_changed: '+parameter.path)
                    outputs.append(np.array([_number(_view(peer),p) for p in request.outputs]))
                fp,fm=outputs;central.append((fp-fm)/(2*h));right.append((fp-base)/h);left.append((base-fm)/h);steps.append(h)
            d0,d1,d2=central;richardson=d2+(d2-d1)/3
            # Last two extrapolants must agree, and independent left/right
            # limits must approach one another. Persistent |x| corners fail.
            previous=d1+(d1-d0)/3;error=np.maximum(abs(richardson-previous),abs(d2-d1)/3)
            tolerance=request.derivative_atol+request.derivative_rtol*abs(richardson)
            gap0=abs(right[0]-left[0]);gap2=abs(right[-1]-left[-1])
            corner=(gap2>8*tolerance)&(gap2>=.7*gap0)
            ok=np.isfinite(richardson).all() and np.all(error<=tolerance) and not np.any(corner)
            rows.append(dict(parameter=parameter.path,parameter_unit=parameter.unit,value=value,steps=steps,
                available=bool(ok),reason='one_sided_corner_or_grazing' if np.any(corner) else None if ok else 'derivative_not_resolved',
                derivative=richardson.tolist() if ok else None,absolute_error_estimate=error.tolist(),
                one_sided_gap=gap2.tolist(),central_estimates=[d.tolist() for d in central]))
        available=all(r['available'] for r in rows)
        return dict(available=available,completed=True,method='full_study_central_Richardson_with_one_sided_and_event_audit',
            outputs=request.outputs,baseline_outputs=base.tolist(),parameters=rows,runs=runs,code_sha256=code,
            study_sha256=canonical_hash(spec),request=request.model_dump(mode='json'),event_sequence=signature,
            includes_consistent_initialization=True,includes_timer_and_reset_actions=True,analytic_reset_derivatives=False,
            local_numerical_evidence=True,elapsed_s=time.monotonic()-started)
    except (ValueError,TimeoutError,ArithmeticError) as exc:
        return dict(available=False,completed=False,reason=str(exc),parameters=rows,runs=runs,code_sha256=code,
                    study_sha256=canonical_hash(spec),request=request.model_dump(mode='json'))
