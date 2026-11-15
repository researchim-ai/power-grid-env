"""Independent two-rotor electrothermal trajectory and stationary-spectrum gate."""
import copy
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
import numpy as np
from scipy.optimize import linear_sum_assignment

from benchmarks.two_cage_winding_cases import jobs,negatives
from benchmarks.two_cage_winding_reference import TwoCageReference
from benchmarks.rms_winding_acceptance import compare
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest
from power_grid_env.evidence import package_fingerprint

SOURCES=['benchmarks/two_cage_winding_cases.py','benchmarks/two_cage_winding_reference.py','benchmarks/two_cage_winding_acceptance.py',
         'benchmarks/rms_winding_reference.py','benchmarks/rms_winding_acceptance.py','benchmarks/loaded_protection_winding_acceptance.py',
         'benchmarks/motor_only_loaded_reference.py']

def producers():return {p:digest(Path(p).read_bytes()) for p in SOURCES}


def assess(job,owner,result,refs):
    checks=[compare(r,result) for r in refs];stationary=job['mode']=='equilibrium';op=result['operating_point'];spectral=None
    if stationary:
        a=np.array([complex(*x) for x in op['physical_eigenvalues_per_s']]);b=np.array([complex(*x) for x in refs[0].initial['eigenvalues_per_s']]);i,j=linear_sum_assignment(abs(a[:,None]-b[None,:]));spectral=float(max(abs(a[i]-b[j])))
    ch=result['channels'];errors=[]
    for index in range(len(result['time_s'])):
        errors.append(abs(ch['m.rotor_copper_loss_w'][index]-ch['m.rotor1_copper_loss_w'][index]-ch['m.rotor2_copper_loss_w'][index]))
    drift={}
    if stationary and job['kind']=='hold':
        drift={k:float(max(abs(np.array(ch['m.'+k])-ch['m.'+k][0]))) for k in ['speed_pu','e_real_pu','e_imag_pu','cage1_e_real_pu','stator_temperature_c','rotor_temperature_c','thermal_level']}
    ps,pr=ch['m.stator_copper_loss_w'][0],ch['m.rotor_copper_loss_w'][0];m=owner['coupled']['motors'][0];h=m['winding_thermal']
    power_error=None
    if stationary:
        power_error=max(abs(ch['m.p_w'][0]-ps-pr-m['rated_va']*ch['m.load_torque_pu'][0]*ch['m.speed_pu'][0]),
            abs(ps+pr-h['stator_ambient_w_per_k']*(ch['m.stator_temperature_c'][0]-h['ambient_c'])-h['rotor_ambient_w_per_k']*(ch['m.rotor_temperature_c'][0]-h['ambient_c'])))
    return dict(passed=bool(all(c['passed'] for c in checks) and max(errors)<1e-8 and (spectral is None or spectral<2e-5) and all(v<1e-7 for v in drift.values()) and (power_error is None or power_error<1e-5)),
                checks=[{k:v for k,v in c.items() if k!='values'} for c in checks],spectrum_error_per_s=spectral,maximum_cage_loss_sum_error_w=float(max(errors)),
                hold_drift=drift,stationary_power_error_w=float(power_error) if power_error is not None else None),checks


def execute(args):
    j,path=args;path=Path(path);path.mkdir(parents=True,exist_ok=False)
    if 'reason' in j:
        r=run_study(StudySpec.model_validate(j['study']));row=dict(id=j['id'],negative=True,passed=not r['completed'] and r.get('reason')==j['reason'] and 'time_s' not in r,reason=r.get('reason'))
        for name,value in [('study',j['study']),('trace',r)]: (path/(name+'.json')).write_bytes(encoded(value))
    else:
        refs=[TwoCageReference(j['study']).run(max_step=.002),TwoCageReference(j['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)];rows=[]
        for method in ['radau','network_dae']:
            for grid in ([0] if j['kind']=='hold' else [0,1]):
                owner=copy.deepcopy(j['study']);owner['coupled'].update(integration_method=method,step_s=.02 if j['kind']=='hold' else .002/2**grid)
                spec=StudySpec.model_validate(owner);r=run_study(spec);result,checks=assess(j,owner,r,refs)
                child=path/(method+'-'+str(grid));child.mkdir()
                for name,value in [('study',spec.model_dump(mode='json')),('trace',r),('comparisons',checks)]: (child/(name+'.json')).write_bytes(encoded(value))
                seal(child);rows.append(dict(method=method,grid=grid,**result))
        for name,r in zip(['reference','refined-reference'],refs): (path/(name+'.json')).write_bytes(encoded(dict(events=r.events,initialization=r.initial)))
        row=dict(**{k:v for k,v in j.items() if k!='study'},negative=False,passed=all(r['passed'] for r in rows),rows=rows)
    (path/'summary.json').write_bytes(encoded(row));seal(path);print('two cage',j['id'],row['passed'],flush=True)
    return dict(**row,manifest_sha256=verify(path))


def permutation_pairs(source):
    source=Path(source);rows=[]
    for j in jobs():
        if not j['permuted']:continue
        old=j['id'].removesuffix('-permuted')
        a,b=[read_json(source/id/'network_dae-1/trace.json') for id in [old,j['id']]]
        identity=lambda e:(e['kind'],e['equipment_id'],e.get('outcome'))
        events_ok=[identity(e) for e in a['event_log']]==[identity(e) for e in b['event_log']]
        pairs=[];time_error=0.;coverage=True
        for side in ['initial','regular','pre_event','post_event']:
            left=[i for i,v in enumerate(a['sample_side']) if v==side];right=[i for i,v in enumerate(b['sample_side']) if v==side]
            coverage &= len(left)==len(right)
            for i,k in zip(left,right):
                time_error=max(time_error,abs(a['time_s'][i]-b['time_s'][k]));pairs.append((i,k))
        mapping={k:k for k in ['speed_pu','e_real_pu','e_imag_pu','current_pu','stator_temperature_c','rotor_temperature_c','rotor_copper_loss_w','stator_copper_loss_w']}
        for x,y in [(1,2),(2,1)]:
            for suffix in ['current_pu','resistance_pu','copper_loss_w']:mapping[f'rotor{x}_'+suffix]=f'rotor{y}_'+suffix
            for suffix in ['real_pu','imag_pu']:mapping[f'cage{x}_e_'+suffix]=f'cage{y}_e_'+suffix
        errors={key:max(abs(a['channels']['m.'+key][i]-b['channels']['m.'+target][k]) for i,k in pairs) for key,target in mapping.items()}
        limits={key:.003 if key.endswith('_w') else 2e-6 for key in errors}
        rows.append(dict(id=j['id'],passed=bool(coverage and len(pairs)==len(a['time_s']) and events_ok and time_error<1e-7 and all(v<=limits[k] for k,v in errors.items())),
                         event_identities_match=events_ok,maximum_sample_time_difference_s=time_error,errors=errors,limits=limits))
    return rows


def qualify(out,workers=4):
    if not 1<=workers<=16:raise ValueError('workers must be1..16')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);fp=package_fingerprint();hashes=producers();declared=list(jobs())+list(negatives());rows=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(execute,(j,str(root/j['id']))) for j in declared]):rows.append(future.result())
    pairs=permutation_pairs(root)
    if package_fingerprint()!=fp or producers()!=hashes:raise ValueError('two-cage producer changed')
    s=dict(passed=len(rows)==45 and all(r['passed'] for r in rows) and all(p['passed'] for p in pairs),physical_scenarios=33,completed_native_runs=120,initialization_rejections=12,
        independent_trajectories=66,numerical_comparisons=240,code_sha256=fp,producers=hashes,rows=sorted(rows,key=lambda r:r['id']),permutation_pairs=pairs,
        scope='Single authored radial feeder,two rotor cages,mutual0/.02,cold/fixed-hot/full-stationary,hold/primary/CB failure/CT loss/no-UV and cage-label controls.Two native engines/grids,independent two-SI-flux KCL DOP853/refinement;8-state physical spectra.Common rotor temperature/material coefficient,not per-cage hotspot or arbitrary multicage/OEM qualification.No mixed synchronous generators.')
    (root/'summary.json').write_bytes(encoded(s));seal(root);return s


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=4)
    r=qualify(**vars(p.parse_args()));print(dict(passed=r['passed'],completed_native_runs=r['completed_native_runs']))
    if not r['passed']:raise SystemExit(2)
