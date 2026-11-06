"""Finite physical scenario search and JSON-only, holdout-gated surrogates."""
from itertools import product
import math
from pathlib import Path
import time
from typing import Literal

import numpy as np
from pydantic import Field, model_validator
from scipy.spatial import Delaunay

from power_grid_env.phase_network import Strict,PhaseCase,run_emt,harmonic_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json
from power_grid_env.study_artifacts import read_json,encoded,digest,seal,verify


class EmtOptions(Strict):
    duration_s: float = Field(gt=0,le=10)
    sample_s: float = Field(gt=0)
    max_step_s: float = Field(gt=0)
    rtol: float = Field(default=1e-7,gt=0)
    atol: float = Field(default=1e-9,gt=0)
    method: Literal['Radau','DOP853'] = 'Radau'


class PhaseStudy(Strict):
    schema_version: Literal['power-grid-env.phase-study/v1'] = 'power-grid-env.phase-study/v1'
    id: str = Field(min_length=1)
    case: PhaseCase
    mode: Literal['emt','harmonic']
    orders: list[int] = Field(default_factory=lambda:[1])
    emt: EmtOptions | None = None

    @model_validator(mode='after')
    def valid(self):
        if (self.mode=='emt') != (self.emt is not None):raise ValueError('EMT settings are required only for EMT studies')
        if self.mode=='emt' and self.orders!=[1]:raise ValueError('harmonic order requests are only used by harmonic studies')
        if not self.orders or len(set(self.orders))!=len(self.orders) or any(not 1<=k<=100 for k in self.orders):raise ValueError('invalid harmonic order list')
        return self


def evaluate(study):
    if study.mode=='harmonic':return harmonic_study(study.case,study.orders)
    return run_emt(study.case,**study.emt.model_dump())


def run_phase_study(study,out):
    study=PhaseStudy.model_validate(study);out=Path(out)
    if out.exists():raise ValueError('new phase output directory required')
    identity=package_fingerprint();document=study.model_dump(mode='json');result=evaluate(study)
    if package_fingerprint()!=identity:raise ValueError('phase producer changed during execution')
    result.update(code_sha256=identity,study_sha256=digest(encoded(document)),policy='local_only_no_GitHub_no_CI')
    encoded(result);out.mkdir(parents=True);write_json(out/'study.json',document);write_json(out/'result.json',result);seal(out)
    return result


class Axis(Strict):
    id: str = Field(min_length=1)
    parameter: Literal['resistance_ohm','inductor_resistance_ohm','inductance_scale','source_rms_v','converter_active_w','converter_reactive_var','converter_voltage_v','drive_ramp_s','drive_dc_capacitance_f','drive_motor_voltage_v']
    equipment_id: str
    values: list[float] = Field(min_length=2,max_length=9)

    @model_validator(mode='after')
    def valid(self):
        if len(set(self.values))!=len(self.values):raise ValueError('unique axis values required')
        if self.parameter not in {'converter_active_w','converter_reactive_var'} and any(x<=0 for x in self.values):raise ValueError('positive physical axis values required')
        return self


class Objective(Strict):
    kind: Literal['node_rms_v','node_thd','inductor_rms_a','peak_branch_a','peak_node_v','energy_error_j','converter_active_w','converter_frequency_hz','drive_speed_rad_s','drive_min_dc_v','drive_peak_grid_a']
    equipment_id: str = ''
    order: int = Field(default=1,ge=1,le=100)
    sense: Literal['min','max'] = 'max'
    threshold: float | None = None


class SearchProtocol(Strict):
    schema_version: Literal['power-grid-env.phase-search/v1'] = 'power-grid-env.phase-search/v1'
    id: str = Field(min_length=1)
    study: PhaseStudy
    axes: list[Axis] = Field(min_length=1,max_length=3)
    objective: Objective

    @model_validator(mode='after')
    def valid(self):
        if len({a.id for a in self.axes})!=len(self.axes) or len({(a.parameter,a.equipment_id) for a in self.axes})!=len(self.axes):raise ValueError('unique axis IDs and physical targets required')
        if math.prod(len(a.values) for a in self.axes)>729:raise ValueError('finite search grid budget exceeded')
        for axis in self.axes:_target(self.study.case,axis)
        o=self.objective
        if self.study.mode=='harmonic':
            if o.kind not in {'node_rms_v','node_thd','inductor_rms_a'}:raise ValueError('harmonic search needs a harmonic objective')
            if o.order not in self.study.orders:raise ValueError('objective harmonic was not requested')
        elif o.kind in {'node_rms_v','node_thd','inductor_rms_a'}:raise ValueError('EMT search needs a sampled time-domain objective')
        if o.kind.startswith('node_') and o.equipment_id not in self.study.case.nodes:raise ValueError('unknown objective node')
        if o.kind=='inductor_rms_a' and o.equipment_id not in {i.id for i in self.study.case.inductors}:raise ValueError('unknown objective inductor')
        if o.kind.startswith('converter_') and o.equipment_id not in {c.id for c in self.study.case.converters}:raise ValueError('unknown objective converter')
        if o.kind.startswith('drive_') and o.equipment_id not in {d.id for d in self.study.case.drives}:raise ValueError('unknown objective drive')
        return self


def _target(case,axis):
    if axis.parameter=='inductance_scale':
        if axis.equipment_id!='all' or not case.inductors:raise ValueError('inductance scale target is all inductors')
        return None,'inductance_h'
    collection,key={
        'resistance_ohm':('resistors','resistance_ohm'),'inductor_resistance_ohm':('inductors','resistance_ohm'),
        'source_rms_v':('sources','rms'),'converter_active_w':('converters','active_ref_w'),
        'converter_reactive_var':('converters','reactive_ref_var'),'converter_voltage_v':('converters','nominal_ln_rms_v'),
        'drive_ramp_s':('drives','ramp_s'),'drive_dc_capacitance_f':('drives','dc_capacitance_f'),'drive_motor_voltage_v':('drives','motor_ln_rms_v'),
    }[axis.parameter]
    target=next((x for x in getattr(case,collection) if x.id==axis.equipment_id),None)
    if target is None:raise ValueError('unknown scenario axis target')
    if axis.parameter=='source_rms_v' and not any(t.order==1 for t in target.tones):raise ValueError('source axis needs an explicit fundamental tone')
    return target,key


def candidate(protocol,values):
    study=protocol.study.model_copy(deep=True)
    for axis,value in zip(protocol.axes,values,strict=True):
        target,key=_target(study.case,axis)
        if key=='inductance_h':study.case.inductance_h=(np.array(study.case.inductance_h)*value).tolist()
        elif key=='rms':next(t for t in target.tones if t.order==1).rms=value
        else:setattr(target,key,value)
    return PhaseStudy.model_validate(study.model_dump())


def objective_value(report,objective):
    if objective.kind in {'node_rms_v','inductor_rms_a'}:
        frame=next(f for f in report['frames'] if f['order']==objective.order)
        values=frame['voltage'] if objective.kind=='node_rms_v' else frame['inductor_current']
        value=abs(complex(*values[objective.equipment_id]))
    elif objective.kind=='node_thd':value=report['voltage_thd'][objective.equipment_id]
    elif objective.kind.startswith('drive_'):
        key={'drive_speed_rad_s':'motor_speed_rad_s','drive_min_dc_v':'dc_voltage_v','drive_peak_grid_a':'current_vector_peak_a'}[objective.kind]
        data=report['channels'][objective.equipment_id+'.'+key]
        value=data[-1] if objective.kind=='drive_speed_rad_s' else min(data) if objective.kind=='drive_min_dc_v' else max(data)
    elif objective.kind.startswith('converter_'):
        suffix='p_w' if objective.kind=='converter_active_w' else 'frequency_hz'
        value=report['channels'][objective.equipment_id+'.'+suffix][-1]
    else:
        key={'peak_branch_a':'max_branch_current_a','peak_node_v':'max_node_voltage_v','energy_error_j':'final_energy_error_j'}[objective.kind]
        value=report['metrics'][key]
        if objective.kind=='energy_error_j':value=abs(value)
    if value is None or not math.isfinite(value):raise ValueError('objective is unavailable/nonfinite')
    return float(value)


def run_search(protocol,out,*,max_points=None,resume_from=None,cancel_file=None):
    protocol=SearchProtocol.model_validate(protocol);document=protocol.model_dump(mode='json');out=Path(out)
    if out.exists():raise ValueError('new search attempt directory required')
    if max_points is not None and (type(max_points) is not int or max_points<1):raise ValueError('positive point budget required')
    identity=package_fingerprint();study_hash=digest(encoded(document));points=list(product(*(a.values for a in protocol.axes)))
    previous=[]
    if resume_from is not None:
        parent=Path(resume_from)
        if out.resolve().is_relative_to(parent.resolve()) or parent.resolve().is_relative_to(out.resolve()):raise ValueError('search attempts must be disjoint')
        verify(parent);old=read_json(parent/'result.json')
        if old['code_sha256']!=identity or old['protocol_sha256']!=study_hash:raise ValueError('resume protocol or producer differs')
        if old['status'] not in {'paused','cancelled'}:raise ValueError('parent search is not resumable')
        previous=read_json(parent/'points.json')
        if len(previous)!=old['evaluated_points'] or any(r['index']!=i or r['values']!=list(points[i]) for i,r in enumerate(previous)):raise ValueError('search prefix differs')
    out.mkdir(parents=True);write_json(out/'protocol.json',document);rows=list(previous);start=time.perf_counter();status='completed'
    for i,values in enumerate(points[len(previous):],start=len(previous)):
        if cancel_file is not None and Path(cancel_file).exists():status='cancelled';break
        if max_points is not None and i-len(previous)>=max_points:status='paused';break
        try:
            spec=candidate(protocol,values);result=evaluate(spec);value=objective_value(result,protocol.objective)
            row=dict(index=i,values=list(values),completed=bool(result['completed']),objective=value,result_sha256=digest(encoded(result)))
        except (ValueError,RuntimeError,np.linalg.LinAlgError) as exc:
            row=dict(index=i,values=list(values),completed=False,objective=None,error=str(exc))
        rows.append(row);write_json(out/'points.json',rows)
    valid=[r for r in rows if r['completed']]
    best=(max if protocol.objective.sense=='max' else min)(valid,key=lambda r:(r['objective'],-r['index'])) if valid else None
    if best is not None:
        chosen=candidate(protocol,best['values']);physical=evaluate(chosen)
        if abs(objective_value(physical,protocol.objective)-best['objective'])>1e-8*(1+abs(best['objective'])):raise ValueError('critical physical replay differs')
        write_json(out/'critical-study.json',chosen.model_dump(mode='json'));write_json(out/'critical-result.json',physical)
    if package_fingerprint()!=identity:raise ValueError('search producer changed')
    write_json(out/'points.json',rows)
    complete=len(rows)==len(points);threshold=protocol.objective.threshold
    exceeded=(best['objective']>=threshold if protocol.objective.sense=='max' else best['objective']<=threshold) if best is not None and threshold is not None else None
    result=dict(completed=complete,status=status,conclusive=complete and len(valid)==len(rows),evaluated_points=len(rows),total_points=len(points),failed_points=len(rows)-len(valid),
                worst_case=best,threshold_exceeded=exceeded,critical_case_physically_replayed=best is not None,
                elapsed_s=time.perf_counter()-start,code_sha256=identity,protocol_sha256=study_hash,policy='local_only_no_GitHub_no_CI',
                global_optimum_claimed=False,unseen_safe_claimed=False,scope='exhaustive declared finite grid, not continuous global optimization')
    write_json(out/'result.json',result);seal(out);return result


def _features(x,degree):
    x=np.asarray(x,dtype=float);columns=[np.ones(len(x))]
    columns.extend(x[:,i] for i in range(x.shape[1]))
    if degree==2:
        columns.extend(x[:,i]**2 for i in range(x.shape[1]))
        columns.extend(x[:,i]*x[:,j] for i in range(x.shape[1]) for j in range(i+1,x.shape[1]))
    return np.column_stack(columns)


def fit_surrogate(points,values,*,maximum_holdout_error,degree=2,seed=41):
    x=np.asarray(points,dtype=float);y=np.asarray(values,dtype=float)
    if x.ndim!=2 or not 1<=x.shape[1]<=3 or y.shape!=(len(x),) or not np.isfinite(x).all() or not np.isfinite(y).all():raise ValueError('finite scalar observations and 1..3 dimensional coordinates required')
    if len(np.unique(x,axis=0))!=len(x):raise ValueError('independent holdout requires unique physical coordinates')
    if degree not in (1,2) or not math.isfinite(maximum_holdout_error) or maximum_holdout_error<=0 or type(seed) is not int or seed<0:raise ValueError('invalid surrogate degree, seed or declared error gate')
    order=np.random.default_rng(seed).permutation(len(x));nhold=max(2,len(x)//4);hold=order[:nhold];train=order[nhold:]
    if len(train)<_features(np.zeros((1,x.shape[1])),degree).shape[1]+1:raise ValueError('insufficient independent training/holdout points')
    low=x[train].min(axis=0);high=x[train].max(axis=0);scale=high-low
    if np.any(scale<=0):raise ValueError('degenerate training domain')
    norm=(x-low)/scale;design=_features(norm[train],degree)
    offset=float(np.mean(y[train]));coeff,residual,rank,s=np.linalg.lstsq(design,y[train]-offset,rcond=None)
    if rank!=design.shape[1] or s[0]/s[-1]>1e10:raise ValueError('rank-deficient or ill-conditioned surrogate design')
    errors=_features(norm[hold],degree)@coeff+offset-y[hold]
    max_error=float(np.max(abs(errors)))
    return dict(schema='power-grid-env.empirical-surrogate/v1',code_sha256=package_fingerprint(),degree=degree,seed=seed,
                coefficients=coeff.tolist(),output_offset=offset,lower=low.tolist(),scale=scale.tolist(),
                training_points=x[train].tolist(),training_indices=train.tolist(),holdout_indices=hold.tolist(),
                holdout_max_absolute_error=max_error,holdout_rmse=float(np.sqrt(np.mean(errors**2))),maximum_holdout_error=maximum_holdout_error,
                accepted=max_error<=maximum_holdout_error,condition_number=float(s[0]/s[-1]),
                error_bound_is_global=False,requires_physical_verification=True,validation='fixed independent holdout; empirical error is not a certified bound')


def predict_surrogate(model,point):
    if model.get('schema')!='power-grid-env.empirical-surrogate/v1' or model.get('code_sha256')!=package_fingerprint() or not model.get('accepted'):
        raise ValueError('surrogate schema/producer/error gate is not accepted')
    if model.get('degree') not in (1,2) or not isinstance(model.get('lower'),list) or not 1<=len(model['lower'])<=3 or not isinstance(model.get('training_points'),list) or not 1<=len(model['training_points'])<=729:
        raise ValueError('invalid bounded surrogate structure')
    point=np.asarray(point,dtype=float);low=np.array(model['lower']);scale=np.array(model['scale']);train=np.array(model['training_points'])
    dimension=len(low);coeff=np.array(model['coefficients'],dtype=float)
    width=_features(np.zeros((1,dimension)),model['degree']).shape[1]
    if scale.shape!=low.shape or train.shape!=(len(train),dimension) or coeff.shape!=(width,) or not all(np.isfinite(a).all() for a in [low,scale,train,coeff]) or np.any(scale<=0) or not math.isfinite(model['output_offset']):
        raise ValueError('invalid surrogate shape or nonfinite coefficient')
    if point.shape!=low.shape or not np.isfinite(point).all():raise ValueError('invalid surrogate coordinate')
    norm=(point-low)/scale
    inside=bool(np.all(norm>=-1e-12) and np.all(norm<=1+1e-12))
    if inside and len(point)>1:inside=Delaunay((train-low)/scale).find_simplex(norm,tol=1e-12)>=0
    if not inside:raise ValueError('outside the training convex hull; physical solve required')
    predicted=float((_features(norm[None,:],model['degree'])@coeff).item()+model['output_offset'])
    return dict(prediction=predicted,observed_holdout_max_absolute_error=model['holdout_max_absolute_error'],
                empirical_error_is_guaranteed=False,requires_physical_verification=True)


def train_from_search(directory,out,*,maximum_holdout_error,degree=2,seed=41):
    directory=Path(directory);verify(directory);result=read_json(directory/'result.json')
    if not result['completed'] or not result['conclusive']:raise ValueError('complete conclusive physical grid required for surrogate training')
    rows=read_json(directory/'points.json');model=fit_surrogate([r['values'] for r in rows],[r['objective'] for r in rows],maximum_holdout_error=maximum_holdout_error,degree=degree,seed=seed)
    model.update(protocol_sha256=result['protocol_sha256'],physical_producer_sha256=result['code_sha256'])
    if model['physical_producer_sha256']!=model['code_sha256']:raise ValueError('training observations belong to another physical producer')
    out=Path(out)
    if out.exists():raise ValueError('new surrogate output required')
    out.mkdir(parents=True);write_json(out/'model.json',model);write_json(out/'protocol.json',read_json(directory/'protocol.json'));seal(out);return model


def query_model(directory,point,out,*,screen_only=False):
    directory=Path(directory);verify(directory);model=read_json(directory/'model.json')
    prediction=predict_surrogate(model,point)
    protocol=SearchProtocol.model_validate(read_json(directory/'protocol.json'))
    if digest(encoded(protocol.model_dump(mode='json')))!=model['protocol_sha256']:raise ValueError('model protocol identity differs')
    result=dict(completed=True,prediction=prediction['prediction'],physically_verified=False,accepted=False,
                requires_physical_verification=True,observed_holdout_max_absolute_error=model['holdout_max_absolute_error'])
    report=None
    if not screen_only:
        study=candidate(protocol,point);report=evaluate(study);actual=objective_value(report,protocol.objective)
        error=abs(actual-prediction['prediction'])
        result.update(physically_verified=True,requires_physical_verification=False,physical_value=actual,
                      absolute_error=error,accepted=error<=model['maximum_holdout_error'])
    result.update(code_sha256=package_fingerprint(),empirical_error_is_guaranteed=False)
    out=Path(out)
    if out.exists():raise ValueError('new prediction output required')
    out.mkdir(parents=True);write_json(out/'query.json',dict(point=list(point),screen_only=screen_only))
    if report is not None:write_json(out/'physical-result.json',report)
    write_json(out/'result.json',result);seal(out);return result


def predict_surrogate_batch(model,points):
    """Producer-bound vectorized screening; every result still needs physics."""
    points=np.asarray(points,dtype=float)
    if points.ndim!=2 or not 1<=len(points)<=100_000 or not np.isfinite(points).all():raise ValueError('finite bounded coordinate batch required')
    predict_surrogate(model,points[0])  # Full structure, identity and acceptance validation.
    low=np.array(model['lower']);scale=np.array(model['scale']);train=np.array(model['training_points'])
    if points.shape[1]!=len(low):raise ValueError('batch dimension differs')
    norm=(points-low)/scale
    inside=np.all((norm>=-1e-12)&(norm<=1+1e-12),axis=1)
    if len(low)>1:inside &= Delaunay((train-low)/scale).find_simplex(norm,tol=1e-12)>=0
    if not np.all(inside):raise ValueError('batch includes points outside the training convex hull')
    values=_features(norm,model['degree'])@np.array(model['coefficients'])+model['output_offset']
    if not np.isfinite(values).all() or model['code_sha256']!=package_fingerprint():raise ValueError('nonfinite prediction or producer drift')
    return dict(predictions=values.tolist(),requires_physical_verification=True,
                observed_holdout_max_absolute_error=model['holdout_max_absolute_error'],empirical_error_is_guaranteed=False)
