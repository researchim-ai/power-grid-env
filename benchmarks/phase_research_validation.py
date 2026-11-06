"""Independent small-circuit equations and empirical, physics-checked surrogate acceptance."""
from pathlib import Path
import argparse,hashlib,json,time
from statistics import median
import numpy as np
from scipy.integrate import solve_ivp

from power_grid_env.phase_research import PhaseStudy,SearchProtocol,run_phase_study,run_search,train_from_search,candidate,evaluate,objective_value,predict_surrogate_batch,query_model
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json
from benchmarks.release_candidate import source_hashes


def run(out):
    out=Path(out)
    if out.exists():raise ValueError('new phase acceptance output required')
    out.mkdir(parents=True);repo=Path(__file__).resolve().parents[1];before=source_hashes(repo);code=package_fingerprint()
    rows=[]
    for name in ['rlc','saturated','four_wire','gfl','gfm','drive','drive_locked','unbalanced_motor']:
        study=PhaseStudy.model_validate(read_json(repo/f'cases/studies/phase_{name}.json'))
        result=run_phase_study(study.model_dump(mode='json'),out/name)
        row=dict(case=name,kcl_max_a=result['kcl_max_a'],energy_error_j=abs(result['energy'][-1]['residual_j']),passed=result['kcl_max_a']<1e-8 and abs(result['energy'][-1]['residual_j'])<1e-4)
        if name in ['rlc','saturated']:
            sat=.02 if name=='saturated' else 0.
            def rate(t,x):
                current,voltage=x;increment=.02+sat/4/np.cosh(np.clip(current/4,-100,100))**2
                return [(np.sqrt(2)*230*np.cos(2*np.pi*50*t)-voltage-current)/increment,(current-voltage/20)/100e-6]
            ref=solve_ivp(rate,(0.,.2),[0.,0.],method='DOP853',rtol=1e-11,atol=1e-12,max_step=.0001,t_eval=result['time_s'])
            np.save(out/(name+'-independent.npy'),ref.y)
            ie=float(np.max(abs(ref.y[0]-result['channels']['line.current_a'])));ve=float(np.max(abs(ref.y[1]-result['channels']['load.voltage_v'])))
            row.update(independent_current_error_a=ie,independent_voltage_error_v=ve);row['passed'] &= ref.success and ie<2e-5 and ve<2e-4
        elif name=='unbalanced_motor':
            a=np.exp(2j*np.pi/3)
            def rate(t,flux):
                ps,pr=flux;ir=(pr-ps)/.023;current=ps/.34-ir
                abc=np.sqrt(2)*np.array([230.,190.,230.])*np.cos(2*np.pi*50*t+np.array([0.,-2*np.pi/3,2*np.pi/3]))
                u=2/3*(abc[0]+a*abc[1]+a*a*abc[2]);return [u-3.7*current,-2.5*ir]
            ref=solve_ivp(rate,(0.,.2),np.zeros(2,dtype=complex),method='DOP853',rtol=1e-11,atol=1e-12,max_step=.0001,t_eval=result['time_s'])
            current=ref.y[0]/.34-(ref.y[1]-ref.y[0])/.023
            actual=np.array(result['channels']['motor.current_alpha_a'])+1j*np.array(result['channels']['motor.current_beta_a'])
            error=float(np.max(abs(current-actual)));row.update(independent_negative_sequence_current_error_a=error);row['passed'] &= error<2e-5
        elif name=='drive_locked':
            ramp=.05;w=2*np.pi*50
            def rate(t,flux):
                ps,pr=flux;ir=(pr-ps)/.023;current=ps/.34-ir
                angle=w*(t*t/(2*ramp) if t<ramp else t-ramp/2)
                u=np.sqrt(2)*230*min(t/ramp,1)*np.exp(1j*angle)
                return [u-3.7*current,-2.5*ir]
            ref=solve_ivp(rate,(0.,.2),np.zeros(2,dtype=complex),method='DOP853',rtol=1e-11,atol=1e-12,max_step=.0001,t_eval=result['time_s'])
            current=ref.y[0]/.34-(ref.y[1]-ref.y[0])/.023
            error=float(np.max(abs(abs(current)-result['channels']['vfd.motor_current_peak_a'])))
            row.update(independent_motor_current_error_a=error);row['passed'] &= error<2e-5
        elif name=='drive':
            row['passed'] &= result['channels']['vfd.motor_speed_rad_s'][-1]>10. and min(result['channels']['vfd.dc_voltage_v'])>500.
        elif name=='gfl':
            t=np.array(result['time_s']);expected=np.hypot(6900.,1380.)/(3*230)*np.sqrt(2)*(1-np.exp(-t/.001))
            error=float(np.max(abs(expected-result['channels']['der.current_vector_peak_a'])));row.update(closed_current_error_a=error);row['passed'] &= error<2e-5
        elif name=='gfm':
            expected=230*20/(22+1j*2*np.pi*50*.001*2)
            error=abs(complex(*result['final_phasors']['a.voltage_v'])-expected);row.update(closed_voltage_error_v=error);row['passed'] &= error<1e-4
        rows.append(row)
    protocol=SearchProtocol.model_validate(read_json(repo/'cases/studies/phase_search.json'))
    partial=run_search(protocol.model_dump(mode='json'),out/'search-part',max_points=7)
    resumed=run_search(protocol.model_dump(mode='json'),out/'search-resumed',resume_from=out/'search-part')
    complete=run_search(protocol.model_dump(mode='json'),out/'search-full')
    points=read_json(out/'search-full/points.json');errors=[]
    for row in points:
        rload,rline=row['values'];w=2*np.pi*50
        exact=abs(230/(1+(rline+1j*w*.02)*(1/rload+1j*w*100e-6)))
        errors.append(abs(row['objective']-exact))
    model=train_from_search(out/'search-full',out/'model',maximum_holdout_error=1.)
    queries=np.array([[r,g] for r in np.linspace(18.,22.,15) for g in np.linspace(.9,1.1,15)])
    timings=[];measured=[];prediction=None
    for repeat in range(3):
        # Alternate order; both timed paths consume the exact same coordinate batch.
        def screen():
            t=time.perf_counter();pred=predict_surrogate_batch(model,queries);return time.perf_counter()-t,pred
        def physics():
            t=time.perf_counter();values=[objective_value(evaluate(candidate(protocol,p)),protocol.objective) for p in queries];return time.perf_counter()-t,values
        if repeat%2:physical_s,values=physics();screen_s,pred=screen()
        else:screen_s,pred=screen();physical_s,values=physics()
        timings.append(dict(physical_s=physical_s,screen_s=screen_s));measured=values;prediction=pred['predictions']
    query_error=float(np.max(abs(np.array(measured)-prediction)))
    verification=query_model(out/'model',[20.,1.],out/'verified-query')
    summary=dict(passed=all(r['passed'] for r in rows) and resumed['conclusive'] and complete['conclusive'] and read_json(out/'search-full/points.json')==read_json(out/'search-resumed/points.json') and max(errors)<1e-9 and model['accepted'] and query_error<1. and verification['accepted'] and before==source_hashes(repo) and code==package_fingerprint(),
        code_sha256=code,cases=rows,grid_points=len(points),independent_grid_error_v=max(errors),critical_case_physically_replayed=complete['critical_case_physically_replayed'],
        continuation_identical=read_json(out/'search-full/points.json')==read_json(out/'search-resumed/points.json'),
        holdout_error_v=model['holdout_max_absolute_error'],verification_queries=len(queries),query_max_error_v=query_error,
        screening_speedup=median(t['physical_s'] for t in timings)/median(t['screen_s'] for t in timings),timings=timings,
        timing_scope='batch screening only; physically verifying every prediction adds the forward-solve time',
        field_validated=False,global_optimum_claimed=False,global_error_bound_claimed=False,policy='local_only_no_GitHub_no_CI')
    (out/'acceptance.json').write_text(json.dumps(summary,indent=2)+'\n');(out/'sources.json').write_text(json.dumps(before,indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps({p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()},indent=2)+'\n')
    return summary


def main():
    p=argparse.ArgumentParser(__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args();r=run(a.out)
    print(json.dumps({k:r[k] for k in ['passed','code_sha256','holdout_error_v','query_max_error_v','screening_speedup']}));raise SystemExit(0 if r['passed'] else 2)

if __name__=='__main__':main()
