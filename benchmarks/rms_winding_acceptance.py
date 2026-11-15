"""RMS electrothermal comparisons; fixed heat-power gates in watts."""
from benchmarks.loaded_protection_winding_acceptance import compare as hybrid_compare


def compare(reference,report):
    result=hybrid_compare(reference,report)
    if 'errors' not in result:return result
    for key in result['limits']:
        if key.endswith(('_loss_w','_heat_w')):result['limits'][key]=.003
    result['passed']=not result['missing_channels'] and result['event_identities_match'] and result['maximum_event_error_s']<=result['event_tolerance_s'] and all(v<=result['limits'][k] for k,v in result['errors'].items())
    return result


def producers():
    from pathlib import Path
    from power_grid_env.study_artifacts import digest
    return {p:digest(Path(p).read_bytes()) for p in ['benchmarks/rms_winding_reference.py','benchmarks/rms_winding_acceptance.py',
        'benchmarks/rms_winding_cases.py','benchmarks/loaded_protection_winding_acceptance.py','benchmarks/motor_only_loaded_reference.py']}


def execute(args):
    import copy
    from pathlib import Path
    from power_grid_env.study_artifacts import encoded,seal,verify
    from power_grid_env.study_spec import StudySpec,run_study
    from benchmarks.rms_winding_reference import ElectrothermalReference
    job,out=args;root=Path(out);root.mkdir(parents=True,exist_ok=False)
    reference=ElectrothermalReference(job['study']).run(max_step=.002)
    refined=ElectrothermalReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)
    rows=[]
    for method in ['radau','network_dae']:
        for grid in [0,1]:
            study=copy.deepcopy(job['study']);study['coupled'].update(integration_method=method,step_s=.002/2**grid)
            spec=StudySpec.model_validate(study);report=run_study(spec)
            checks=[compare(r,report) for r in [reference,refined]]
            row=dict(method=method,grid=grid,passed=all(c['passed'] for c in checks),checks=[{k:v for k,v in c.items() if k!='values'} for c in checks])
            child=root/(method+'-'+str(grid));child.mkdir()
            for name,value in [('study',spec.model_dump(mode='json')),('trace',report),('comparison',checks)]:
                (child/(name+'.json')).write_bytes(encoded(value))
            seal(child);rows.append(row)
    from benchmarks.loaded_protection_winding_acceptance import event_key
    same=[event_key(e) for e in reference.events]==[event_key(e) for e in refined.events]
    event_error=max(abs(a['time_s']-b['time_s']) for a,b in zip(reference.events,refined.events)) if same else None
    times=[i*.001 for i in range(601) if all(abs(i*.001-e['time_s'])>2e-7 for e in reference.events+refined.events)]
    refinement={k:max(abs(reference.sample(t)[k]-refined.sample(t)[k]) for t in times) for k in reference.sample(0)}
    for name,r in [('reference',reference),('refined-reference',refined)]:
        values=[dict(time_s=t,channels=r.sample(t)) for t in times]
        (root/(name+'.json')).write_bytes(encoded(dict(events=r.events,initialization=r.initial,regular_values=values)))
    row=dict(**{k:v for k,v in job.items() if k!='study'},passed=all(r['passed'] for r in rows) and same and event_error<1e-8 and all(v<(1e-4 if k.endswith(('_loss_w','_heat_w','.current_a','.measurement')) else 1e-7) for k,v in refinement.items()),
        rows=rows,refinement_event_error_s=event_error,refinement_errors=refinement)
    (root/'summary.json').write_bytes(encoded(row));seal(root)
    print('RMS winding',job['id'],row['passed'],flush=True)
    return dict(**row,manifest_sha256=verify(root))


def qualify(out,workers=4):
    from concurrent.futures import ProcessPoolExecutor,as_completed
    from pathlib import Path
    import copy
    import numpy as np
    from power_grid_env.study_artifacts import read_json,encoded,seal,verify
    from power_grid_env.evidence import package_fingerprint
    from benchmarks.rms_winding_cases import jobs
    root=Path(out);root.mkdir(parents=True,exist_ok=False);fp=package_fingerprint();hashes=producers();declared=list(jobs());rows=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for f in as_completed([pool.submit(execute,(j,str(root/j['id']))) for j in declared]):rows.append(f.result())
    effects=[]
    for j in declared:
        if not j['feedback']:continue
        control=next(k for k in declared if not k['feedback'] and all(k[x]==j[x] for x in ['kind','initial','temperatures']))
        a,b=[read_json(root/k['id']/'network_dae-1/trace.json') for k in [j,control]]
        x,y=copy.deepcopy(j['study']),copy.deepcopy(control['study']);m=x['coupled']['motors'][0];h=m['winding_thermal'];ts,tr=h['initial_temperature_c']
        m['stator_r_pu']*=1+h['alpha_per_c']*(ts-h['reference_c']);m['rotor_r_pu']*=1+h['alpha_per_c']*(tr-h['reference_c']);h['alpha_per_c']=0.
        ids=['m.speed_pu','m.current_pu','m.e_real_pu','m.e_imag_pu','m.stator_temperature_c','m.rotor_temperature_c','m.stator_resistance_pu','m.rotor_resistance_pu']
        initial_error=max(abs(a['channels'][k][0]-b['channels'][k][0]) for k in ids)
        delta=max(abs(np.array(a['channels']['m.current_pu'])-b['channels']['m.current_pu']))
        effects.append(dict(id=j['id'],matched_owner=x==y,initial_channel_error=float(initial_error),current_magnitude_max_difference_pu=float(delta)))
    result=dict(passed=len(rows)==32 and all(r['passed'] for r in rows) and all(e['matched_owner'] and e['initial_channel_error']<1e-9 and e['current_magnitude_max_difference_pu']>1e-7 for e in effects),
        physical_scenarios=32,native_runs=128,independent_trajectories=64,numerical_comparisons=256,code_sha256=fp,producers=hashes,rows=sorted(rows,key=lambda r:r['id']),matched_feedback_effects=effects,
        scope='Authored balanced radial single-cage RMS, algebraic stator,R(T),two physical winding heat nodes,separate I2 replica;loaded electrical equilibrium at fixed initial winding temperatures or cold zero flux.Two initial temperature pairs,primary/CB failure/CT loss and matched no-UV controls;frozen-initial-R controls.Both engines/output grids,independent SI winding/KCL/clocks DOP853/refinement.No thermal equilibrium,EMT energy,OEM/general topology certificate.')
    if package_fingerprint()!=fp or producers()!=hashes:raise ValueError('RMS qualification producer changed')
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=4)
    a=p.parse_args()
    if not 1<=a.workers<=16:raise ValueError('workers must be1..16')
    r=qualify(a.out,a.workers);print(dict(passed=r['passed'],native_runs=r['native_runs']))
    if not r['passed']:raise SystemExit(2)
