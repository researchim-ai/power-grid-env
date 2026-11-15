"""Bound stationary thermal roots to independent SI dynamics and native gates."""
import copy
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from benchmarks.rms_equilibrium_cases import jobs,negatives
from benchmarks.rms_equilibrium_reference import StationaryReference
from benchmarks.rms_winding_acceptance import compare
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest

SOURCES=['benchmarks/rms_equilibrium_cases.py','benchmarks/rms_equilibrium_reference.py','benchmarks/rms_equilibrium_acceptance.py',
         'benchmarks/rms_winding_reference.py','benchmarks/rms_winding_acceptance.py','benchmarks/loaded_protection_winding_acceptance.py',
         'benchmarks/motor_only_loaded_reference.py','benchmarks/motor_only_loaded_protection.py','cases/motor-only-loaded/protocol.json']


def producers():return {p:digest(Path(p).read_bytes()) for p in SOURCES}


def execute(args):
    job,path=args;root=Path(path);root.mkdir(parents=True,exist_ok=False)
    if 'reason' in job:
        result=run_study(StudySpec.model_validate(job['study']))
        passed=not result['completed'] and result.get('reason')==job['reason'] and 'time_s' not in result
        for name,value in [('study',job['study']),('trace',result)]:
            (root/(name+'.json')).write_bytes(encoded(value))
        row=dict(id=job['id'],negative=True,passed=passed,reason=result.get('reason'))
    else:
        refs=[StationaryReference(job['study']).run(max_step=.004),StationaryReference(job['study']).run(max_step=.002,rtol=5e-12,atol=5e-13)]
        rows=[]
        for method in ['radau','network_dae']:
            for grid in ([0] if job['kind']=='hold' else [0,1]):
                owner=copy.deepcopy(job['study']);owner['coupled'].update(integration_method=method,step_s=.02 if job['kind']=='hold' else .002/2**grid)
                spec=StudySpec.model_validate(owner);result=run_study(spec);checks=[compare(ref,result) for ref in refs]
                op=result['operating_point'];initial=op['motor_initial']['m'];ref=refs[0].initial
                initial_errors={k:abs(initial[k]-ref[k]) for k in ['speed_pu','current_pu','stator_temperature_c','rotor_temperature_c']}
                a=np.array([complex(*z) for z in op['physical_eigenvalues_per_s']]);b=np.array([complex(*z) for z in ref['eigenvalues_per_s']]);ix,iy=linear_sum_assignment(abs(a[:,None]-b[None,:]));spectrum_error=float(max(abs(a[ix]-b[iy])))
                drift={}
                if job['kind']=='hold':
                    drift={key:float(max(abs(np.array(result['channels']['m.'+key])-result['channels']['m.'+key][0]))) for key in ['e_real_pu','e_imag_pu','speed_pu','thermal_level','stator_temperature_c','rotor_temperature_c']}
                passed=all(c['passed'] for c in checks) and max(initial_errors.values())<1e-6 and spectrum_error<1e-5 and op['checked_dynamic_residual_per_s']<1e-8 and all(v<1e-7 for v in drift.values())
                child=root/(method+'-'+str(grid));child.mkdir()
                for name,value in [('study',spec.model_dump(mode='json')),('trace',result),('comparisons',checks)]:
                    (child/(name+'.json')).write_bytes(encoded(value))
                seal(child)
                rows.append(dict(method=method,grid=grid,passed=bool(passed),checks=[{k:v for k,v in c.items() if k!='values'} for c in checks],initial_errors=initial_errors,spectrum_error_per_s=spectrum_error,hold_drift=drift))
        for name,ref in zip(['reference','refined-reference'],refs):
            (root/(name+'.json')).write_bytes(encoded(dict(events=ref.events,initialization=ref.initial)))
        row=dict(**{k:v for k,v in job.items() if k!='study'},negative=False,passed=all(r['passed'] for r in rows),rows=rows)
    (root/'summary.json').write_bytes(encoded(row));seal(root)
    print('stationary RMS',job['id'],row['passed'],flush=True)
    return dict(**row,manifest_sha256=verify(root))


def qualify(out,workers=4):
    if not 1<=workers<=16:raise ValueError('workers must be1..16')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);fp=package_fingerprint();hashes=producers();declared=list(jobs())+list(negatives());rows=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for f in as_completed([pool.submit(execute,(j,str(root/j['id']))) for j in declared]):rows.append(f.result())
    # Search seed invariance: identical physical passports, roots and spectra.
    pairs=[]
    for j in jobs():
        if j['seed'][0]!=20:continue
        other=next(k for k in jobs() if k['condition']==j['condition'] and k['kind']==j['kind'] and k['seed'][0]==90)
        a,b=[read_json(root/k['id']/'network_dae-0/trace.json') for k in [j,other]]
        errors={key:abs(a['channels']['m.'+key][0]-b['channels']['m.'+key][0]) for key in ['speed_pu','current_pu','e_real_pu','e_imag_pu','stator_temperature_c','rotor_temperature_c']}
        x,y=copy.deepcopy(j['study']),copy.deepcopy(other['study'])
        for p in [x,y]:p['coupled']['motors'][0]['winding_thermal']['initial_temperature_c']=[20.,20.]
        pairs.append(dict(id=j['id'],matched_physics=x==y,initial_errors=errors,passed=x==y and max(errors.values())<1e-7))
    if fp!=package_fingerprint() or hashes!=producers():raise ValueError('stationary qualification producer changed')
    result=dict(passed=len(rows)==52 and all(r['passed'] for r in rows) and all(p['passed'] for p in pairs),physical_scenarios=20,search_seed_configurations=40,
        completed_native_runs=144,initialization_rejections=12,native_launches=156,independent_trajectories=80,independent_comparisons=288,
        code_sha256=fp,producers=hashes,rows=sorted(rows,key=lambda r:r['id']),seed_pairs=pairs,
        scope='Bounded motor-only single-cage simultaneous speed/EMF/network/Ts/Tr/I2 equilibrium.Physical spectrum includes winding heat states.4 electrical endpoints,2 seeds,protection faults and5s holds;6 matched initial rejection causes.Both engines,independent T-circuit/heat root + SI winding/clock dynamics.No global uniqueness/nonexistence,OEM,hybrid-stability or mixed-machine certificate.')
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=4)
    r=qualify(**vars(p.parse_args()));print(dict(passed=r['passed'],native_launches=r['native_launches']))
    if not r['passed']:raise SystemExit(2)
