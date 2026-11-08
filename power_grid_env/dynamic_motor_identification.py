"""Time-domain one-cage Gamma identification with independent held-out replay.

Electrical parameters are fitted conditional on measured speed; inertia and one
load coefficient use integral mechanics. Acceptance additionally requires a free
electromechanical replay, not merely a conditional electrical fit. No statistical
confidence or field qualification is inferred from synthetic data.
"""
from typing import Literal
import hashlib,math,time
import numpy as np
from scipy.integrate import solve_ivp,cumulative_trapezoid
from scipy.optimize import least_squares,lsq_linear
from scipy.interpolate import CubicSpline
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.flux_machine import FluxMachinePassport
from power_grid_env.study_artifacts import encoded
from power_grid_env.evidence import package_fingerprint


class DynamicMotorTrace(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    id:str=Field(min_length=1,max_length=64)
    provenance:str=Field(min_length=1,max_length=2048)
    frequency_hz:float=Field(gt=0,le=400)
    time_s:list[float]=Field(min_length=33,max_length=20001)
    voltage_alpha_v:list[float]
    voltage_beta_v:list[float]
    current_alpha_a:list[float]
    current_beta_a:list[float]
    speed_rad_s:list[float]
    initial_stator_flux_vs:tuple[float,float]=(0.,0.)
    initial_rotor_flux_vs:tuple[float,float]=(0.,0.)
    current_sigma_a:float=Field(default=.01,gt=0,le=1000)
    speed_sigma_rad_s:float=Field(default=.02,gt=0,le=1000)

    @model_validator(mode='after')
    def aligned(self):
        arrays=[self.time_s,self.voltage_alpha_v,self.voltage_beta_v,self.current_alpha_a,self.current_beta_a,self.speed_rad_s]
        if any(len(a)!=len(self.time_s) or not np.isfinite(a).all() for a in arrays):raise ValueError('finite aligned trace channels required')
        if self.time_s[0]!=0 or np.any(np.diff(self.time_s)<=0) or self.time_s[-1]>2.:raise ValueError('trace must increase from zero over at most two seconds')
        if max(np.diff(self.time_s))*self.frequency_hz>.1:raise ValueError('at least ten input samples per electrical period required')
        if max(abs(np.array(self.voltage_alpha_v)+1j*np.array(self.voltage_beta_v)))>1e6 or max(abs(np.array(self.current_alpha_a)+1j*np.array(self.current_beta_a)))>1e6 or max(abs(np.array(self.speed_rad_s)))>2000 or max(abs(complex(*v)) for v in [self.initial_stator_flux_vs,self.initial_rotor_flux_vs])>100:raise ValueError('trace outside supported SI amplitude range')
        if not self.provenance.strip():raise ValueError('nonblank trace provenance required')
        return self


ELECTRICAL=('stator_r_ohm','unsaturated_h','rotor_r_ohm','rotor_leakage_h','saturation_beta_per_vs')


class DynamicMotorIdentification(BaseModel):
    schema_version:Literal['power-grid-env.dynamic-motor-identification/v1']='power-grid-env.dynamic-motor-identification/v1'
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    coordinates:Literal['balanced_peak_alpha_beta_si']='balanced_peak_alpha_beta_si'
    initial:FluxMachinePassport
    electrical_parameters:list[Literal['stator_r_ohm','unsaturated_h','rotor_r_ohm','rotor_leakage_h','saturation_beta_per_vs']]=Field(default_factory=lambda:list(ELECTRICAL[:4]),min_length=1,max_length=5)
    mechanical_load:Literal['fan','viscous','fixed']='fan'
    fitting:list[DynamicMotorTrace]=Field(min_length=1,max_length=4)
    validation:list[DynamicMotorTrace]=Field(min_length=1,max_length=4)
    maximum_evaluations:int=Field(default=500,ge=1,le=5000)
    maximum_rhs_evaluations:int=Field(default=2000000,ge=1,le=10000000)
    maximum_wall_s:float=Field(default=120.,gt=0,le=600.)
    maximum_condition:float=Field(default=1e6,gt=1,le=1e10)
    tolerance_sigma:float=Field(default=5.,gt=0,le=100)
    replay_method:Literal['dop853','sample_rk4']='dop853'

    @model_validator(mode='after')
    def applicable(self):
        p=self.initial
        if len(p.cages)!=1 or p.thermal is not None or p.core_loss_s:raise ValueError('dynamic identification profile requires one cage, fixed temperature and zero core-loss conductance')
        if len(set(self.electrical_parameters))!=len(self.electrical_parameters):raise ValueError('unique fitted parameters required')
        if 'saturation_beta_per_vs' in self.electrical_parameters and not p.saturation_beta_per_vs:raise ValueError('positive saturation seed required when fitting saturation')
        if p.locked_rotor and self.mechanical_load!='fixed':raise ValueError('locked rotor has no mechanical parameter identifiability')
        if self.mechanical_load=='fan' and p.viscous_nm_per_rad_s or self.mechanical_load=='viscous' and p.fan_nm_per_rad_s2:raise ValueError('choose a single load law or fixed known mechanics')
        keys=[_experiment_key(t) for t in self.fitting+self.validation]
        if len(set(keys))!=len(keys):raise ValueError('training and hold-out excitations must be distinct, including renamed/noisy copies')
        if len({t.id for t in self.fitting+self.validation})!=len(keys):raise ValueError('unique trace IDs required')
        if p.locked_rotor and any(any(abs(v)>1e-12 for v in t.speed_rad_s) for t in self.fitting+self.validation):raise ValueError('locked-rotor observations must have zero speed')
        if len(encoded(self.model_dump(mode='json')))>16000000:raise ValueError('identification input byte budget exceeded')
        return self


def _experiment_key(trace):
    return hashlib.sha256(encoded(dict(frequency=trace.frequency_hz,time=trace.time_s,ua=trace.voltage_alpha_v,ub=trace.voltage_beta_v,initial_speed=trace.speed_rad_s[0],ps=trace.initial_stator_flux_vs,pr=trace.initial_rotor_flux_vs))).hexdigest()


def _value(p,name):
    return p.cages[0].resistance_ohm if name=='rotor_r_ohm' else p.cages[0].leakage_h if name=='rotor_leakage_h' else getattr(p,name)


def _passport(seed,names,values):
    p=seed.model_copy(deep=True)
    for name,value in zip(names,values):
        if name=='rotor_r_ohm':p.cages[0].resistance_ohm=float(value)
        elif name=='rotor_leakage_h':p.cages[0].leakage_h=float(value)
        else:setattr(p,name,float(value))
    return FluxMachinePassport.model_validate(p.model_dump(mode='json'))


def _currents(p,ps,pr):
    ir=(pr-ps)/p.cages[0].leakage_h
    current=ps*(1+(p.saturation_beta_per_vs*abs(ps))**p.saturation_exponent)/p.unsaturated_h-ir
    torque=1.5*p.pole_pairs*np.imag(current*np.conj(ps))
    return current,ir,torque


class WorkBudget:
    def __init__(self,spec):self.spec=spec;self.started=time.monotonic();self.rhs_calls=0;self.evaluations=0
    def check(self):
        if time.monotonic()-self.started>self.spec.maximum_wall_s:raise TimeoutError('dynamic identification wall budget exceeded')
    def rhs(self):
        self.check()
        if self.rhs_calls>=self.spec.maximum_rhs_evaluations:raise TimeoutError('dynamic identification RHS budget exceeded')
        self.rhs_calls+=1
    def evaluate(self):
        self.check()
        if self.evaluations>=self.spec.maximum_evaluations:raise TimeoutError('dynamic identification evaluation budget exceeded')
        self.evaluations+=1


def replay(p,trace,budget,*,conditional=False,method=None,accuracy_factor=1.,subdivisions=1):
    times=np.asarray(trace.time_s);omega=2*math.pi*trace.frequency_hz
    rotation=np.exp(-1j*omega*times);voltage=(np.array(trace.voltage_alpha_v)+1j*np.array(trace.voltage_beta_v))*rotation
    speed=np.asarray(trace.speed_rad_s);measured_speed=CubicSpline(times,speed) if conditional else None
    def physical_rhs(t,x,u):
        budget.rhs();ps=x[0]+1j*x[1];pr=x[2]+1j*x[3];w=float(measured_speed(t)) if conditional else x[4]
        current,rotor,torque=_currents(p,ps,pr)
        ds=u-p.stator_r_ohm*current-1j*omega*ps
        dr=-p.cages[0].resistance_ohm*rotor-1j*(omega-p.pole_pairs*w)*pr
        dw=0. if p.locked_rotor or conditional else (torque-p.fan_nm_per_rad_s2*w*abs(w)-p.viscous_nm_per_rad_s*w)/p.inertia_kg_m2
        return [ds.real,ds.imag,dr.real,dr.imag,dw]
    def rhs(t,x):
        u=np.interp(t,times,voltage.real)+1j*np.interp(t,times,voltage.imag)
        return physical_rhs(t,x,u)
    x=[*trace.initial_stator_flux_vs,*trace.initial_rotor_flux_vs,speed[0]]
    method=method or budget.spec.replay_method
    if not 0 < accuracy_factor <= 1:raise ValueError('accuracy factor must tighten existing tolerances')
    stats=None
    if method=='sample_rk4':
        from power_grid_env.sampled_integrator import integrate_sampled
        def interval(index):
            left=times[index];delta=times[index+1]-left;start=voltage[index];change=voltage[index+1]-start
            return lambda t,state:physical_rhs(t,state,start+((t-left)/delta)*change)
        values,stats=integrate_sampled(times,x,interval,rtol=2e-9*accuracy_factor,atol=2e-11*accuracy_factor,subdivisions=subdivisions)
    elif method=='dop853':
        solution=solve_ivp(rhs,(0.,times[-1]),x,t_eval=times,method='DOP853',rtol=2e-9*accuracy_factor,atol=2e-11*accuracy_factor,max_step=min(.002,1/(20*trace.frequency_hz)))
        if not solution.success:raise ValueError('complete finite dynamic replay required')
        values=solution.y
    else:raise ValueError('supported replay method required')
    if values.shape!=(5,len(times)) or not np.isfinite(values).all():raise ValueError('complete finite dynamic replay required')
    ps=values[0]+1j*values[1];pr=values[2]+1j*values[3];current,_,torque=_currents(p,ps,pr)
    return dict(current=current/rotation,speed=speed if conditional else values[4],torque=torque,stator_flux=ps/rotation,rotor_flux=pr/rotation,integration=stats)


def _rank(matrix,maximum_condition):
    singular=np.linalg.svd(matrix,compute_uv=False)
    rank=int(np.count_nonzero(singular>singular[0]*1e-10)) if len(singular) and singular[0]>0 else 0
    condition=float(singular[0]/singular[-1]) if len(singular) and singular[-1]>0 else None
    return dict(rank=rank,columns=matrix.shape[1],condition=condition,singular_values=singular.tolist(),identifiable=bool(rank==matrix.shape[1] and condition is not None and condition<=maximum_condition))


def _mechanics(p,traces,budget,law,maximum_condition):
    if law=='fixed':return p,dict(identifiable=True,method='known_fixed_mechanics',estimated=False)
    excitation=max(float(np.ptp(trace.speed_rad_s))/trace.speed_sigma_rad_s for trace in traces)
    if excitation<10.:return p,dict(identifiable=False,method='integral_mechanics',estimated=True,reason='speed_variation_below_resolution',speed_excitation_sigma=excitation,minimum_speed_excitation_sigma=10.)
    matrix=[];target=[]
    for trace in traces:
        response=replay(p,trace,budget,conditional=True);w=np.asarray(trace.speed_rad_s);t=np.asarray(trace.time_s)
        cumulative_torque=cumulative_trapezoid(response['torque'],t,initial=0.)
        load=w*abs(w) if law=='fan' else w
        cumulative_load=cumulative_trapezoid(load,t,initial=0.)
        # Non-overlapping integral windows avoid differentiating speed data.
        bounds=np.unique(np.linspace(0,len(t)-1,min(17,len(t)),dtype=int))
        for a,b in zip(bounds,bounds[1:]):
            matrix.append([w[b]-w[a],cumulative_load[b]-cumulative_load[a]])
            target.append(cumulative_torque[b]-cumulative_torque[a])
    matrix=np.array(matrix);target=np.array(target);scale=np.linalg.norm(matrix,axis=0)
    if np.any(scale<=np.finfo(float).tiny):return p,dict(identifiable=False,method='integral_mechanics',estimated=True,reason='unexcited_mechanics')
    normalized=matrix/scale;diagnostic=_rank(normalized,maximum_condition)
    fitted=lsq_linear(normalized,target,bounds=(0.,np.inf),tol=1e-12)
    values=fitted.x/scale
    if not fitted.success or values[0]<=1e-12:diagnostic['identifiable']=False
    if diagnostic['identifiable']:
        p=p.model_copy(deep=True);p.inertia_kg_m2=float(values[0])
        setattr(p,'fan_nm_per_rad_s2' if law=='fan' else 'viscous_nm_per_rad_s',float(values[1]))
        p=FluxMachinePassport.model_validate(p.model_dump(mode='json'))
    diagnostic.update(method='nonnegative_scaled_integral_mechanics',estimated=True,speed_excitation_sigma=excitation,minimum_speed_excitation_sigma=10.,parameters=['inertia_kg_m2','fan_nm_per_rad_s2' if law=='fan' else 'viscous_nm_per_rad_s'],integral_residual_max_nm_s=float(max(abs(matrix@values-target))))
    return p,diagnostic


def identify_dynamic(spec):
    spec=DynamicMotorIdentification.model_validate(spec);budget=WorkBudget(spec);code=package_fingerprint();input_digest=hashlib.sha256(encoded(spec.model_dump(mode='json'))).hexdigest()
    digest=hashlib.sha256(encoded(dict(coordinates=spec.coordinates,fitting=[t.model_dump(mode='json') for t in spec.fitting],validation=[t.model_dump(mode='json') for t in spec.validation]))).hexdigest()
    names=spec.electrical_parameters;seed=np.array([_value(spec.initial,n) for n in names])
    def residual(log):
        budget.evaluate();p=_passport(spec.initial,names,np.exp(log));rows=[]
        for trace in spec.fitting:
            r=replay(p,trace,budget,conditional=True)
            error=(r['current']-(np.array(trace.current_alpha_a)+1j*np.array(trace.current_beta_a)))/trace.current_sigma_a
            rows.extend(error.real);rows.extend(error.imag)
        return np.asarray(rows)
    try:
        if np.any(seed<=0) or not np.isfinite(seed).all():raise ValueError('positive finite fitted seeds required')
        fitted=least_squares(residual,np.log(seed),bounds=(np.log(seed/4),np.log(seed*4)),jac='3-point',diff_step=1e-4,max_nfev=spec.maximum_evaluations,ftol=1e-10,xtol=1e-10,gtol=1e-9)
        electrical=_rank(fitted.jac,spec.maximum_condition)
        electrical['resolved_at_declared_scale']=bool(electrical['singular_values'] and electrical['singular_values'][-1]>=1.)
        electrical['minimum_weighted_singular_value']=1.
        electrical['identifiable']=electrical['identifiable'] and electrical['resolved_at_declared_scale']
        electrical['at_parameter_bound']=bool(np.any(fitted.active_mask));electrical['conditional_current_error_sigma']=float(np.max(abs(fitted.fun)))
        p=_passport(spec.initial,names,np.exp(fitted.x))
        p,mechanical=_mechanics(p,spec.fitting,budget,spec.mechanical_load,spec.maximum_condition)
        assessments=[];trajectories={}
        for split,traces in [('fitting',spec.fitting),('validation',spec.validation)]:
            for trace in traces:
                r=replay(p,trace,budget,conditional=False)
                wanted=np.array(trace.current_alpha_a)+1j*np.array(trace.current_beta_a)
                current_error=max(float(np.max(abs(r['current'].real-wanted.real))),float(np.max(abs(r['current'].imag-wanted.imag))))/trace.current_sigma_a
                speed_error=float(np.max(abs(r['speed']-trace.speed_rad_s)))/trace.speed_sigma_rad_s
                assessments.append(dict(id=trace.id,split=split,current_error_sigma=current_error,speed_error_sigma=speed_error,passed=bool(current_error<=spec.tolerance_sigma and speed_error<=spec.tolerance_sigma)))
                trajectories[trace.id]=dict(time_s=trace.time_s,current_alpha_a=r['current'].real.tolist(),current_beta_a=r['current'].imag.tolist(),speed_rad_s=r['speed'].tolist())
        identifiable=electrical['identifiable'] and mechanical['identifiable']
        passed=bool(fitted.success and identifiable and not electrical['at_parameter_bound'] and electrical['conditional_current_error_sigma']<=spec.tolerance_sigma and all(r['passed'] for r in assessments))
        reason=None if passed else 'optimization_not_converged' if not fitted.success else 'electrical_not_identifiable' if not electrical['identifiable'] else 'mechanics_not_identifiable' if not mechanical['identifiable'] else 'parameter_bound_active' if electrical['at_parameter_bound'] else 'conditional_current_fit_rejected' if electrical['conditional_current_error_sigma']>spec.tolerance_sigma else 'free_replay_rejected'
        origin='Dynamic dataset '+digest+'; published/synthetic provenance retained, no field qualification'
        p.provenance.update(electrical=origin,magnetics=origin)
        if mechanical.get('estimated'):p.provenance['mechanical']=origin
        p.provenance['dynamic_identification']=origin
        if code!=package_fingerprint():raise ValueError('dynamic identification producer changed')
        return dict(completed=True,passed=passed,reason=reason,identifiable=bool(identifiable),method='conditional_rotating_flux_fit_integral_mechanics_free_heldout_replay',passport=p.model_dump(mode='json'),seed_passport=spec.initial.model_dump(mode='json'),electrical=electrical,mechanical=mechanical,parameters=names,assessments=assessments,trajectories=trajectories,code_sha256=code,dataset_sha256=digest,trace_provenance={t.id:t.provenance for t in spec.fitting+spec.validation},protocol_sha256=input_digest,optimizer=dict(success=bool(fitted.success),message=str(fitted.message),nfev=fitted.nfev,njev=fitted.njev,residual_evaluations=budget.evaluations,numeric_jacobian_residual_evaluations=budget.evaluations-fitted.nfev,active_mask=fitted.active_mask.tolist()),replay_method=spec.replay_method,calibration_domain=dict(frequency_hz=[min(t.frequency_hz for t in spec.fitting),max(t.frequency_hz for t in spec.fitting)],voltage_peak_alpha_beta_v=[min(float(min(np.hypot(t.voltage_alpha_v,t.voltage_beta_v))) for t in spec.fitting),max(float(max(np.hypot(t.voltage_alpha_v,t.voltage_beta_v))) for t in spec.fitting)],speed_rad_s=[min(min(t.speed_rad_s) for t in spec.fitting),max(max(t.speed_rad_s) for t in spec.fitting)]),interpolation=dict(voltage='linear rotating-frame alpha-beta',conditional_speed='cubic not-a-knot; not used in free replay'),known_initial_fluxes=True,electrical_parameter_bounds={n:[float(v/4),float(v*4)] for n,v in zip(names,seed)},evaluations=budget.evaluations,rhs_evaluations=budget.rhs_calls,elapsed_s=time.monotonic()-budget.started,field_validated=False,statistical_confidence_claimed=False,industrial_release_qualified=False)
    except (ValueError,ArithmeticError,TimeoutError,np.linalg.LinAlgError) as exc:
        return dict(completed=False,passed=False,identifiable=False,status='runtime_limit_exceeded' if isinstance(exc,TimeoutError) else 'identification_failed',reason=str(exc),code_sha256=code,dataset_sha256=digest,protocol_sha256=input_digest,evaluations=budget.evaluations,rhs_evaluations=budget.rhs_calls,field_validated=False)
