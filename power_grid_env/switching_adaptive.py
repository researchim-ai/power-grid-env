"""Step doubling with rollback, event alignment and diode-zero restart.

Two native midpoint steps are accepted without Richardson extrapolation.
Local estimates are not rigorous global error bounds. Source-current event
projection is bounded, separately recorded, and never booked as physical heat.
"""
import copy
import math
import numpy as np
from pydantic import Field
from power_grid_env.phase_network import Strict
from power_grid_env.pwm_bridge import T


class AdaptiveMidpointControl(Strict):
    error_budget_fraction: float = Field(default=.2,ge=1e-6,le=1.)
    local_floor_fraction: float = Field(default=1e-3,gt=0,le=.01)
    dc_voltage_v: float = Field(default=.02,gt=0)
    source_current_a: float = Field(default=.01,gt=0)
    motor_current_a: float = Field(default=.005,gt=0)
    flux_vs: float = Field(default=1e-5,gt=0)
    speed_rad_s: float = Field(default=.002,gt=0)
    temperature_c: float = Field(default=.001,gt=0)
    event_time_s: float = Field(default=1e-9,ge=1e-12,le=1e-6)
    source_event_current_a: float = Field(default=1e-7,ge=1e-12,le=1e-4)
    motor_event_current_a: float = Field(default=1e-7,ge=1e-12,le=1e-4)
    min_step_s: float = Field(default=1e-10,ge=1e-12)
    trial_budget: int = Field(default=500_000,ge=1,le=2_000_000)


_ARRAYS=('line','flux','voltage','temperature','winding_temperature','latched','motor_latched','previous_gates','_old_rails')
_COST=('newton_calls','linear_direct_solves','linear_least_squares_solves','linear_direct_fallbacks')
_STATS=('adaptive_trials','adaptive_probe_evaluations','adaptive_rejections','adaptive_solver_failures','adaptive_accepted_pairs','adaptive_event_steps','adaptive_source_boundaries','adaptive_motor_boundaries','adaptive_root_probes','adaptive_peak_estimate','adaptive_min_accepted_step_s','adaptive_max_accepted_step_s','adaptive_source_event_energy_j','_adaptive_next_h')


def initialize(model):
    if hasattr(model,'adaptive_trials'):return
    for name in _STATS:setattr(model,name,0)
    model.adaptive_min_accepted_step_s=model.p.max_step_s
    model._adaptive_next_h=model.p.max_step_s;model.adaptive_mesh_events=[]


def clone_trial(model):
    trial=copy.copy(model)
    for name in _ARRAYS:
        if hasattr(model,name):setattr(trial,name,getattr(model,name).copy())
    trial.events=list(model.events)
    if hasattr(model,'adaptive_mesh_events'):trial.adaptive_mesh_events=list(model.adaptive_mesh_events)
    # Raw steps never call the shared controller; static stamps and stateless
    # magnetics are also read-only while each trial owns its physical arrays.
    return trial


def _current(model,gates):
    p=model.p;factor=1+p.thermal.resistance_alpha_per_k*(model.temperature[1]-25.)
    return model.magnetics.terminal_current(model.flux,model.voltage[3:5],gates,p.mosfet_resistance_ohm*factor,p.body_diode_resistance_ohm*factor,p.body_diode_drop_v,model.winding_temperature if p.motor.thermal else None)[:2]


def _events(model,initial):
    return [(e['kind'],e.get('component'),e.get('cause')) for e in model.events[initial:]]


def _source_tolerance(cfg):
    return min(cfg.source_event_current_a,max(1e-12,.1*cfg.source_current_a*cfg.error_budget_fraction*cfg.local_floor_fraction))


def _crossings(original,trial,cfg):
    eligible=abs(original.line)>_source_tolerance(cfg)
    return np.flatnonzero(eligible&(original.line*trial.line<=0.)).tolist()


def _motor_tolerance(cfg):
    return min(cfg.motor_event_current_a,max(1e-12,.1*cfg.motor_current_a*cfg.error_budget_fraction*cfg.local_floor_fraction))


def _motor_crossings(initial,current,gates,cfg):
    eligible=(abs(initial)>_motor_tolerance(cfg))&(~np.any(gates,axis=1))
    # When the last conducting diode pair releases an isolated star, all
    # terminal currents vanish together. Roundoff may give the two legs
    # opposite signed zeros. Canonicalize this vector-zero event using the
    # already declared current tolerance, without changing flux or current.
    if np.max(abs(current))<=_motor_tolerance(cfg):return np.flatnonzero(eligible).tolist()
    return np.flatnonzero(eligible&(initial*current<=0.)).tolist()


def _probe(model,t,h,gates,refined):
    cfg=model.p.adaptive
    if model.adaptive_probe_evaluations>=cfg.trial_budget:raise RuntimeError('adaptive probe budget exhausted')
    model.adaptive_probe_evaluations+=1;trial=clone_trial(model)
    before={name:getattr(model,name) for name in _COST};parts=[];peaks=[];elapsed=0.;phases=[];motor_phases=[]
    initial_motor=T@_current(model,gates)
    try:
        for _ in range(2 if refined else 1):
            actual,q=trial._midpoint_step(t+elapsed,h/2 if refined else h,gates)
            parts.append((actual,q));elapsed+=actual;end_current=_current(trial,gates);peaks.append(float(np.linalg.norm(end_current)))
            motor_phases=_motor_crossings(initial_motor,T@end_current,gates,cfg)
            phases=_crossings(model,trial,cfg)
            if phases or motor_phases or len(trial.events)>len(model.events):break
        return dict(state=trial,parts=parts,peaks=peaks,elapsed=elapsed,source_phases=phases,motor_phases=motor_phases)
    finally:
        # Includes coarse/rejected/root work, but not their physical ledgers.
        for name in _COST:setattr(model,name,getattr(model,name)+getattr(trial,name)-before[name])


def _boundary(model,probe):
    return bool(probe['source_phases'] or probe['motor_phases'] or len(probe['state'].events)>len(model.events))


def _locate(model,t,h,gates,refined,upper_probe):
    low=0.;high=h;best=upper_probe;cfg=model.p.adaptive
    for _ in range(48):
        source_resolved=all(abs(best['state'].line[k])<=_source_tolerance(cfg) for k in best['source_phases'])
        motor=T@_current(best['state'],gates)
        motor_resolved=all(abs(motor[k])<=_motor_tolerance(cfg) for k in best['motor_phases'])
        if high-low<=min(1e-13,cfg.event_time_s) and source_resolved and motor_resolved:return best
        middle=(low+high)/2
        if t+middle==t:raise RuntimeError('adaptive event below floating time resolution')
        model.adaptive_root_probes+=1;probe=_probe(model,t,middle,gates,refined)
        if _boundary(model,probe):high=middle;best=probe
        else:low=middle
    raise RuntimeError('adaptive event localization did not reach current/time tolerance')


def _project_source(model,probe):
    trial=probe['state'];phases=probe['source_phases']
    if not phases:return 0.,[]
    values=trial.line[phases].copy();tol=_source_tolerance(model.p.adaptive)
    if np.max(abs(values))>tol:raise RuntimeError('unresolved source-current boundary')
    energy=float(.5*model.p.source_inductance_h*(values@values))
    scale=max(1.,abs(trial.stored()),abs(trial.work),abs(trial.external_loss))
    if not math.isfinite(energy) or not math.isfinite(scale) or energy>1e-12*scale:raise RuntimeError('source event energy correction exceeds numerical budget')
    trial.line[phases]=0.
    return energy,values.tolist()


def _integrals(parts):
    return (sum(h*complex(*q['currents_mid'][:2]) for h,q in parts),sum(h*q['speed_mid'] for h,q in parts))


def _error(original,coarse,fine,gates,cfg):
    c=coarse['state'];f=fine['state'];hc=coarse['elapsed'];hf=fine['elapsed'];dt=hf-hc;aligned=c
    if dt:
        aligned=clone_trial(c)
        for name in ['line','flux','temperature','winding_temperature']:
            setattr(aligned,name,getattr(c,name)+dt/hc*(getattr(c,name)-getattr(original,name)))
        aligned.voltage[3:5]=c.voltage[3:5]+dt/hc*(c.voltage[3:5]-original.voltage[3:5])
        aligned.speed=c.speed+dt/hc*(c.speed-original.speed)
    allocation=cfg.error_budget_fraction*max(hf/original.p.duration_s,cfg.local_floor_fraction);values=[]
    def add(a,b,limit):
        delta=np.max(abs(np.asarray(a)-np.asarray(b))) if np.size(a) else 0.
        values.append(float(delta/(limit*allocation)))
    add(aligned.line,f.line,cfg.source_current_a);add(aligned.flux,f.flux,cfg.flux_vs)
    add(aligned.voltage[3:5],f.voltage[3:5],cfg.dc_voltage_v)
    add(aligned.voltage[3]-aligned.voltage[4],f.voltage[3]-f.voltage[4],cfg.dc_voltage_v)
    add(aligned.speed,f.speed,cfg.speed_rad_s);add(aligned.temperature,f.temperature,cfg.temperature_c)
    add(aligned.winding_temperature,f.winding_temperature,cfg.temperature_c);add(_current(aligned,gates),_current(f,gates),cfg.motor_current_a)
    ci,cw=_integrals(coarse['parts']);fi,fw=_integrals(fine['parts']);current=_current(c,gates)
    ci+=dt*complex(*current);cw+=dt*c.speed
    values.extend([abs(ci-fi)/(cfg.motor_current_a*hf*cfg.error_budget_fraction),abs(cw-fw)/(cfg.speed_rad_s*hf*cfg.error_budget_fraction),abs(dt)/cfg.event_time_s])
    if dt:
        for name,limit in [('line',cfg.source_current_a),('flux',cfg.flux_vs),('temperature',cfg.temperature_c),('winding_temperature',cfg.temperature_c)]:
            delta=getattr(c,name)-getattr(original,name)
            if delta.size:values.append(float(np.max(abs(delta))*abs(dt)/(hc*limit*allocation)))
    return float(max(values)) if all(math.isfinite(v) for v in values) else float('inf')


def adaptive_step(model,t,requested,gates):
    initialize(model);cfg=model.p.adaptive
    if not math.isfinite(requested) or requested<=0:raise ValueError('positive finite adaptive interval required')
    if model.steps+2>model.p.step_budget:raise RuntimeError('adaptive midpoint requires budget for two accepted native steps')
    proposal=model._adaptive_next_h;h=min(requested,proposal);failure=None
    for _ in range(40):
        if t+h==t or h<=0:raise RuntimeError('adaptive interval below floating time resolution')
        model.adaptive_trials+=1;estimate=float('inf');projection=0.;corrections=[]
        try:
            coarse=_probe(model,t,h,gates,False);fine=_probe(model,t,h,gates,True)
            # Root localization prevents a trapezoidal inductor reflection
            # across a blocking diode. It also separates coincident trips.
            if coarse['source_phases'] or fine['source_phases'] or coarse['motor_phases'] or fine['motor_phases']:
                if _boundary(model,coarse):coarse=_locate(model,t,h,gates,False,coarse)
                if _boundary(model,fine):fine=_locate(model,t,h,gates,True,fine)
                if any(set(coarse[k])!=set(fine[k]) for k in ['source_phases','motor_phases']):raise RuntimeError('coarse/fine current event identities differ')
                _project_source(model,coarse);projection,corrections=_project_source(model,fine)
            c=coarse['state'];f=fine['state'];n=len(model.events)
            aligned_events=_events(c,n)==_events(f,n) and np.array_equal(c.latched,f.latched) and np.array_equal(c.motor_latched,f.motor_latched)
            if aligned_events:
                estimate=_error(model,coarse,fine,gates,cfg)
                if max(c.kcl_max,f.kcl_max)>5e-5 or max(c.magnetic_residual_max,f.magnetic_residual_max)>1e-8:estimate=float('inf')
            failure=None
        except (ValueError,RuntimeError,np.linalg.LinAlgError) as exc:
            model.adaptive_solver_failures+=1;failure=str(exc)
            if failure=='adaptive probe budget exhausted':raise
        if math.isfinite(estimate):model.adaptive_peak_estimate=max(model.adaptive_peak_estimate,estimate)
        if estimate<=1.:
            if fine['state'].steps-model.steps!=len(fine['parts']):raise RuntimeError('adaptive accepted-step accounting differs')
            costs={name:getattr(model,name) for name in _COST};stats={name:getattr(model,name) for name in _STATS};events=list(model.adaptive_mesh_events)
            model.__dict__.update(fine['state'].__dict__)
            for name,value in costs.items():setattr(model,name,value)
            for name,value in stats.items():setattr(model,name,value)
            model.adaptive_mesh_events=events;parts=fine['parts'];elapsed=fine['elapsed']
            model.adaptive_accepted_pairs+=int(len(parts)==2);model.adaptive_event_steps+=int(len(model.events)>n)
            model.adaptive_min_accepted_step_s=min(model.adaptive_min_accepted_step_s,min(part[0] for part in parts))
            model.adaptive_max_accepted_step_s=max(model.adaptive_max_accepted_step_s,max(part[0] for part in parts))
            if fine['source_phases']:
                model.adaptive_source_boundaries+=1;model.adaptive_source_event_energy_j+=projection
                model.adaptive_mesh_events.append(dict(time_s=t+elapsed,kind='source_current_zero',phases=fine['source_phases'],current_correction_a=corrections,energy_correction_j=projection))
            if fine['motor_phases']:
                model.adaptive_motor_boundaries+=1
                motor=T@_current(model,gates)
                model.adaptive_mesh_events.append(dict(time_s=t+elapsed,kind='motor_current_zero',phases=fine['motor_phases'],current_residual_a=motor[fine['motor_phases']].tolist(),flux_projection=False))
            exponent=1/3 if elapsed/model.p.duration_s<cfg.local_floor_fraction else .5
            factor=2. if estimate==0 else min(2.,max(.5,.8/estimate**exponent))
            model._adaptive_next_h=min(model.p.max_step_s,max(cfg.min_step_s,h*factor))
            if h<proposal and estimate<.25 and not fine['source_phases'] and not fine['motor_phases']:model._adaptive_next_h=min(model.p.max_step_s,max(proposal,model._adaptive_next_h))
            q=dict(parts[-1][1]);ci,cw=_integrals(parts)
            q.update(observer_current_integral_as=ci,observer_speed_integral_rad=cw,interval_body_diode_heat_j=sum(part[0]*part[1]['body_diode_power_w'] for part in parts),interval_overlap_steps=sum(int(part[1]['rectifier_conducting']>2) for part in parts),accepted_peak_current_a=max(fine['peaks']))
            return elapsed,q
        model.adaptive_rejections+=1
        if h<=cfg.min_step_s:raise RuntimeError(f'adaptive error control reached min_step_s at t={t}, h={h}, estimate={estimate}'+(': '+failure if failure else ''))
        exponent=1/3 if h/model.p.duration_s<cfg.local_floor_fraction else .5
        factor=.5 if not math.isfinite(estimate) else max(.1,min(.5,.8/estimate**exponent))
        h=min(requested,max(cfg.min_step_s,h*factor))
    raise RuntimeError('adaptive midpoint rejection limit exceeded')
