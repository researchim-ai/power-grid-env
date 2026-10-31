"""Exact open-stator coast plus independent SI reclose trajectories.

Coast is conditional on the state at opening; the complete trajectory is also
checked against the separate physical SI rotor-flux circuit and DOP853 solver.
No native ANDES coasting equivalence or measured validation is claimed.
"""
from pathlib import Path
import argparse
import math
import numpy as np
from benchmarks.induction_reference import rotor_flux_group_reference
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json,sha256_file

CASES=['short','long','viscous','no_load']
KEYS=['speed_pu','e_real_pu','e_imag_pu','current_pu','bus_voltage_pu','p_w','q_var']
LIMITS=dict(zip(KEYS,[2e-5,2e-5,2e-5,1e-4,1e-5,100.,100.]))


def coast(motor,frequency_hz,times,speed0,emf0):
    """Exact solution at I=0 for nonnegative speed and fan or viscous load."""
    t=np.asarray(times,dtype=float)
    if np.any(t<0) or speed0<0:raise ValueError('nonnegative coast time and speed required')
    wb=2*math.pi*frequency_hz
    # Rotor self-inductance / resistance, derived directly from passport X/R.
    tau=(motor.rotor_x_pu+motor.magnetizing_x_pu)/(wb*motor.rotor_r_pu)
    if motor.mechanical_load is None:
        rate=motor.fan_torque_pu/(2*motor.inertia_s)
        speed=speed0/(1+rate*speed0*t)
        integral=np.log1p(rate*speed0*t)/rate if rate else speed0*t
    else:
        law=motor.mechanical_load
        if law.quadratic_torque_pu or law.friction_torque_pu:raise ValueError('exact reference only supports pure viscous or fan load')
        rate=law.viscous_torque_pu/(2*motor.inertia_s)
        speed=speed0*np.exp(-rate*t)
        integral=-speed0*np.expm1(-rate*t)/rate if rate else speed0*t
    emf=emf0*np.exp(-t/tau-1j*wb*(t-integral))
    return speed,emf


def compare(spec,out=None):
    if spec.induction.motors[0].model!='single_cage':raise ValueError('single-exponential coast reference cannot discard a second cage')
    if len(spec.induction.motors)!=1 or spec.induction.motors[0].thermal or spec.induction.motors[0].contactor:raise ValueError('one nonthermal motor required')
    commands=spec.induction.events
    if len(commands)!=2 or [e.kind for e in commands]!=['motor_open','motor_close']:raise ValueError('one open-close interval required')
    report=run_study(spec,isolate=True,wall_timeout_s=60.)
    if not report.get('completed'):raise ValueError('incomplete production solve')
    motor=spec.induction.motors[0];prefix=motor.id+'.';times=np.array(report['time_s']);sides=np.array(report['sample_side'])
    opened,closed=[e.time_s for e in commands]
    indices=np.flatnonzero(((times>opened)&(times<closed))|((times==opened)&(sides=='post_event'))|((times==closed)&(sides=='pre_event')))
    if len(indices)<3:raise ValueError('insufficient coast samples')
    initial=indices[0];channels=report['channels']
    speed,emf=coast(motor,spec.network.frequency_hz,times[indices]-opened,channels[prefix+'speed_pu'][initial],complex(channels[prefix+'e_real_pu'][initial],channels[prefix+'e_imag_pu'][initial]))
    exact={'speed_pu':speed,'e_real_pu':emf.real,'e_imag_pu':emf.imag,'terminal_voltage_pu':abs(emf),'current_pu':np.zeros(len(indices))}
    coast_errors={k:float(max(abs(np.array(channels[prefix+k])[indices]-v))) for k,v in exact.items()}
    reference=rotor_flux_group_reference(spec,report)[motor.id]
    errors={k:float(max(abs(np.array(channels[prefix+k])-reference[:,j]))) for j,k in enumerate(KEYS)}
    result=dict(passed=all(v<=2e-6 for v in coast_errors.values()) and all(errors[k]<=v for k,v in LIMITS.items()),code_sha256=package_fingerprint(),coast_errors=coast_errors,coast_limit_pu=2e-6,full_trajectory_errors=errors,full_trajectory_limits=LIMITS,
        coast_scope='Exact open-stator solution conditioned on opening state; not an independent initialization test',full_trajectory_reference='Independent physical SI circuit, rotor flux, DOP853, including equilibrium and both event sides',reclose_diagnostics=[e for e in report['event_log'] if e['kind']=='motor_close'],field_validated=False,industrial_profile_qualified=False)
    if out is not None:
        out=Path(out);write_json(out/'production.json',report);write_json(out/'si-reference.json',dict(time_s=report['time_s'],sample_side=report['sample_side'],channels=dict(zip(KEYS,reference.T.tolist()))));write_json(out/'exact-coast.json',dict(time_s=times[indices].tolist(),channels={k:v.tolist() for k,v in exact.items()}));write_json(out/'comparison.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args();rows=[]
    for name in CASES:
        path=Path('cases/studies/motor_coast_'+name+'.json');s=StudySpec.model_validate_json(path.read_text());r=compare(s,a.out/name);r.update(case=str(path),case_sha256=sha256_file(path));rows.append(r)
    result=dict(passed=all(r['passed'] for r in rows),cases=rows,code_sha256=package_fingerprint(),reference_source_sha256=sha256_file(Path(__file__)),si_source_sha256=sha256_file(Path('benchmarks/induction_reference.py')),field_validated=False,industrial_profile_qualified=False)
    write_json(a.out/'summary.json',result);print([(r['case'],r['passed'],r['coast_errors']) for r in rows])
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
