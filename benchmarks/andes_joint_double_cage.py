"""Four preregistered active GENROU/Motor5 cold-start cases, native step refinement."""
from pathlib import Path
import argparse,math
import numpy as np
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json,sha256_file
from benchmarks.andes_motor_reference import STEPS,LIMITS
from benchmarks.andes_joint_motor_reference import native_reference,scope

NAMES=('cold','voltage_sag','setpoint_steps','saturated_sag')


def example(name):
    if name not in NAMES:raise ValueError('unknown external joint case')
    return StudySpec.model_validate_json(Path('cases/studies/andes_joint_double_cage_'+name+'.json').read_text())


def sample_native(native,times,sides,key):
    """Interpolate within continuous segments, never across an event jump."""
    t=np.array(native['time_s']);v=np.array(native['channels'][key]);side=np.array(native['sample_side'])
    if t.ndim!=1 or v.shape!=t.shape or side.shape!=t.shape or not np.isfinite(t).all() or not np.isfinite(v).all() or np.any(np.diff(t)<0):raise ValueError('malformed native trajectory')
    if len(t)<2 or times[0]<t[0]-1e-12 or times[-1]>t[-1]+1e-12:raise ValueError('native trajectory horizon mismatch')
    values=[]
    for at,label in zip(times,sides):
        exact=np.flatnonzero(abs(t-at)<1e-12)
        if label in {'pre_event','post_event'}:
            match=exact[side[exact]==label]
            if len(match)!=1:raise ValueError('native event side missing/duplicated')
            values.append(v[match[0]]);continue
        if len(exact)==1:values.append(v[exact[0]]);continue
        if len(exact)>1:raise ValueError('ambiguous event-side sample')
        right=int(np.searchsorted(t,at,side='right'));left=right-1
        if left<0 or right>=len(t) or t[right]<=t[left]:raise ValueError('invalid native interpolation bracket')
        values.append(np.interp(at,t[left:right+1],v[left:right+1]))
    return np.array(values)


def validate_event_samples(report,settings):
    times=np.asarray(report['time_s']);sides=np.asarray(report['sample_side'])
    if times.ndim!=1 or len(times)<2 or sides.shape!=times.shape or not np.isfinite(times).all() or np.any(np.diff(times)<0) or abs(times[0])>1e-12 or abs(times[-1]-settings.duration_s)>1e-12:raise ValueError('incomplete joint sample timeline')
    instants={e.time_s for e in settings.events}
    for at in instants:
        found=sides[abs(times-at)<1e-12]
        if list(found)!=['pre_event','post_event']:raise ValueError('scheduled joint event sides missing or duplicated')
    for i in np.flatnonzero(np.diff(times)==0):
        if not any(abs(times[i]-at)<1e-12 for at in instants):raise ValueError('unregistered duplicate sample time')


def compare(spec,steps=STEPS,out=None):
    scope(spec)
    if len(steps)<2 or list(steps)!=sorted(set(steps),reverse=True) or any(not math.isfinite(h) or not 1e-5<=h<=.01 for h in steps):raise ValueError('strictly decreasing native steps required')
    actual=run_study(spec,isolate=True,wall_timeout_s=60.)
    if not actual.get('completed'):raise ValueError('production joint simulation incomplete')
    validate_event_samples(actual,spec.coupled)
    m=spec.coupled.motors[0];g=spec.network.generators[0]
    limits={m.id+'.'+k:v for k,v in LIMITS.items()}
    limits.update({g.id+'.'+k:v for k,v in dict(delta=1e-4,omega=1e-5,e1q=1e-3,e1d=1e-3,e2d=1e-3,e2q=1e-3,p_w=1000.,q_var=1000.).items()})
    limits.update({g.id+'.TGOV1.valve_pu':1e-4,g.id+'.SEXS.field_pu':1e-3})
    if out is not None:
        out=Path(out);write_json(out/'study.json',spec.model_dump(mode='json'));write_json(out/'production.json',actual)
    rows=[]
    for step in steps:
        native=native_reference(spec,step)
        if not native.get('completed'):
            rows.append(dict(step_s=step,passed=False,failure=native));continue
        validate_event_samples(native,spec.coupled)
        if set(native['channels'])!=set(limits):raise ValueError('native joint channel inventory mismatch')
        errors={k:float(max(abs(sample_native(native,actual['time_s'],actual['sample_side'],k)-actual['channels'][k]))) for k in limits}
        rows.append(dict(step_s=step,errors=errors,passed=all(np.isfinite(errors[k]) and errors[k]<=limit for k,limit in limits.items()),initial_algebraic_residual_pu=native['initial_algebraic_residual_pu'],initial_generator_controller_rhs=native['initial_nonmotor_rhs'],seed_voltage_replacement_error_pu=native['seed_voltage_replacement_error_pu']))
        if out is not None:write_json(out/f'andes-{step}.json',native)
    convergence=all('errors' in row for row in rows) and all(rows[-1]['errors'][k]<rows[0]['errors'][k] for k in [m.id+'.speed_pu',g.id+'.delta'])
    result=dict(case_id=spec.id,passed=bool(rows[-1]['passed'] and convergence),steps=rows,finest_limits=limits,convergence_passed=convergence,event_sides_verified=True,
        convergence_protocol='Fine speed and generator angle errors must both decrease relative to coarse; every fine channel must meet fixed limits',field_validated=False,industrial_profile_qualified=False)
    if out is not None:
        result['artifacts_sha256']={p.name:sha256_file(p) for p in sorted(out.glob('*.json')) if p.name!='comparison.json'}
        write_json(out/'comparison.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    rows=[]
    for name in NAMES:
        r=compare(example(name),out=a.out/name);rows.append(r);print(name,r['passed'],flush=True)
    result=dict(passed=all(r['passed'] for r in rows),cases=rows,code_sha256=package_fingerprint(),
        reference='ANDES 2.0.0 active GENROU/TGOV1/SEXS + analytically mapped Motor5, native trapezoidal stepper; independent cold-load initialization',
        source_sha256={str(p):sha256_file(p) for p in map(Path,[__file__,'benchmarks/andes_joint_motor_reference.py','benchmarks/double_cage_andes_limits.py'])},
        case_files_sha256={str(p):sha256_file(p) for p in [Path('cases/studies/andes_joint_double_cage_'+n+'.json') for n in NAMES]},
        scope='Balanced two-node source/uncharged feeder/common motor-generator bus, fan load, cold starts, source voltage and controller setpoint steps; no coasting/reclose/thermal/PSS/EMT',
        field_validated=False,industrial_profile_qualified=False,competitive_superiority_proven=False)
    write_json(a.out/'summary.json',result)
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
