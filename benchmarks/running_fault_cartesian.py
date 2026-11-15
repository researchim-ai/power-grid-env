"""Finite Cartesian cable-temperature/source-voltage/relay-corner studies.

Owner-bound catalog compilation, preserved normal/fault requirements, bounded
immutable study packets, explicit soft deadline and restart of sealed work.
No continuous uncertainty, EMT or independent trajectory claim.
"""
import argparse
import copy
import itertools
import math
import time
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
from pathlib import Path
from types import SimpleNamespace

from pydantic import Field,model_validator
from power_grid_env.electrical_sizing import Contract,Selection
from power_grid_env.equipment_catalog import EquipmentCatalog,CatalogBrief,compile_catalog
from power_grid_env.protection_envelope import ProtectionEnvelope,effective_plans
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.running_fault_protection import assess
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.study_artifacts import encoded,read_json,seal,verify,digest
from benchmarks.running_fault_corner_store import jobs as base_jobs,normal_result,finish_corner
from benchmarks.running_fault_island_reference import algebra

DEPENDENCIES=[__file__,'benchmarks/running_fault_corner_store.py',
    'benchmarks/running_fault_island_reference.py','benchmarks/running_fault_protection_reference.py',
    'benchmarks/closed_loop_protection_reference.py','benchmarks/motor_fault_branch_duty.py']


class CartesianProtocol(Contract):
    id: str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$')
    envelope: ProtectionEnvelope
    catalog: EquipmentCatalog
    brief: CatalogBrief
    selection: Selection
    conductor_temperatures_c: list[float]=Field(min_length=1,max_length=5)
    source_voltage_factors: list[float]=Field(min_length=1,max_length=5)
    maximum_studies: int=Field(default=5000,ge=36,le=20000)
    wall_budget_s: float=Field(default=14400,gt=0,le=43200)

    @model_validator(mode='after')
    def profile(self):
        _,_,selection,task,_=compile_catalog(self.catalog,self.brief,self.selection)
        if self.envelope.base.equipment!=task or self.envelope.base.selection!=selection:
            raise ValueError('owner catalog/brief/selection differ from envelope equipment')
        if {c.id for c in task.mandatory_cases}!={'start','repeated_sag','coast_restart'} or len(task.mandatory_cases)!=3:
            raise ValueError('Cartesian v1 requires the three declared normal modes')
        cable=next(c for c in self.catalog.cables if c.id==selection.cable_id)
        for values,lo,hi,name in [(self.conductor_temperatures_c,20,cable.maximum_conductor_c.value,'temperature'),
                                   (self.source_voltage_factors,.5,1.5,'voltage factor')]:
            if values!=sorted(set(values)) or any(not math.isfinite(v) or v<lo or v>hi for v in values):
                raise ValueError('unique sorted supported '+name+' samples required')
        if len(self.conductor_temperatures_c)*len(self.source_voltage_factors)*17*36>self.maximum_studies:
            raise ValueError('Cartesian study count exceeds owner budget')
        return self


def producer():
    return dict(code_sha256=package_fingerprint(),sources={Path(p).name:digest(Path(p).read_bytes()) for p in DEPENDENCIES})


def conditions(p):
    for temperature,factor in itertools.product(p.conductor_temperatures_c,p.source_voltage_factors):
        name=f'T{temperature}-V{factor}'.replace('.','p')
        yield name,dict(conductor_temperature_c=temperature,source_voltage_factor=factor)


def condition_jobs(p,base,condition):
    cable=next(c for c in p.catalog.cables if c.id==p.selection.cable_id)
    a=p.catalog.assumptions;t=condition['conductor_temperature_c']
    resistance=cable.dc_r20_ohm_km.value*(1+a.copper_alpha_per_c.value*(t-20))*a.ac_dc_resistance_factor.value*p.brief.installation.length_m/1000
    for original in base_jobs(base):
        j=copy.deepcopy(original);n=j['study']['network']
        n['lines'][0]['r_ohm']=[[resistance if i==k else 0 for k in range(3)] for i in range(3)]
        n['sources'][0]['ll_v']*=condition['source_voltage_factor']
        # Preserve all owner acceptance criteria, not just the study settings.
        if j['kind']=='normal':j['case']['study']=copy.deepcopy(j['study'])
        j['condition']=condition
        yield j


def evaluation(job,report):
    if not report.get('completed') or not report.get('solved'):
        return dict(passed=False,numerically_complete=False,status=report.get('status'),reason=report.get('reason'),report_sha256=digest(encoded(report)))
    s=StudySpec.model_validate(job['study'])
    if report.get('code_sha256')!=package_fingerprint() or report.get('network_sha256')!=canonical_hash(s.network) or report.get('settings')!=s.coupled.model_dump(mode='json'):
        raise ValueError('native trace identity differs from owner study')
    r=assess(s,report,job['requirements']) if job['kind']=='fault' else normal_result(job,report)
    return dict(numerically_complete=True,**r)


def verify_job(job,root):
    root=Path(root);anchor=verify(root)
    if read_json(root/'study.json')!=job['study'] or read_json(root/'condition.json')!=job['condition']:
        raise ValueError('owner study/condition differs')
    name='requirements' if job['kind']=='fault' else 'case'
    if read_json(root/(name+'.json'))!=job[name]:raise ValueError('owner engineering criteria differ')
    report=read_json(root/'trace.json');result=evaluation(job,report)
    if read_json(root/'result.json')!=result:raise ValueError('study result recomputation differs')
    oracle=algebra(SimpleNamespace(study=StudySpec.model_validate(job['study'])),report) if result['numerically_complete'] else dict(passed=False,reason='incomplete_native_trace')
    if verify(root)!=anchor:raise ValueError('study changed during audit')
    return dict(id=job['id'],kind=job['kind'],scenario=job['scenario'],method=job['method'],grid=job['grid'],
                profile_passed=result['passed'],numerically_complete=result['numerically_complete'],algebra=oracle,
                passed=result['passed'] and oracle['passed'],manifest_sha256=anchor)


def execute(args):
    job,path,verify_only=args;root=Path(path)
    if root.exists():return verify_job(job,root)
    if verify_only:raise ValueError('verification cannot create a missing study')
    report=run_study(StudySpec.model_validate(job['study']))
    root.mkdir(parents=True,exist_ok=False)
    # Preserve raw data even if a later clock/criteria audit rejects the trace.
    (root/'study.json').write_bytes(encoded(job['study']))
    (root/'trace.json').write_bytes(encoded(report))
    result=evaluation(job,report)
    entries=dict(study=job['study'],condition=job['condition'],trace=report,result=result)
    name='requirements' if job['kind']=='fault' else 'case';entries[name]=job[name]
    for name,value in entries.items():(root/(name+'.json')).write_bytes(encoded(value))
    seal(root);return verify_job(job,root)


def finish(base,condition,root,rows):
    if len(rows)!=36 or len({r['id'] for r in rows})!=36:raise ValueError('all36 unique studies required')
    if all(r['numerically_complete'] for r in rows):
        result=finish_corner(base,root,rows)
    else:
        result=dict(passed=False,rows=sorted(rows,key=lambda r:r['id']),solver_pairs=[],duty=[],studies=36,
                    code_sha256=package_fingerprint(),base_sha256=digest(encoded(base.model_dump(mode='json'))))
    return dict(**result,condition=condition,numerically_complete=all(r['numerically_complete'] for r in rows))


def collect(pool,requests,deadline=None):
    if deadline is not None and deadline<=time.monotonic():raise TimeoutError('Cartesian soft wall budget exhausted before submission')
    pending={pool.submit(execute,args) for args in requests};rows=[]
    try:
        while pending:
            remaining=None if deadline is None else deadline-time.monotonic()
            if remaining is not None and remaining<=0:raise TimeoutError('Cartesian soft wall budget exhausted; no complete index published')
            done,pending=wait(pending,timeout=2 if remaining is None else min(2,remaining),return_when=FIRST_COMPLETED)
            for f in done:rows.append(f.result())
    finally:
        for f in pending:f.cancel()
    return rows


def summary(p,entries,prod):
    count=len(list(conditions(p)))*17
    complete=len(entries)==count
    return dict(schema='power-grid-env.running-cartesian/v1',protocol_sha256=digest(encoded(p.model_dump(mode='json'))),
        producer=prod,complete=complete,passed=complete and all(e['passed'] for e in entries),
        conditions=len(list(conditions(p))),variants=len(entries),native_runs=36*len(entries),
        solver_pairs=18*len(entries),measured_fault_runs=18*len(entries),duty_fine_pairs=6*len(entries),
        all_declared_cartesian_samples_qualified=complete and all(e['passed'] for e in entries),
        continuous_uncertainty_qualified=False,normative_approval=False,reward=None,
        scope='Finite Cartesian conductor temperatures/source voltages/nominal+16 relay corners, all six owner modes, Radau/DAE and three output meshes. Catalog-derived constant cable resistance; fixed transformer R/X. No continuous interior, dynamic thermal feedback, independent ODE or universal selectivity claim.')


def run_store(protocol,out,workers=8,resume=False):
    start=time.monotonic();p=CartesianProtocol.model_validate(protocol);prod=producer();root=Path(out)
    if not 1<=workers<=16:raise ValueError('workers must be1..16')
    if root.is_symlink():raise ValueError('symlink store forbidden')
    if root.exists():
        if not resume:raise ValueError('new output required, or explicit --resume')
        if (root/'index/manifest.json').exists():return verify_store(root,p,workers)
        if read_json(root/'index/protocol.json')!=p.model_dump(mode='json') or read_json(root/'index/producer.json')!=prod:
            raise ValueError('resume owner/producer mismatch')
    else:
        (root/'index').mkdir(parents=True);(root/'conditions').mkdir()
        (root/'index/protocol.json').write_bytes(encoded(p.model_dump(mode='json')))
        (root/'index/producer.json').write_bytes(encoded(prod))
    entries=[];pool=ProcessPoolExecutor(max_workers=workers)
    try:
        for condition_id,condition in conditions(p):
            parent=root/'conditions'/condition_id
            if (root/'conditions').is_symlink() or parent.is_symlink():raise ValueError('symlink condition collection forbidden')
            parent.mkdir(exist_ok=True)
            for name,base,recipe in effective_plans(p.envelope):
                if time.monotonic()-start>=p.wall_budget_s:raise TimeoutError('Cartesian soft wall budget exhausted')
                corner=parent/name
                expected=list(condition_jobs(p,base,condition))
                rows=collect(pool,[(j,str(corner/j['id']),False) for j in expected],start+p.wall_budget_s)
                result=finish(base,condition,corner,rows)
                if producer()!=prod:raise ValueError('producer changed during Cartesian run')
                if (corner/'manifest.json').exists():
                    if read_json(corner/'recipe.json')!=recipe or read_json(corner/'result.json')!=result:raise ValueError('resumed corner differs')
                else:
                    (corner/'recipe.json').write_bytes(encoded(recipe));(corner/'result.json').write_bytes(encoded(result));seal(corner)
                entries.append(dict(condition_id=condition_id,corner=name,passed=result['passed'],manifest_sha256=verify(corner)))
                print(condition_id,name,len(entries),'/',len(list(conditions(p)))*17,'passed=',result['passed'],flush=True)
    finally:pool.shutdown(wait=True,cancel_futures=True)
    if producer()!=prod:raise ValueError('producer changed before index publication')
    result=summary(p,entries,prod)
    (root/'index/entries.json').write_bytes(encoded(entries));(root/'index/result.json').write_bytes(encoded(result));seal(root/'index')
    return result


def verify_store(root,protocol,workers=8):
    root=Path(root);p=CartesianProtocol.model_validate(protocol);prod=producer()
    if not 1<=workers<=16:raise ValueError('workers must be1..16')
    if root.is_symlink() or {x.name for x in root.iterdir()}!={'index','conditions'} or (root/'conditions').is_symlink():raise ValueError('unexpected Cartesian store layout')
    if {x.name for x in (root/'index').iterdir()}!={'protocol.json','producer.json','entries.json','result.json','manifest.json'}:raise ValueError('unexpected index layout')
    before=verify(root/'index')
    if read_json(root/'index/protocol.json')!=p.model_dump(mode='json') or read_json(root/'index/producer.json')!=prod:raise ValueError('owner/producer mismatch')
    declared=list(conditions(p));variants=list(effective_plans(p.envelope))
    if {x.name for x in (root/'conditions').iterdir()}!={c[0] for c in declared}:raise ValueError('condition coverage differs')
    entries=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for condition_id,condition in declared:
            parent=root/'conditions'/condition_id
            if parent.is_symlink() or {x.name for x in parent.iterdir()}!={v[0] for v in variants}:raise ValueError('corner coverage differs')
            for name,base,recipe in variants:
                corner=parent/name;anchor=verify(corner);jobs=list(condition_jobs(p,base,condition))
                if {x.name for x in corner.iterdir()}!={j['id'] for j in jobs}|{'recipe.json','result.json','manifest.json'}:raise ValueError('study layout/coverage differs')
                if read_json(corner/'recipe.json')!=recipe:raise ValueError('corner recipe differs')
                rows=collect(pool,[(j,str(corner/j['id']),True) for j in jobs])
                result=finish(base,condition,corner,rows)
                if read_json(corner/'result.json')!=result or verify(corner)!=anchor:raise ValueError('corner changed or result differs')
                entries.append(dict(condition_id=condition_id,corner=name,passed=result['passed'],manifest_sha256=anchor))
                print('verified',condition_id,name,flush=True)
    for e in entries:
        if verify(root/'conditions'/e['condition_id']/e['corner'])!=e['manifest_sha256']:raise ValueError('previous corner changed during audit')
    result=summary(p,entries,prod)
    if read_json(root/'index/entries.json')!=entries or read_json(root/'index/result.json')!=result or verify(root/'index')!=before or producer()!=prod:
        raise ValueError('index/producer changed or result differs')
    return dict(verified=True,index_manifest_sha256=before,**result)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--protocol',required=True);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=8)
    p.add_argument('--resume',action='store_true');p.add_argument('--verify',action='store_true')
    a=p.parse_args();protocol=read_json(a.protocol)
    result=verify_store(a.out,protocol,a.workers) if a.verify else run_store(protocol,a.out,a.workers,a.resume)
    print(dict(passed=result['passed'],native_runs=result['native_runs']))
    if not result['passed']:raise SystemExit(2)

if __name__=='__main__':main()
