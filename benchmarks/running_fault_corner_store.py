"""Finite running-motor protection corners in individually bounded artifacts.

No continuous uncertainty claim. Each corner contains 36 executed studies;
index seals only references, never bypassing the generic artifact byte limits.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from types import SimpleNamespace

from power_grid_env.protection_envelope import ProtectionEnvelope,effective_plans
from power_grid_env.closed_loop_protection import compile_closed_loop
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.industrial_dol import IndustrialDOLCase,assessment
from power_grid_env.running_fault_protection import run as run_fault,verify_clearance
from power_grid_env.relay_clock_audit import audit_clock
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.study_artifacts import encoded,read_json,seal,verify,digest
from benchmarks.running_fault_protection_reference import recipes
from benchmarks.closed_loop_protection_reference import algebra,compare_solvers
from benchmarks.motor_fault_branch_duty import measured

DEPENDENCIES=[__file__,'benchmarks/running_fault_protection_reference.py',
              'benchmarks/closed_loop_protection_reference.py','benchmarks/motor_fault_branch_duty.py']


def producer():
    return dict(code_sha256=package_fingerprint(),sources={Path(p).name:digest(Path(p).read_bytes()) for p in DEPENDENCIES})


def jobs(base):
    for kind,uv,method,grid,study,req in recipes(base):
        if uv:
            yield dict(id=f'fault-{kind}-{method}-grid{grid}',kind='fault',scenario=kind,method=method,grid=grid,
                       study=study.model_dump(mode='json'),requirements=req)
    _,pack,_,_=compile_closed_loop(base)
    for original in pack.cases:
        if original.id.startswith('fault-'):continue
        for method in ['radau','network_dae']:
            for grid in range(3):
                data=original.model_dump(mode='json');c=data['study']['coupled']
                c.update(integration_method=method,step_s=.002/2**grid)
                for m in c['motors']:
                    m['contactor']=dict(sense_node_id=m['node_id'],dropout_voltage_pu=.3,pickup_voltage_pu=.8,
                        dropout_delay_s=.09,pickup_delay_s=.5,minimum_off_time_s=.5,maximum_automatic_restarts=0)
                case=IndustrialDOLCase.model_validate(data)
                yield dict(id=f'normal-{case.id}-{method}-grid{grid}',kind='normal',scenario=case.id,method=method,grid=grid,
                           study=case.study.model_dump(mode='json'),case=case.model_dump(mode='json'))


def normal_result(job,report):
    case=IndustrialDOLCase.model_validate(job['case'])
    if report.get('settings')!=case.study.coupled.model_dump(mode='json') or report.get('code_sha256')!=package_fingerprint() or report.get('network_sha256')!=canonical_hash(case.study.network):
        raise ValueError('normal trace identity differs')
    dol=assessment(case,report)
    clocks={p.id:audit_clock(report,p) for p in case.study.coupled.network_automation.relays}
    no_trip=not any(e['kind']=='network_relay_trip' or e['kind']=='motor_open' and e.get('cause')=='undervoltage_timer' for e in report['event_log'])
    return dict(passed=dol['within_limits'] and no_trip and all(a['passed'] for a in clocks.values()),dol=dol,clocks=clocks,no_nuisance_trip=no_trip)


def verify_job(job,root):
    root=Path(root);before=verify(root)
    if read_json(root/'study.json')!=job['study']:raise ValueError('owner study differs')
    report=read_json(root/'trace.json')
    if job['kind']=='fault':
        result=verify_clearance(root,expected_study=job['study'],expected_requirements=job['requirements'])
    else:
        if read_json(root/'case.json')!=job['case']:raise ValueError('owner normal criteria differ')
        result=normal_result(job,report)
        if result!=read_json(root/'result.json'):raise ValueError('normal result differs')
    oracle=algebra(SimpleNamespace(study=StudySpec.model_validate(job['study'])),report)
    if verify(root)!=before:raise ValueError('study changed during verification')
    return dict(id=job['id'],kind=job['kind'],scenario=job['scenario'],method=job['method'],grid=job['grid'],
                profile_passed=result['passed'],algebra=oracle,passed=result['passed'] and oracle['passed'],manifest_sha256=before)


def execute(args):
    job,path=args;root=Path(path)
    if root.exists():
        return verify_job(job,root)
    if job['kind']=='fault':
        run_fault(job['study'],job['requirements'],root)
    else:
        report=run_study(StudySpec.model_validate(job['study']));result=normal_result(job,report)
        root.mkdir(parents=True,exist_ok=False)
        for name,value in [('study',job['study']),('case',job['case']),('trace',report),('result',result)]:
            (root/(name+'.json')).write_bytes(encoded(value))
        seal(root)
    return verify_job(job,root)


def finish_corner(base,root,rows):
    root=Path(root);expected=list(jobs(base))
    if {r['id'] for r in rows}!={j['id'] for j in expected} or len(rows)!=36:
        raise ValueError('corner does not contain all36 studies')
    pairs=[];duty=[]
    for kind in ['normal','fault']:
        scenarios=sorted({j['scenario'] for j in expected if j['kind']==kind})
        for scenario in scenarios:
            for grid in range(3):
                a=read_json(root/f'{kind}-{scenario}-radau-grid{grid}'/'trace.json')
                b=read_json(root/f'{kind}-{scenario}-network_dae-grid{grid}'/'trace.json')
                pairs.append(dict(kind=kind,scenario=scenario,grid=grid,**compare_solvers(a,b)))
            if kind=='fault':
                for method in ['radau','network_dae']:
                    grids=[]
                    for grid in range(3):
                        child=root/f'fault-{scenario}-{method}-grid{grid}'
                        grids.append(measured(read_json(child/'study.json'),read_json(child/'trace.json')))
                    changes={k:abs(grids[2][k]['i2t_a2s']-grids[1][k]['i2t_a2s'])/max(grids[2][k]['i2t_a2s'],1e-9) for k in grids[2]}
                    duty.append(dict(scenario=scenario,method=method,grids=grids,relative_changes=changes,passed=max(changes.values())<=.002))
    result=dict(passed=all(r['passed'] for r in rows) and all(p['passed'] for p in pairs) and all(d['passed'] for d in duty),
                rows=sorted(rows,key=lambda r:r['id']),solver_pairs=pairs,duty=duty,studies=36,
                code_sha256=package_fingerprint(),base_sha256=digest(encoded(base.model_dump(mode='json'))))
    return result


def index_result(protocol,entries,p):
    return dict(schema='power-grid-env.running-corner-store/v1',protocol_sha256=digest(encoded(protocol.model_dump(mode='json'))),
                producer=p,complete=len(entries)==17,passed=len(entries)==17 and all(e['passed'] for e in entries),
                variants=len(entries),native_runs=36*len(entries),solver_pairs=18*len(entries),
                measured_fault_runs=18*len(entries),duty_fine_pairs=6*len(entries),
                continuous_uncertainty_qualified=False,normative_approval=False,reward=None,
                scope='Nominal plus16 finite relay pickup/TMS corners; six normal/fault scenarios, Radau/DAE, three output meshes; fixed hot cable and nominal source voltage. No continuous uncertainty or physical intersample extinction proof.')


def run_store(protocol,out,workers=4,resume=False):
    e=ProtectionEnvelope.model_validate(protocol);p=producer();root=Path(out)
    if not 1<=workers<=8:raise ValueError('workers must be1..8')
    if root.exists():
        if not resume:raise ValueError('new output required; use explicit resume for immutable completed studies')
        if (root/'index/manifest.json').exists():return verify_store(root,expected_protocol=e)
        if read_json(root/'index/protocol.json')!=e.model_dump(mode='json') or read_json(root/'index/producer.json')!=p:
            raise ValueError('resume owner/producer mismatch')
    else:
        (root/'index').mkdir(parents=True);(root/'studies').mkdir()
        (root/'index/protocol.json').write_bytes(encoded(e.model_dump(mode='json')))
        (root/'index/producer.json').write_bytes(encoded(p))
    entries=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for name,base,recipe in effective_plans(e):
            corner=root/'studies'/name
            if (corner/'manifest.json').exists():
                verify(corner);result=read_json(corner/'result.json')
                if read_json(corner/'recipe.json')!=recipe:raise ValueError('resumed corner recipe differs')
            else:
                corner.mkdir(exist_ok=True);rows=[]
                pending={pool.submit(execute,(j,str(corner/j['id']))):j['id'] for j in jobs(base)}
                for f in as_completed(pending):
                    row=f.result();rows.append(row)
                    if len(rows)%12==0:print(name,len(rows),'/36',flush=True)
                if producer()!=p:raise ValueError('producer changed during execution')
                result=finish_corner(base,corner,rows)
                (corner/'recipe.json').write_bytes(encoded(recipe));(corner/'result.json').write_bytes(encoded(result));seal(corner)
            entries.append(dict(id=name,passed=result['passed'],manifest_sha256=verify(corner)))
            print(name,'passed=',result['passed'],flush=True)
    if producer()!=p:raise ValueError('producer changed before publication')
    result=index_result(e,entries,p)
    (root/'index/entries.json').write_bytes(encoded(entries));(root/'index/result.json').write_bytes(encoded(result));seal(root/'index')
    return result


def verify_store(root,*,expected_protocol):
    root=Path(root);e=ProtectionEnvelope.model_validate(expected_protocol);p=producer()
    if root.is_symlink() or {x.name for x in root.iterdir()}!={'index','studies'}:raise ValueError('unexpected composite store layout')
    if (root/'studies').is_symlink():raise ValueError('symlink study collection forbidden')
    if {x.name for x in (root/'index').iterdir()}!={'protocol.json','producer.json','entries.json','result.json','manifest.json'}:raise ValueError('unexpected index layout')
    before=verify(root/'index')
    if read_json(root/'index/protocol.json')!=e.model_dump(mode='json') or read_json(root/'index/producer.json')!=p:
        raise ValueError('owner protocol/producer mismatch')
    variants=list(effective_plans(e))
    if {x.name for x in (root/'studies').iterdir()}!={v[0] for v in variants}:raise ValueError('corner coverage differs')
    entries=[]
    for name,base,recipe in variants:
        corner=root/'studies'/name;anchor=verify(corner)
        expected=list(jobs(base))
        if {x.name for x in corner.iterdir()}!={j['id'] for j in expected}|{'recipe.json','result.json','manifest.json'}:
            raise ValueError('study coverage/layout differs')
        if read_json(corner/'recipe.json')!=recipe:raise ValueError('corner derivation differs')
        rows=[verify_job(j,corner/j['id']) for j in expected]
        result=finish_corner(base,corner,rows)
        if result!=read_json(corner/'result.json') or verify(corner)!=anchor:raise ValueError('corner result changed or recomputation differs')
        entries.append(dict(id=name,passed=result['passed'],manifest_sha256=anchor))
        print('verified',name,flush=True)
    for entry in entries:
        if verify(root/'studies'/entry['id'])!=entry['manifest_sha256']:raise ValueError('corner changed after audit')
    if producer()!=p:raise ValueError('producer changed during verification')
    result=index_result(e,entries,p)
    if read_json(root/'index/entries.json')!=entries or read_json(root/'index/result.json')!=result or verify(root/'index')!=before:
        raise ValueError('composite index differs')
    return dict(verified=True,index_manifest_sha256=before,**result)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--protocol',required=True);p.add_argument('--out',required=True)
    p.add_argument('--workers',type=int,default=4);p.add_argument('--resume',action='store_true');p.add_argument('--verify',action='store_true')
    a=p.parse_args();e=read_json(a.protocol)
    r=verify_store(a.out,expected_protocol=e) if a.verify else run_store(e,a.out,a.workers,a.resume)
    print(dict(passed=r['passed'],native_runs=r['native_runs']))
    if not r['passed']:raise SystemExit(2)

if __name__=='__main__':main()
