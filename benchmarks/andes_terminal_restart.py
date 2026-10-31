"""Adapted native Motor5 open-stator boundary, exact coast law and joint restart.

This is explicitly NOT stock ANDES u=0 coasting. Native differential laws and
integrator are preserved; only Id/Iq algebraic constraints are switched to zero.
"""
from pathlib import Path
import argparse,math
import numpy as np
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint
from benchmarks.andes_joint_motor_reference import native_reference,scope
from benchmarks.andes_joint_double_cage import sample_native,validate_event_samples
from benchmarks.andes_motor_reference import LIMITS

NAMES=('loaded_hold','loaded_restart','cold_restart','sag_restart','saturated_restart','frequency_base_restart')
STEPS=(.0005,.00025,.000125,.0000625)


def example(name):
    if name not in NAMES:raise ValueError('unknown adapted terminal case')
    return StudySpec.model_validate_json(Path('cases/studies/andes_terminal_'+name+'.json').read_text())


def coast_check(spec,native):
    """Exact native two-pole open-circuit solution, including fan slowdown phase."""
    m=spec.coupled.motors[0];params=native['mapping']['native_parameters'];wb=2*np.pi*spec.network.frequency_hz;xm=m.magnetizing_x_pu
    rate1=wb*params['rotor_r_pu']/(params['rotor_x_pu']+xm)
    rate2=wb*params['rotor2_r_pu']/(params['rotor2_x_pu']+params['rotor_x_pu']*xm/(params['rotor_x_pu']+xm))
    connected=np.array(native['terminal_connected'],dtype=bool);time=np.array(native['time_s']);states=native['native_motor_states']
    speed=1-np.array(states['slip']);e1=np.array(states['e1q'])-1j*np.array(states['e1d']);e2=np.array(states['e2q'])-1j*np.array(states['e2d'])
    intervals=[];starts=np.flatnonzero(~connected & np.r_[True,connected[:-1]])
    for start in starts:
        after=np.flatnonzero(connected[start:]);end=start+after[0] if len(after) else len(time)
        dt=time[start:end]-time[start];a=m.fan_torque_pu/(2*m.inertia_s);w0=speed[start]
        expected_speed=w0/(1+a*w0*dt)
        speed_integral=np.log1p(a*w0*dt)/a if a else w0*dt
        phase=np.exp(-1j*wb*(dt-speed_integral))
        expected1=e1[start]*np.exp(-rate1*dt)*phase
        expected2=(e2[start]*np.exp(-rate2*dt)+e1[start]*(np.exp(-rate1*dt)-np.exp(-rate2*dt)))*phase
        intervals.append(dict(from_s=float(time[start]),to_s=float(time[end-1]),samples=int(end-start),
            speed_error_pu=float(max(abs(speed[start:end]-expected_speed))),e1_error_pu=float(max(abs(e1[start:end]-expected1))),e2_error_pu=float(max(abs(e2[start:end]-expected2))),
            speed_change_pu=float(speed[end-1]-speed[start])))
    off=~connected;keys=[m.id+'.'+k for k in ['current_pu','te_pu','p_w','q_var']]
    maxima={k:float(max(abs(np.array(native['channels'][k])[off]))) if np.any(off) else 0. for k in keys}
    boundary_passed=maxima[keys[0]]<=1e-8 and maxima[keys[1]]<=1e-8 and max(maxima[keys[2]],maxima[keys[3]])<=1e-3
    exact_passed=all(i['speed_error_pu']<=1e-7 and i['e1_error_pu']<=1e-3 and i['e2_error_pu']<=1e-3 for i in intervals)
    expected_open=any(e.kind=='motor_open' for e in spec.coupled.events)
    return dict(passed=bool(boundary_passed and exact_passed and bool(intervals)==expected_open),open_boundary_passed=bool(boundary_passed),intervals=intervals,open_channel_maxima=maxima,exact_law_limits=dict(speed_pu=1e-7,e1_pu=1e-3,e2_pu=1e-3))


def compare(spec,steps=STEPS,out=None):
    scope(spec,True)
    if len(steps)<2 or list(steps)!=sorted(set(steps),reverse=True) or any(not math.isfinite(h) or not 1e-5<=h<=.01 for h in steps):raise ValueError('strictly decreasing adapted native steps required')
    actual=run_study(spec,isolate=True,wall_timeout_s=60.)
    if not actual.get('completed'):raise ValueError('production joint restart incomplete')
    validate_event_samples(actual,spec.coupled)
    m=spec.coupled.motors[0];g=spec.network.generators[0]
    limits={m.id+'.'+k:v for k,v in LIMITS.items()}
    limits.update({g.id+'.'+k:v for k,v in dict(delta=1e-4,omega=1e-5,e1q=1e-3,e1d=1e-3,e2d=1e-3,e2q=1e-3,p_w=1000.,q_var=1000.).items()})
    limits.update({g.id+'.TGOV1.valve_pu':1e-4,g.id+'.SEXS.field_pu':1e-3})
    if out is not None:
        out=Path(out);write_json(out/'study.json',spec.model_dump(mode='json'));write_json(out/'production.json',actual)
    rows=[]
    for step in steps:
        native=native_reference(spec,step,terminal_adapter=True)
        if not native.get('completed'):
            rows.append(dict(step_s=step,passed=False,failure=native));continue
        validate_event_samples(native,spec.coupled)
        if set(native['channels'])!=set(limits) or not native.get('stock_generated_cache_unchanged') or not native['terminal_adaptation']['differential_equations_unchanged']:raise ValueError('adapted native inventory/law/cache mismatch')
        errors={k:float(max(abs(sample_native(native,actual['time_s'],actual['sample_side'],k)-actual['channels'][k]))) for k in limits}
        coast=coast_check(spec,native)
        rows.append(dict(step_s=step,errors=errors,passed=bool(all(np.isfinite(errors[k]) and errors[k]<=v for k,v in limits.items()) and coast['passed']),coast=coast,
            initial_motor_rhs=native['initial_motor_rhs'],initial_nonmotor_rhs=native['initial_nonmotor_rhs'],initial_algebraic_residual_pu=native['initial_algebraic_residual_pu']))
        if out is not None:write_json(out/f'andes-adapted-{step}.json',native)
    stationary=not spec.coupled.events
    all_completed=all('errors' in row for row in rows)
    convergence=all_completed and (stationary or all(rows[-1]['errors'][k]<rows[0]['errors'][k] for k in [m.id+'.speed_pu',g.id+'.delta']))
    result=dict(case_id=spec.id,passed=bool(rows[-1]['passed'] and convergence),steps=rows,finest_limits=limits,convergence_passed=convergence,
        convergence_protocol='Stationary hold checks initialization; transient fine speed and generator angle errors must decrease relative to coarse',
        adapted_terminal_reference=True,stock_black_box_coast_qualified=False,field_validated=False,industrial_profile_qualified=False)
    if out is not None:
        result['artifacts_sha256']={p.name:sha256_file(p) for p in sorted(out.glob('*.json')) if p.name!='comparison.json'}
        write_json(out/'comparison.json',result)
    return result


def closed_equivalence(out):
    from benchmarks.andes_joint_double_cage import example as cold
    s=cold('cold');s.coupled.duration_s=.2;step=.00025
    stock=native_reference(s,step);adapted=native_reference(s,step,terminal_adapter=True)
    if not stock.get('completed') or not adapted.get('completed'):raise ValueError('closed equivalence native failure')
    if stock['time_s']!=adapted['time_s'] or stock['sample_side']!=adapted['sample_side']:raise ValueError('closed equivalence clock mismatch')
    errors={k:float(max(abs(np.array(v)-adapted['channels'][k]))) for k,v in stock['channels'].items()}
    limits={k:1e-3 if k.endswith(('.p_w','.q_var')) else 1e-9 for k in errors}
    r=dict(passed=all(errors[k]<=v for k,v in limits.items()),maximum_errors=errors,limits=limits,step_s=step)
    write_json(out/'stock.json',stock);write_json(out/'adapted.json',adapted);write_json(out/'comparison.json',r)
    return r


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    # Never leave an earlier successful summary after a failed re-run.
    write_json(a.out/'summary.json',dict(passed=False,status='running'))
    rows=[]
    try:
        equivalence=closed_equivalence(a.out/'closed-equivalence')
        for name in NAMES:
            r=compare(example(name),out=a.out/name);rows.append(r);print(name,r['passed'],flush=True)
        result=dict(passed=bool(equivalence['passed'] and all(r['passed'] for r in rows)),cases=rows,closed_equivalence=equivalence,code_sha256=package_fingerprint(),steps_s=STEPS,
            source_sha256={str(p):sha256_file(p) for p in map(Path,[__file__,'benchmarks/andes_joint_motor_reference.py','benchmarks/andes_motor_terminal.py','benchmarks/andes_joint_double_cage.py','benchmarks/andes_motor_reference.py','benchmarks/double_cage_andes_limits.py'])},
            case_files_sha256={str(p):sha256_file(p) for p in [Path('cases/studies/andes_terminal_'+n+'.json') for n in NAMES]},
            reference='Native Motor5 differential laws/Jacobian/trapezoidal solver plus explicitly adapted open-stator algebraic boundary, independent native loaded initialization and exact fan/two-pole coast solution',
            scope='Two-node balanced common motor/GENROU bus, TGOV1/SEXS, prescribed motor open/close and voltage steps, healthy fan motor, cold or loaded start; no automatic contactor/thermal/PSS/EMT',
            adapted_terminal_reference=True,stock_black_box_coast_qualified=False,field_validated=False,industrial_profile_qualified=False,competitive_superiority_proven=False)
    except Exception as exc:
        write_json(a.out/'summary.json',dict(passed=False,status='failed',error=str(exc)[:2000],cases=rows));raise
    write_json(a.out/'summary.json',result)
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
