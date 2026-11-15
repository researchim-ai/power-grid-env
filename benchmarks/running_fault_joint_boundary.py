"""Joint primary/backup revision after a cold/high-voltage counterexample."""
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
import copy

from power_grid_env.protection_envelope import ProtectionEnvelope
from power_grid_env.study_artifacts import encoded,read_json,seal,verify,digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.running_fault_boundary_reference import boundary_jobs,execute
from benchmarks.closed_loop_protection_reference import compare_solvers


def joint_jobs(previous,revised,original):
    before=ProtectionEnvelope.model_validate(previous).base.model_dump(mode='json')
    after=ProtectionEnvelope.model_validate(revised).base.model_dump(mode='json')
    bt,at=before['primary']['time_multiplier'],after['primary']['time_multiplier']
    for data in [before,after]:
        data.pop('id');data['primary']=dict(data['primary']);data['primary'].pop('time_multiplier')
    if before!=after or (bt,at)!=(.01,.009):
        raise ValueError('joint revision must change only label and primary TMS .01 -> .009')
    for job in boundary_jobs(previous,original):
        if job['label']!='candidate':continue
        if job['corner']=='corner-1100' and job['temperature_c']==20 and job['voltage_factor']==1.05:
            negative=copy.deepcopy(job);negative['id']=negative['id'].replace('candidate-','original-',1);negative['label']='original'
            yield negative
        candidate=copy.deepcopy(job)
        relay=next(r for r in candidate['study']['coupled']['network_automation']['relays'] if r['id']=='primary-oc')
        relay['time_multiplier_s']*=at/bt
        yield candidate


def qualify(previous,revised,original,out,workers=4):
    jobs=list(joint_jobs(previous,revised,original));root=Path(out);root.mkdir(parents=True,exist_ok=False)
    p=package_fingerprint();rows=[]
    sources=[__file__,'benchmarks/running_fault_boundary_reference.py','benchmarks/running_fault_island_reference.py']
    producer={Path(f).name:digest(Path(f).read_bytes()) for f in sources}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(execute,(j,str(root/j['id']))) for j in jobs]
        for future in as_completed(futures):
            rows.append(future.result())
            if len(rows)%6==0:print('joint boundary',len(rows),'/54',flush=True)
    pairs=[]
    for job in jobs:
        if job['method']!='network_dae':continue
        other=next(j for j in jobs if all(j[k]==job[k] for k in ['label','corner','temperature_c','voltage_factor','grid']) and j['method']=='radau')
        pairs.append(dict(id=job['id'],**compare_solvers(read_json(root/other['id']/'trace.json'),read_json(root/job['id']/'trace.json'))))
    if p!=package_fingerprint() or producer!={Path(f).name:digest(Path(f).read_bytes()) for f in sources}:
        raise ValueError('producer changed during joint boundary study')
    result=dict(passed=len(rows)==54 and all(r['passed'] for r in rows) and all(r['passed'] for r in pairs),
                candidate_runs=48,previous_negative_runs=6,rows=sorted(rows,key=lambda r:r['id']),solver_pairs=pairs,
                code_sha256=p,producer=producer,previous_01_0155_accepted_at_cold_high_voltage=False,
                all_corners_at_every_boundary_qualified=False,continuous_uncertainty_qualified=False,
                scope='Revised primary .009, backup .0155; two selected corners at20/90C and .95/1.05U,48 positives; six matched .01/.0155 cold/high-voltage negatives. No full uncertainty rectangle proof.')
    for name,value in [('previous',previous),('revised',revised),('original',original),('summary',result)]:
        (root/(name+'.json')).write_bytes(encoded(value))
    seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['previous','revised','original','out']:p.add_argument('--'+key,required=True)
    p.add_argument('--workers',type=int,default=4);a=vars(p.parse_args())
    for key in ['previous','revised','original']:a[key]=read_json(a[key])
    r=qualify(**a);print(dict(passed=r['passed'],candidate_runs=48,previous_negative_runs=6))
    if not r['passed']:raise SystemExit(2)
