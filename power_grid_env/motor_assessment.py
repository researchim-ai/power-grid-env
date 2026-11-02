"""Reproducible sampled acceptance of motor starting and self-restarting studies.

A completed simulation and a satisfactory start are distinct outcomes. Criteria
are explicit project inputs; no manufacturer limits or causal diagnoses inferred.
"""
from pathlib import Path
import argparse,json,math
import numpy as np
from pydantic import BaseModel,ConfigDict,Field
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.report import write_json,sha256_file
from power_grid_env.study_spec import StudySpec,run_study


class MotorAcceptanceCriteria(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    required_speed_pu: float=Field(default=.95,gt=0,le=2)
    settling_window_s: float=Field(default=.1,gt=0,le=60)
    minimum_sampled_voltage_pu: float | None=Field(default=None,ge=0)
    maximum_sampled_current_pu: float | None=Field(default=None,gt=0)


def assess(spec,report,criteria):
    if spec.study not in ('motor_start','coupled_rms'):raise ValueError('motor_start or coupled_rms required')
    settings=spec.coupled if spec.coupled else spec.induction
    if not report.get('completed'):
        return dict(passed=False,calculation_completed=False,status='calculation_failed',calculation_status=report.get('status'),reason=report.get('reason'),motors={})
    if report.get('code_sha256')!=package_fingerprint() or report.get('network_sha256')!=canonical_hash(spec.network) or report.get('settings')!=settings.model_dump(mode='json'):raise ValueError('stale or mismatched motor trace')
    t=np.asarray(report['time_s'],dtype=float);sides=report['sample_side']
    if t.ndim!=1 or len(t)<2 or len(sides)!=len(t) or not np.isfinite(t).all() or t[0]!=0 or abs(t[-1]-settings.duration_s)>1e-9 or np.any(np.diff(t)<0):raise ValueError('incomplete motor time axis')
    for j,side in enumerate(sides):
        if side not in ('initial','regular','pre_event','post_event'):raise ValueError('unknown sample side')
        if side=='pre_event' and (j+1>=len(t) or sides[j+1]!='post_event' or t[j+1]!=t[j]):raise ValueError('unpaired motor event')
        if side=='post_event' and (j==0 or sides[j-1]!='pre_event' or t[j-1]!=t[j]):raise ValueError('unpaired motor event')
        if j and t[j]==t[j-1] and not(sides[j-1]=='pre_event' and side=='post_event'):raise ValueError('ambiguous duplicate motor time')
    ch=report['channels'];rows={};window_start=settings.duration_s-criteria.settling_window_s
    for motor in settings.motors:
        def channel(name):
            values=np.asarray(ch[motor.id+'.'+name],dtype=float)
            if values.shape!=t.shape or not np.isfinite(values).all():raise ValueError('nonfinite or incomplete motor channel')
            return values
        speed=channel('speed_pu');current=channel('current_pu');voltage=channel('bus_voltage_pu');connected=channel('connected')
        if np.any(current<0) or np.any(voltage<0) or not np.isin(connected,[0,1]).all():raise ValueError('invalid motor magnitude or discrete flag')
        failures=[]
        if window_start<0:failures.append('insufficient_horizon_for_settling_window')
        start=max(0.,window_start)
        indices=np.flatnonzero((t>start)|((t==start)&(np.asarray(sides)!='pre_event')))
        edge=int(np.searchsorted(t,start,side='right')-1)
        minimum_speed=min(float(np.interp(start,t,speed)),float(min(speed[indices])))
        all_connected=bool(connected[edge]==1 and np.all(connected[indices]==1))
        if minimum_speed<criteria.required_speed_pu:failures.append('required_speed_not_maintained_in_final_window')
        if not all_connected:failures.append('motor_not_connected_through_final_window')
        thermal=False
        if motor.thermal:
            latch=channel('thermal_latched')
            if not np.isin(latch,[0,1]).all():raise ValueError('invalid thermal latch')
            thermal=bool(np.any(latch==1))
            if thermal:failures.append('motor_thermal_trip_observed')
        if criteria.minimum_sampled_voltage_pu is not None and min(voltage)<criteria.minimum_sampled_voltage_pu:failures.append('sampled_voltage_below_declared_minimum')
        if criteria.maximum_sampled_current_pu is not None and max(current)>criteria.maximum_sampled_current_pu:failures.append('sampled_current_above_declared_maximum')
        good=(speed>=criteria.required_speed_pu)&(connected==1)
        bad=np.flatnonzero(~good);first=int(bad[-1]+1) if len(bad) else 0
        sustained=float(t[first]) if first<len(t) else None
        events=[e for e in report.get('event_log',[]) if e.get('equipment_id')==motor.id]
        rows[motor.id]=dict(passed=not failures,failure_reasons=failures,final_speed_pu=float(speed[-1]),final_connected=bool(connected[-1]),
            minimum_final_window_speed_pu=minimum_speed,sampled_sustained_speed_start_s=sustained,
            maximum_sampled_current_pu=float(max(current)),minimum_sampled_bus_voltage_pu=float(min(voltage)),thermal_trip_observed=thermal,events=events)
    passed=all(r['passed'] for r in rows.values())
    return dict(passed=passed,calculation_completed=True,status='accepted' if passed else 'criteria_not_met',motors=rows,
                criteria=criteria.model_dump(mode='json'),criterion_scope='saved samples and linear interpolation at window edge; no continuous extrema certification',
                study_sha256=canonical_hash(spec),code_sha256=package_fingerprint(),industrial_profile_qualified=False)


def run(spec,criteria,out):
    if spec.study not in ('motor_start','coupled_rms'):raise ValueError('motor_start or coupled_rms required')
    out=Path(out)
    if out.exists() and any(out.iterdir()):raise ValueError('new empty assessment directory required')
    out.mkdir(parents=True,exist_ok=True)
    write_json(out/'protocol.json',dict(study=spec.model_dump(mode='json'),criteria=criteria.model_dump(mode='json'),code_sha256=package_fingerprint(),study_sha256=canonical_hash(spec)))
    report=run_study(spec,isolate=True,wall_timeout_s=120.);write_json(out/'trace.json',report)
    result=assess(spec,report,criteria);write_json(out/'assessment.json',result)
    write_json(out/'manifest.json',dict(passed=result['passed'],calculation_completed=result['calculation_completed'],files={p.name:sha256_file(p) for p in out.iterdir() if p.is_file()}))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('study',type=Path);p.add_argument('--criteria',type=Path);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    s=StudySpec.model_validate_json(a.study.read_text());c=MotorAcceptanceCriteria.model_validate_json(a.criteria.read_text()) if a.criteria else MotorAcceptanceCriteria()
    r=run(s,c,a.out);print(json.dumps(r,indent=2,allow_nan=False))
    raise SystemExit(0 if r['passed'] else 2 if r['calculation_completed'] else 1)


if __name__=='__main__':main()
