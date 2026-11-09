"""Event-sided shaft-energy / I²t audit and three-grid motor studies.

These are sampled RMS engineering checks, not EMT extrema or OEM temperature
qualification. The audit reconstructs algebra/shaft work without production RHS.
An independent SI trajectory reference belongs to the acceptance benchmark.
"""
from pathlib import Path
import math
import time
from typing import Literal

import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator

from power_grid_env.industrial_dol import IndustrialDOLCase,assessment
from power_grid_env.motor_trace import validate_motor_result
from power_grid_env.study_spec import StudySpec
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify


class MotorAuditLimits(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    shaft_absolute_j:float=Field(default=2.,gt=0,le=1000)
    shaft_relative:float=Field(default=5e-4,gt=0,le=.01)
    algebra_pu:float=Field(default=1e-8,gt=0,le=1e-5)
    thermal_absolute:float=Field(default=5e-4,gt=0,le=.01)
    integral_relative:float=Field(default=.002,gt=0,le=.02)
    final_speed_absolute_pu:float=Field(default=1e-5,gt=0,le=.001)


class MotorEngineeringPlan(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    schema_version:Literal['power-grid-env.motor-engineering/v1']='power-grid-env.motor-engineering/v1'
    id:str=Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')
    purpose:Literal['engineering','regression']='engineering'
    cases:list[IndustrialDOLCase]=Field(min_length=1,max_length=8)
    limits:MotorAuditLimits=Field(default_factory=MotorAuditLimits)
    per_run_timeout_s:float=Field(default=30,gt=0,le=300)
    total_timeout_s:float=Field(default=300,gt=0,le=3600)

    @model_validator(mode='after')
    def profile(self):
        if len({c.id for c in self.cases})!=len(self.cases):raise ValueError('unique engineering case IDs required')
        for case in self.cases:
            s=case.study;settings=s.coupled if s.study=='coupled_rms' else s.induction
            if s.study not in {'motor_start','coupled_rms'} or settings is None or len(settings.motors)>8 or settings.duration_s>10:
                raise ValueError('bounded balanced RMS profile: at most eight motors and ten seconds')
            if settings.duration_s/settings.step_s*4>16000:raise ValueError('refined output budget exceeded')
            if settings.event_trace!='all':raise ValueError('full event trace required for three-grid v1')
            if any(m.model!='single_cage' for m in settings.motors):raise ValueError('single-cage shaft/algebra audit v1 required')
            if case.expectation=='rejected' or (self.purpose=='engineering' and case.expectation!='within_limits'):
                raise ValueError('completed studies required; expected violations require regression purpose')
        if len(encoded(self.model_dump(mode='json')))>16000000:raise ValueError('engineering input byte budget exceeded')
        return self


def product_integrals(t,a,b):
    """Exact product integral of the two piecewise-linear sample interpolants.

    Duplicate pre/post event samples have zero width. Adjacent intervals thus
    end on pre-event and begin on post-event values, without smoothing jumps.
    """
    return np.diff(t)*(2*a[:-1]*b[:-1]+a[:-1]*b[1:]+a[1:]*b[:-1]+2*a[1:]*b[1:])/6


def audit_motor(case,report,limits=None):
    case=IndustrialDOLCase.model_validate(case);limits=MotorAuditLimits.model_validate(limits or {})
    settings=case.study.coupled if case.study.study=='coupled_rms' else case.study.induction
    if case.study.study not in {'motor_start','coupled_rms'} or settings is None or any(m.model!='single_cage' for m in settings.motors):
        raise ValueError('single-cage balanced RMS audit required')
    verdict=assessment(case,report)
    rows={}
    for motor in settings.motors:
        t,_=validate_motor_result(case.study,report,motor.id)
        sides=report['sample_side'];dt=np.diff(t)
        if sides[0]!='initial' or any(s not in {'initial','regular','pre_event','post_event'} for s in sides):raise ValueError('invalid sample side')
        for i,s in enumerate(sides):
            if s=='initial' and i!=0:raise ValueError('duplicate initial side')
            if s=='pre_event' and (i+1>=len(t) or sides[i+1]!='post_event' or t[i+1]!=t[i]):raise ValueError('missing post-event side')
            if s=='post_event' and (i==0 or sides[i-1]!='pre_event' or t[i-1]!=t[i]):raise ValueError('missing pre-event side')
        def values(key):
            a=np.asarray(report['channels'].get(motor.id+'.'+key,[]),float)
            if a.shape!=t.shape or not np.isfinite(a).all():raise ValueError('missing/nonfinite audit channel: '+key)
            return a
        speed=values('speed_pu');current=values('current_pu');te=values('te_pu');load=values('load_torque_pu')
        connected=values('connected');scale=values('load_scale')
        if np.any((connected!=0)&(connected!=1)) or np.any(current<0) or np.any(scale<0):raise ValueError('invalid discrete/current/load channel')
        if np.any((np.diff(connected)!=0)&(dt!=0)) or np.any((np.diff(scale)!=0)&(dt!=0)):
            raise ValueError('discrete transition missing one-sided event samples')
        for event in settings.events:
            tied=abs(t-event.time_s)<=64*np.finfo(float).eps*max(1.,abs(event.time_s))
            if not any(tied[i] and sides[i]=='pre_event' for i in range(len(t))):raise ValueError('scheduled event sides missing')
        # Event records are not used to invent speed jumps. A finite-inertia
        # shaft and the thermal state remain continuous across switching.
        jump=float(max(abs(np.diff(speed)[dt==0]),default=0))
        with np.errstate(over='raise',invalid='raise',divide='raise'):
            kinetic=motor.inertia_s*motor.rated_va*speed**2
            em=np.r_[0,np.cumsum(product_integrals(t,te,speed)*motor.rated_va)]
            work=np.r_[0,np.cumsum(product_integrals(t,load,speed)*motor.rated_va)]
            residual=em-work-(kinetic-kinetic[0])
            energy_scale=max(float(np.max(abs(em))),float(np.max(abs(work))),float(np.max(abs(kinetic-kinetic[0]))),1.)
            i2t=float(sum(product_integrals(t,current,current)))
            z=complex(motor.stator_r_pu,motor.stator_x_pu+motor.rotor_x_pu*motor.magnetizing_x_pu/(motor.rotor_x_pu+motor.magnetizing_x_pu))
            voltage=values('terminal_e_real_pu')+1j*values('terminal_e_imag_pu')
            e=values('e_real_pu')+1j*values('e_imag_pu')
            electrical_current=connected*(voltage-e)/z
            power=voltage*np.conj(electrical_current)
            error=max(float(np.max(abs(abs(electrical_current)-current))),float(np.max(abs((e*np.conj(electrical_current)).real-te))),
                      float(np.max(abs(power.real-values('p_w')/motor.rated_va))),float(np.max(abs(power.imag-values('q_var')/motor.rated_va))))
            error=max(error,float(np.max(abs(abs(voltage)-values('terminal_voltage_pu')))),
                      float(np.max(connected*abs(abs(voltage)-values('bus_voltage_pu')))),
                      float(np.max((1-connected)*abs(voltage-e))))
            if motor.mechanical_load is None:law=motor.fan_torque_pu*speed*abs(speed)
            else:
                p=motor.mechanical_load
                law=p.viscous_torque_pu*speed+p.quadratic_torque_pu*speed*abs(speed)
                if p.friction_torque_pu:law+=p.friction_torque_pu*np.tanh(speed/p.smoothing_speed_pu)
            law_error=float(np.max(abs(load-law*scale)))
        shaft_error=float(np.max(abs(residual)))
        checks=dict(shaft_energy=shaft_error<=limits.shaft_absolute_j+limits.shaft_relative*energy_scale,
                    algebra=error<=limits.algebra_pu,mechanical_load_law=law_error<=limits.algebra_pu,
                    continuous_speed=jump<=limits.algebra_pu)
        if settings.operating_point is None:
            checks['initial_rest']=abs(speed[0])<=limits.algebra_pu and abs(e[0])<=limits.algebra_pu
        else:
            wb=2*math.pi*case.study.network.frequency_hz
            tau=(motor.rotor_x_pu+motor.magnetizing_x_pu)/(wb*motor.rotor_r_pu)
            difference=motor.magnetizing_x_pu**2/(motor.rotor_x_pu+motor.magnetizing_x_pu)
            de=-(1/tau+1j*wb*(1-speed[0]))*e[0]+1j*difference/tau*electrical_current[0]
            tolerance=max(limits.algebra_pu,10*settings.operating_point.tolerance)
            checks['initial_equilibrium']=abs(de)<=tolerance and abs(te[0]-load[0])<=tolerance
        expected_scale=np.ones(len(t))
        for event in sorted(settings.events,key=lambda event:event.time_s):
            if event.kind=='load_torque_scale' and event.equipment_id==motor.id:
                tied=abs(t-event.time_s)<=64*np.finfo(float).eps*max(1.,abs(event.time_s))
                selected=((t>event.time_s)&~tied)|(tied&np.array([side!='pre_event' for side in sides]))
                expected_scale[selected]=event.value
        checks['declared_load_schedule']=bool(np.max(abs(expected_scale-scale))<=limits.algebra_pu)
        metrics=dict(initial_speed_pu=float(speed[0]),final_speed_pu=float(speed[-1]),
                     electromagnetic_work_j=float(em[-1]),load_work_j=float(work[-1]),kinetic_change_j=float(kinetic[-1]-kinetic[0]),
                     maximum_cumulative_shaft_balance_error_j=shaft_error,shaft_energy_scale_j=energy_scale,
                     maximum_algebra_error_pu=error,maximum_load_law_error_pu=law_error,i2t_pu2_s=i2t,
                     interval_rms_current_a=math.sqrt(i2t/(t[-1]-t[0]))*motor.rated_va/(math.sqrt(3)*motor.rated_ll_v),
                     disconnected_duration_s=float(sum(dt*(1-connected[:-1]))),maximum_event_speed_jump_pu=jump)
        if motor.thermal:
            heat=values('thermal_level')
            latch=values('thermal_latched')
            if np.any(heat<0):raise ValueError('negative thermal replica')
            if np.any((latch!=0)&(latch!=1)):raise ValueError('invalid thermal latch')
            integral_heat=np.r_[0,np.cumsum(dt*(heat[:-1]+heat[1:])/2)]
            integral_i2=np.r_[0,np.cumsum(product_integrals(t,current,current))]
            thermal_residual=heat-heat[0]-(integral_i2-integral_heat)/motor.thermal.time_constant_s
            thermal_error=float(max(abs(thermal_residual)))
            heat_jump=float(max(abs(np.diff(heat)[dt==0]),default=0))
            metrics.update(maximum_thermal_equation_residual=thermal_error,maximum_event_thermal_jump=heat_jump)
            checks.update(thermal_equation=thermal_error<=limits.thermal_absolute,continuous_thermal=heat_jump<=limits.algebra_pu)
            expected_initial=current[0]**2 if settings.operating_point and settings.operating_point.thermal_state=='equilibrium' else motor.thermal.initial_level
            changes=np.diff(latch)>0
            checks.update(initial_thermal=abs(heat[0]-expected_initial)<=limits.algebra_pu,
                          thermal_latch=bool(np.all(np.diff(latch)>=0) and np.all(connected[latch==1]==0)
                            and np.all(dt[changes]==0) and np.all(abs(heat[1:][changes]-motor.thermal.trip_level)<=limits.algebra_pu)
                            and max(heat)<=motor.thermal.trip_level+limits.algebra_pu))
        rows[motor.id]=dict(metrics=metrics,checks={k:bool(v) for k,v in checks.items()},passed=bool(all(checks.values())))
    return dict(schema='power-grid-env.motor-energy-audit/v1',passed=all(r['passed'] for r in rows.values()),
                engineering_assessment=verdict,motors=rows,
                scope='sampled balanced single-cage RMS; piecewise-linear quadrature, shaft balance, stator I2 replica; no electrical stored-energy or hotspot claim')


def grid_consensus(audits,limits):
    if len(audits)!=3:raise ValueError('all three refinement levels required')
    motors=set(audits[0]['motors'])
    if any(set(a['motors'])!=motors for a in audits):raise ValueError('refinement motor identities differ')
    errors={};checks=[]
    for identifier in sorted(motors):
        a=audits[1]['motors'][identifier]['metrics'];b=audits[2]['motors'][identifier]['metrics']
        row={}
        for key in ['i2t_pu2_s','electromagnetic_work_j','load_work_j']:
            value=abs(a[key]-b[key])/max(abs(a[key]),abs(b[key]),1e-9)
            row[key+'_relative_change']=value;checks.append(value<=limits.integral_relative)
        speed=abs(a['final_speed_pu']-b['final_speed_pu']);row['final_speed_change_pu']=speed
        checks.append(speed<=limits.final_speed_absolute_pu);errors[identifier]=row
    decisions=[a['engineering_assessment']['within_limits'] for a in audits]
    stable=len(set(decisions))==1
    qualified=all(a['passed'] for a in audits) and all(checks) and stable
    return dict(numerically_qualified=bool(qualified),decision_stable=stable,grid_decisions=decisions,
                fine_pair_changes=errors,engineering_acceptable=bool(qualified and decisions[-1]),
                fine_within_limits=decisions[-1],scope='three discrete max-step/output grids, not proof of asymptotic order or continuous extrema')


def run_engineering(document,out,*,cancelled=None):
    from power_grid_env.rms_process import run_isolated_rms
    plan=MotorEngineeringPlan.model_validate(document)
    plan=MotorEngineeringPlan.model_validate(plan.model_dump(mode='json'));out=Path(out)
    if out.exists():raise ValueError('new motor engineering output required')
    out.mkdir(parents=True);code=package_fingerprint();started=time.monotonic();used=0
    def write(name,value):
        nonlocal used
        raw=encoded(value);used+=len(raw)
        if len(raw)>64000000 or used>220000000:raise ValueError('engineering artifact byte budget exceeded')
        path=out/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    write('input.json',plan.model_dump(mode='json'));rows=[]
    for case in plan.cases:
        audits=[];reason=None
        for level in range(3):
            remaining=plan.total_timeout_s-(time.monotonic()-started)
            if cancelled is not None and cancelled():reason='cancelled';break
            if remaining<=0:reason='total_timeout';break
            if package_fingerprint()!=code:reason='producer_changed';break
            refined=case.model_copy(deep=True);settings=refined.study.coupled if refined.study.study=='coupled_rms' else refined.study.induction
            settings.step_s/=2**level
            result=run_isolated_rms(refined.study,timeout_s=min(remaining,plan.per_run_timeout_s),cancelled=cancelled,
                                    max_result_bytes=min(64000000,max(1,220000000-used)))
            write(case.id+f'/grid-{level}/study.json',refined.study.model_dump(mode='json'))
            write(case.id+f'/grid-{level}/result.json',result)
            if not result.get('completed'):reason=result.get('reason','incomplete_simulation');break
            audit=audit_motor(refined,result,plan.limits)
            write(case.id+f'/grid-{level}/audit.json',audit);audits.append(audit)
        if len(audits)==3:
            consensus=grid_consensus(audits,plan.limits)
            met=consensus['numerically_qualified'] and (consensus['fine_within_limits']==(case.expectation=='within_limits'))
            row=dict(id=case.id,status='completed',expectation_met=bool(met),**consensus)
        else:row=dict(id=case.id,status='incomplete',reason=reason,completed_grids=len(audits),numerically_qualified=False,engineering_acceptable=False,expectation_met=False)
        rows.append(row)
    unchanged=package_fingerprint()==code;complete=all(r['status']=='completed' for r in rows) and unchanged and time.monotonic()-started<plan.total_timeout_s
    summary=dict(schema='power-grid-env.motor-engineering-result/v1',id=plan.id,producer_sha256=code,completed=complete,
                 validation_passed=bool(complete and all(r['expectation_met'] for r in rows)),
                 engineering_acceptable=bool(complete and all(r['engineering_acceptable'] for r in rows)),
                 cases=rows,independent_physics_oracle=False,field_validated=False,scope='bounded sampled RMS engineering study')
    write('summary.json',summary);seal(out);return summary


def verify_engineering(root):
    root=Path(root);identity=verify(root);plan=MotorEngineeringPlan.model_validate(read_json(root/'input.json'));summary=read_json(root/'summary.json')
    if summary.get('producer_sha256')!=package_fingerprint() or not summary.get('completed'):raise ValueError('complete current-producer engineering study required')
    if len(summary['cases'])!=len(plan.cases):raise ValueError('engineering case coverage differs')
    for case,row in zip(plan.cases,summary['cases'],strict=True):
        audits=[]
        for level in range(3):
            refined=case.model_copy(deep=True);settings=refined.study.coupled if refined.study.study=='coupled_rms' else refined.study.induction
            settings.step_s/=2**level
            path=root/case.id/f'grid-{level}'
            if read_json(path/'study.json')!=refined.study.model_dump(mode='json'):raise ValueError('refined study differs from plan')
            audit=audit_motor(refined,read_json(path/'result.json'),plan.limits)
            if audit!=read_json(path/'audit.json'):raise ValueError('physical audit differs from trace')
            audits.append(audit)
        expected=grid_consensus(audits,plan.limits)
        expected=dict(id=case.id,status='completed',expectation_met=bool(expected['numerically_qualified'] and expected['fine_within_limits']==(case.expectation=='within_limits')),**expected)
        if row!=expected:raise ValueError('engineering verdict differs from audit')
    if summary['validation_passed']!=all(r['expectation_met'] for r in summary['cases']) or summary['engineering_acceptable']!=all(r['engineering_acceptable'] for r in summary['cases']):raise ValueError('summary verdict differs')
    expected_summary=dict(schema='power-grid-env.motor-engineering-result/v1',id=plan.id,producer_sha256=package_fingerprint(),completed=True,
                 validation_passed=all(r['expectation_met'] for r in summary['cases']),engineering_acceptable=all(r['engineering_acceptable'] for r in summary['cases']),
                 cases=summary['cases'],independent_physics_oracle=False,field_validated=False,scope='bounded sampled RMS engineering study')
    if summary!=expected_summary:raise ValueError('summary scope or qualification metadata differs')
    return dict(verified=True,manifest_sha256=identity,cases=len(plan.cases))
