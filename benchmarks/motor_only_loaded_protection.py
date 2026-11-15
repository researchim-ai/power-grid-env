"""Loaded-start protection at all four network endpoints with matched UV controls.

Native event actions, independent conditional SI algebra and clock replay;
not an independent differential implementation of active protection.
"""
import copy
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from types import SimpleNamespace
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.running_fault_protection import assess
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.running_fault_cartesian import CartesianProtocol,conditions
from benchmarks.running_fault_protection_reference import recipes
from benchmarks.running_fault_island_reference import algebra
from benchmarks.closed_loop_protection_reference import compare_solvers


def jobs(protocol):
    p=CartesianProtocol.model_validate(protocol)
    for condition_id,condition in conditions(p):
        cable=next(c for c in p.catalog.cables if c.id==p.selection.cable_id);a=p.catalog.assumptions
        resistance=cable.dc_r20_ohm_km.value*(1+a.copper_alpha_per_c.value*(condition['conductor_temperature_c']-20))*a.ac_dc_resistance_factor.value*p.brief.installation.length_m/1000
        for thermal in ['passport','equilibrium']:
            for kind,uv,method,grid,study,req in recipes(p.envelope.base):
                if not uv and kind!='primary':continue
                s=study.model_dump(mode='json');c=s['coupled'];n=s['network']
                c.update(duration_s=.6,operating_point=dict(thermal_state=thermal))
                c['faults'][0].update(apply_s=.1,clear_s=.4)
                for event in c['events']:
                    if event['kind']=='relay_measurement_lost':event['time_s']=.05
                n['lines'][0]['r_ohm']=[[resistance if i==j else 0 for j in range(3)] for i in range(3)]
                n['sources'][0]['ll_v']*=condition['source_voltage_factor']
                req=copy.deepcopy(req);req['id']='loaded-'+kind
                req['source']=dict(reference='owner://motor-only-loaded-protection/v1',conditions='Independent high-speed machine/network initialization at t=0; fault .1..4s, extinction within150ms; measured steady thermal policy explicit')
                name=f'{condition_id}-{thermal}-{kind}-'+('uv' if uv else 'no-uv')+f'-{method}-grid{grid}'
                yield dict(id=name,condition_id=condition_id,thermal=thermal,kind=kind,uv=uv,method=method,grid=grid,
                    study=StudySpec.model_validate(s).model_dump(mode='json'),requirements=req)


def execute(args):
    job,path=args;root=Path(path);study=StudySpec.model_validate(job['study']);report=run_study(study)
    result=assess(study,report,job['requirements']);oracle=algebra(SimpleNamespace(study=study),report)
    expected=result['passed'] if job['uv'] else (not result['passed'] and result['checks']['prefault_running'] and result['checks']['clock_audits'] and result['checks']['grid_electrically_isolated'] and not result['checks']['motors_disconnected'])
    root.mkdir(parents=True,exist_ok=False)
    for name,value in [('study',job['study']),('requirements',job['requirements']),('trace',report),('result',result),('algebra',oracle)]:
        (root/(name+'.json')).write_bytes(encoded(value))
    seal(root)
    return dict(**{k:v for k,v in job.items() if k not in ['study','requirements']},passed=expected and oracle['passed'],profile_accepted=result['passed'],manifest_sha256=verify(root))


def qualify(protocol,out,workers=8):
    if not 1<=workers<=16:raise ValueError('workers must be1..16')
    source=package_fingerprint();root=Path(out);root.mkdir(parents=True,exist_ok=False);(root/'index').mkdir()
    declared=list(jobs(protocol));rows=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for f in as_completed([pool.submit(execute,(j,str(root/j['id']))) for j in declared]):
            rows.append(f.result())
            if len(rows)%24==0:print('loaded protected',len(rows),'/192',flush=True)
    pairs=[]
    for j in declared:
        if j['method']!='network_dae':continue
        other=next(k for k in declared if all(k[key]==j[key] for key in ['condition_id','thermal','kind','uv','grid']) and k['method']=='radau')
        pairs.append(dict(id=j['id'],**compare_solvers(read_json(root/other['id']/'trace.json'),read_json(root/j['id']/'trace.json'))))
    matched=True
    for j in declared:
        if j['uv']:continue
        other=next(k for k in declared if k['uv'] and all(k[key]==j[key] for key in ['condition_id','thermal','kind','method','grid']))
        before=copy.deepcopy(j['study']);after=copy.deepcopy(other['study'])
        for m in before['coupled']['motors']+after['coupled']['motors']:m['contactor']=None
        matched &= before==after and j['requirements']==other['requirements']
    if package_fingerprint()!=source:raise ValueError('runtime changed during loaded protection matrix')
    result=dict(passed=len(rows)==192 and matched and all(r['passed'] for r in rows) and all(p['passed'] for p in pairs),
        native_runs=192,accepted_protected_runs=144,matched_no_uv_negative_runs=48,solver_pairs=pairs,rows=sorted(rows,key=lambda r:r['id']),
        matched_requirements_and_physics=bool(matched),code_sha256=source,producer_sha256=digest(Path(__file__).read_bytes()),
        normative_approval=False,independent_active_protection_trajectories=False,
        scope='Explicit loaded machine/network equilibrium, both heat policies and four endpoint conditions.144 protected primary/CB-failure/CT-loss studies plus48 matched primary no-UV failures; conditional SI algebra, clock replay and96 two-solver pairs. No relay tolerance corners or independent active-protection ODE qualification.')
    (root/'index/protocol.json').write_bytes(encoded(protocol));(root/'index/summary.json').write_bytes(encoded(result));seal(root/'index');return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--protocol',required=True);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=8)
    a=p.parse_args();r=qualify(read_json(a.protocol),a.out,a.workers);print(dict(passed=r['passed'],native_runs=192))
    if not r['passed']:raise SystemExit(2)
