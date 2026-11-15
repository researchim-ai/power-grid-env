"""Declared voltage/temperature samples and a preserved VI .015 rejection.

This is an additional finite boundary sample, not a Cartesian robustness proof
for all relay corners and all interior voltage/temperature values.
"""
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from types import SimpleNamespace

from power_grid_env.protection_envelope import ProtectionEnvelope,effective_plans
from power_grid_env.study_spec import StudySpec
from power_grid_env.running_fault_protection import run,verify_clearance
from power_grid_env.study_artifacts import read_json,encoded,verify,seal,digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.running_fault_protection_reference import recipes
from benchmarks.closed_loop_protection_reference import compare_solvers
from benchmarks.running_fault_island_reference import algebra


def boundary_jobs(protocol,original):
    e=ProtectionEnvelope.model_validate(protocol)
    candidate=e.base.model_dump(mode='json')
    old=ProtectionEnvelope.model_validate(dict(id='rejected-015',base=original))
    # Revisions change only label and backup multiplier; deadlines/physics stay.
    before=old.base.model_dump(mode='json');after=dict(candidate)
    before.pop('id');after.pop('id')
    before['backup']=dict(before['backup']);after['backup']=dict(after['backup'])
    old_tms=before['backup'].pop('time_multiplier');new_tms=after['backup'].pop('time_multiplier')
    if before!=after or (old_tms,new_tms)!=(.015,.0155):
        raise ValueError('only explicit .015 -> .0155 backup TMS revision allowed')
    for label,envelope in [('candidate',e),('original',old)]:
        for corner,base,_ in effective_plans(envelope):
            if corner not in ['corner-1100','corner-0011']:continue
            wanted='primary' if corner=='corner-1100' else 'measurement_loss'
            for scenario,uv,method,grid,study,req in recipes(base):
                if scenario!=wanted or not uv:continue
                for temperature in [20,90]:
                    for factor in [.95,1.05]:
                        if label=='original' and (corner!='corner-1100' or temperature!=90 or factor!=1.05):continue
                        s=study.model_dump(mode='json');n=s['network']
                        # Same copper/AC coefficient as the curated catalog;
                        # transformer impedance is held at passport conditions.
                        resistance=.193*(1+.00393*(temperature-20))*1.05*.1
                        n['lines'][0]['r_ohm']=[[resistance if i==j else 0 for j in range(3)] for i in range(3)]
                        n['sources'][0]['ll_v']*=factor
                        name=f'{label}-{corner}-T{temperature}-V{factor}-{method}-grid{grid}'
                        yield dict(id=name,label=label,corner=corner,temperature_c=temperature,voltage_factor=factor,
                                   method=method,grid=grid,study=StudySpec.model_validate(s).model_dump(mode='json'),requirements=req)


def execute(args):
    job,path=args;root=Path(path)
    if not root.exists():run(job['study'],job['requirements'],root)
    result=verify_clearance(root,expected_study=job['study'],expected_requirements=job['requirements'])
    report=read_json(root/'trace.json');oracle=algebra(SimpleNamespace(study=StudySpec.model_validate(job['study'])),report)
    if job['label']=='original':
        expected=(not result['passed'] and result['checks']['clock_audits'] and result['checks']['prefault_running'] and
                  not result['checks']['no_unexpected_trip'] and result['checks']['fault_extinction_deadline'])
    else:expected=result['passed']
    return dict(**{k:v for k,v in job.items() if k not in ['study','requirements']},passed=expected and oracle['passed'],
                profile_passed=result['passed'],oracle=oracle,manifest_sha256=verify(root),
                max_backup_progress=max(report['channels']['backup-oc.progress']),
                grid_delay_s=None if result['grid_isolation_time_s'] is None else result['grid_isolation_time_s']-3.)


def qualify(protocol,original,out,workers=4):
    root=Path(out);root.mkdir(parents=True,exist_ok=False);p=package_fingerprint()
    jobs=list(boundary_jobs(protocol,original));rows=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(execute,(j,str(root/j['id']))):j for j in jobs}
        for future in as_completed(futures):
            rows.append(future.result())
            if len(rows)%6==0:print('boundary',len(rows),'/54',flush=True)
    pairs=[]
    for j in jobs:
        if j['method']!='network_dae':continue
        a=next(k for k in jobs if k['label']==j['label'] and k['corner']==j['corner'] and k['temperature_c']==j['temperature_c'] and k['voltage_factor']==j['voltage_factor'] and k['grid']==j['grid'] and k['method']=='radau')
        pairs.append(dict(id=j['id'],**compare_solvers(read_json(root/a['id']/'trace.json'),read_json(root/j['id']/'trace.json'))))
    if package_fingerprint()!=p:raise ValueError('runtime changed during boundary qualification')
    result=dict(passed=len(rows)==54 and all(r['passed'] for r in rows) and all(p['passed'] for p in pairs),
                candidate_runs=48,original_negative_runs=6,rows=sorted(rows,key=lambda r:r['id']),solver_pairs=pairs,
                code_sha256=p,producer_sha256=digest(Path(__file__).read_bytes()),
                original_015_accepted=False,continuous_uncertainty_qualified=False,
                scope='Two selected relay corners with primary/measurement-loss faults at20/90C and .95/1.05U,48 candidate studies plus6 original high-voltage negatives. Fixed transformer R/X; not all16 corners at every voltage/temperature or a continuous proof.')
    for name,value in [('protocol',protocol),('original',original),('summary',result)]:
        (root/(name+'.json')).write_bytes(encoded(value))
    seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--protocol',required=True);p.add_argument('--original',required=True);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=4)
    a=p.parse_args();r=qualify(read_json(a.protocol),read_json(a.original),a.out,a.workers);print(dict(passed=r['passed'],candidate_runs=48,original_negative_runs=6))
    if not r['passed']:raise SystemExit(2)
