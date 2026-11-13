"""Bounded temporal self-consistency qualification for a switching drive.

This uses the production solver at successive half steps, not an independent
oracle. A certificate needs three consecutive audited trajectories, decreasing
physical differences and fixed physical-unit Cauchy limits. It does not bound
absolute continuous-time error. Failed attempts remain part of the evidence.
"""
from pathlib import Path
import numpy as np
from pydantic import Field
from power_grid_env.phase_network import Strict
from power_grid_env.switching_drive import SwitchingDriveStudy,run_switching_drive
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,seal


class SwitchingTemporalLimits(Strict):
    dc_voltage_v: float = Field(default=.02,gt=0)
    source_current_a: float = Field(default=.01,gt=0)
    motor_current_a: float = Field(default=.005,gt=0)
    flux_vs: float = Field(default=1e-5,gt=0)
    speed_rad_s: float = Field(default=.002,gt=0)
    temperature_c: float = Field(default=.001,gt=0)
    event_time_s: float = Field(default=1e-8,gt=0)
    reduction_factor: float = Field(default=.85,gt=0,lt=1)


def inventory(study,limits):
    p=study;q=limits;groups={
        'network':dict(dc_voltage_v=q.dc_voltage_v,**{f'source_current_{k}':q.source_current_a for k in 'abc'}),
        'machine_observer':dict(speed_rad_s=q.speed_rad_s,motor_current_alpha_a=q.motor_current_a,motor_current_beta_a=q.motor_current_a,**{k:q.flux_vs for k in ['motor_stator_flux_alpha_vs','motor_stator_flux_beta_vs','flux_estimate_alpha_vs','flux_estimate_beta_vs']}),
        'thermal':{f'{k}_temperature_c':q.temperature_c for k in ['rectifier','inverter','brake']}}
    for k in range(1,len(p.motor.cages)+1):
        for axis in ['alpha','beta']:groups['machine_observer'][f'rotor_cage_{k}_flux_{axis}_vs']=q.flux_vs
    if p.motor.thermal:
        for k in ['stator','rotor']:groups['thermal'][f'motor_{k}_temperature_c']=q.temperature_c
    flags=['thermal_trip','gates_enabled','precharge_bypassed','torque_enabled']
    if p.motor.thermal:flags+=['motor_stator_thermal_trip','motor_rotor_thermal_trip']
    return groups,flags


def audited(study,result,limits):
    groups,flags=inventory(study,limits)
    if not isinstance(result,dict) or not {'time_s','channels','event_log','metrics'}<=result.keys():raise ValueError('incomplete temporal result inventory')
    if not isinstance(result['channels'],dict) or not isinstance(result['metrics'],dict) or not isinstance(result['event_log'],list):raise ValueError('invalid temporal result containers')
    required=[k for group in groups.values() for k in group]+flags+['voltage_limited']
    if not set(required)<=result['channels'].keys():raise ValueError('incomplete temporal channel inventory')
    if not {'final_energy_relative_error','kcl_max_a','motor_constitutive_residual_v'}<=result['metrics'].keys():raise ValueError('incomplete temporal physics audit')
    times=np.asarray(result['time_s'],float)
    if times.ndim!=1:raise ValueError('invalid temporal time axis')
    count=int(np.floor((study.duration_s+5e-13)/study.sample_s))+1
    last=(count-1)*study.sample_s
    if study.duration_s-last>5e-13:count+=1
    if not result.get('completed') or len(times)!=count or len(times)<2 or not np.all(np.isfinite(times)) or abs(times[0])>5e-13 or abs(times[-1]-study.duration_s)>5e-13 or np.any(np.diff(times)<=0):raise ValueError('incomplete temporal trajectory coverage')
    expected=np.minimum(np.arange(count)*study.sample_s,study.duration_s)
    if not np.allclose(times,expected,rtol=0,atol=5e-13):raise ValueError('temporal sampling clock differs')
    for k in required:
        values=np.asarray(result['channels'][k],float)
        if values.shape!=times.shape or not np.all(np.isfinite(values)):raise ValueError('nonfinite or incomplete temporal channel: '+k)
        if k in flags+['voltage_limited'] and not np.all((values==0)|(values==1)):raise ValueError('nonbinary temporal action: '+k)
    for event in result['event_log']:
        if not isinstance(event,dict) or not {'time_s','kind'}<=event.keys():raise ValueError('incomplete temporal event inventory')
        if not np.isfinite(event['time_s']) or not -5e-13<=event['time_s']<=study.duration_s+5e-13:raise ValueError('invalid temporal event clock')
    m=result['metrics'];values=[m[k] for k in ['final_energy_relative_error','kcl_max_a','motor_constitutive_residual_v']]
    if not all(np.isfinite(v) and v>=0 for v in values):raise ValueError('nonfinite temporal physics audit')
    return dict(energy=bool(values[0]<1e-7),kcl=bool(values[1]<5e-5),magnetic=bool(values[2]<1e-8))


def differences(study,a,b,limits):
    groups,flags=inventory(study,limits)
    if len(a['time_s'])!=len(b['time_s']) or not np.allclose(a['time_s'],b['time_s'],rtol=0,atol=5e-13):raise ValueError('temporal comparison clocks differ')
    summary={}
    for name,channels in groups.items():
        errors={k:float(np.max(abs(np.asarray(a['channels'][k])-b['channels'][k]))) for k in channels}
        worst=max(errors,key=lambda k:errors[k]/channels[k]);norm=errors[worst]/channels[worst]
        summary[name]=dict(errors=errors,limits=channels,maximum_normalized=float(norm),worst_channel=worst,passed=bool(norm<=1.))
    exact={k:bool(np.array_equal(a['channels'][k],b['channels'][k])) for k in flags}
    enabled=(np.asarray(a['channels']['gates_enabled'])!=0)|(np.asarray(b['channels']['gates_enabled'])!=0)
    exact['active_voltage_limited']=bool(np.array_equal(np.asarray(a['channels']['voltage_limited'])[enabled],np.asarray(b['channels']['voltage_limited'])[enabled]))
    events_a=a['event_log'];events_b=b['event_log'];event_match=len(events_a)==len(events_b);event_error=0.
    if event_match:
        for x,y in zip(events_a,events_b):
            event_match=event_match and all(x.get(k)==y.get(k) for k in ['kind','component','cause'])
            event_error=max(event_error,abs(x['time_s']-y['time_s']))
    event_match=event_match and event_error<=limits.event_time_s
    return dict(passed=bool(all(r['passed'] for r in summary.values()) and all(exact.values()) and event_match),groups=summary,discrete_actions=exact,events_match=bool(event_match),maximum_event_time_difference_s=float(event_error))


def _qualification(study,max_levels,limits,sink):
    p=SwitchingDriveStudy.model_validate(study);q=SwitchingTemporalLimits.model_validate(limits or {})
    if isinstance(max_levels,bool) or not isinstance(max_levels,int) or not 3<=max_levels<=9:raise ValueError('temporal qualification requires 3..9 levels')
    if p.adaptive is not None:raise ValueError('fixed-mesh qualification requires adaptive=None; refine adaptive error budgets separately')
    before=package_fingerprint();attempts=[];previous=None;previous_difference=None;previous_audits=[];selected=None;status='levels_exhausted'
    for index in range(max_levels):
        h=p.max_step_s/2**index
        if h<1e-9 or p.duration_s/h>2*p.step_budget/3:
            status='step_budget_exhausted';attempts.append(dict(index=index,step_s=h,status='not_run',reason='requested refinement exceeds declared step budget/profile'));break
        candidate=SwitchingDriveStudy.model_validate(dict(p.model_dump(mode='json'),max_step_s=h));doc=candidate.model_dump(mode='json')
        try:
            result=run_switching_drive(doc);audits=audited(candidate,result,q)
        except (ValueError,RuntimeError,np.linalg.LinAlgError) as exc:
            attempt=dict(index=index,step_s=h,status='solver_failed',error=str(exc));attempts.append(attempt);sink(index,doc,None,attempt)
            previous=None;previous_difference=None;previous_audits=[];continue
        attempt=dict(index=index,step_s=h,status='completed',physics_audits=audits,steps=result['metrics']['steps']);check=None;reduction={}
        if previous is not None:
            check=differences(candidate,previous,result,q);attempt['difference']=check
            if previous_difference is not None:
                for name,current in check['groups'].items():
                    old=previous_difference['groups'][name]['maximum_normalized'];new=current['maximum_normalized']
                    reduction[name]=dict(previous_normalized=old,current_normalized=new,ratio=new/old if old>1e-8 else None,passed=bool(new<=max(q.reduction_factor*old,1e-8)))
                attempt['reduction']=reduction
                if check['passed'] and all(v['passed'] for v in reduction.values()) and all(a for entry in previous_audits[-2:]+[audits] for a in entry.values()):
                    selected=h;status='qualified'
        attempts.append(attempt);sink(index,doc,result,attempt);previous=result;previous_difference=check;previous_audits.append(audits)
        if selected is not None:break
    if package_fingerprint()!=before:raise RuntimeError('temporal qualification producer changed during run')
    return dict(schema='power-grid-env.switching-temporal-qualification/v1',completed=True,qualified=selected is not None,status=status,selected_step_s=selected,limits=q.model_dump(mode='json'),attempts=attempts,code_sha256=before,scope='production midpoint solver, physical-unit Cauchy differences and reduction across three audited meshes; discrete physical actions/events checked; instantaneous last_midpoint snapshots excluded; not an independent oracle, an absolute error bound, an asymptotic order proof or universal step recommendation',absolute_error_certified=False,independent_reference=False)


def qualify_switching_drive(study,*,max_levels=6,limits=None):
    return _qualification(study,max_levels,limits,lambda *args:None)


def run_switching_qualification_artifact(study,out,*,max_levels=6,limits=None):
    p=SwitchingDriveStudy.model_validate(study);q=SwitchingTemporalLimits.model_validate(limits or {})
    if isinstance(max_levels,bool) or not isinstance(max_levels,int) or not 3<=max_levels<=9:raise ValueError('temporal qualification requires 3..9 levels')
    if p.adaptive is not None:raise ValueError('fixed-mesh qualification requires adaptive=None; refine adaptive error budgets separately')
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    (out/'study.json').write_bytes(encoded(p.model_dump(mode='json')))
    (out/'protocol.json').write_bytes(encoded(dict(max_levels=max_levels,limits=q.model_dump(mode='json'),step_budget=p.step_budget)))
    def store(index,doc,result,attempt):
        folder=out/f'level-{index}';folder.mkdir()
        (folder/'study.json').write_bytes(encoded(doc));(folder/'assessment.json').write_bytes(encoded(attempt))
        if result is not None:(folder/'result.json').write_bytes(encoded(result))
    summary=_qualification(p.model_dump(mode='json'),max_levels,q.model_dump(mode='json'),store)
    (out/'summary.json').write_bytes(encoded(summary));seal(out);return summary
