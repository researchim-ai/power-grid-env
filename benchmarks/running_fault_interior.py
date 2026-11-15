"""Deterministic stratified interior probes; not continuous robustness proof."""
import argparse
import random
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from pydantic import Field,model_validator
from power_grid_env.electrical_sizing import Contract
from power_grid_env.closed_loop_protection import ClosedLoopPlan
from power_grid_env.study_artifacts import read_json,encoded,verify,seal,digest
from benchmarks.running_fault_cartesian import CartesianProtocol,condition_jobs,collect,finish,producer as cartesian_producer


class InteriorProtocol(Contract):
    cartesian: CartesianProtocol
    seed: int=Field(default=20261115,ge=0,le=2**32-1)
    strata: int=Field(default=8,ge=1,le=16)
    wall_budget_s: float=Field(default=7200,gt=0,le=14400)

    @model_validator(mode='after')
    def intervals(self):
        if len(self.cartesian.conductor_temperatures_c)!=2 or len(self.cartesian.source_voltage_factors)!=2:
            raise ValueError('interior probes require exactly two temperature/voltage endpoints')
        return self


def producer():
    return dict(cartesian=cartesian_producer(),interior_sha256=digest(Path(__file__).read_bytes()))


def points(p):
    rng=random.Random(p.seed);columns=[]
    for _ in range(6):
        bins=list(range(p.strata));rng.shuffle(bins)
        columns.append([(b+rng.random())/p.strata for b in bins])
    yield 'center',[.5]*6
    for i in range(p.strata):yield f'interior-{i:02d}',[col[i] for col in columns]


def concrete(p,coordinates):
    base=p.cartesian.envelope.base.model_dump(mode='json');base['id']='interior-effective'
    for u,(stage,key) in zip(coordinates[:4],[('primary','pickup_a'),('primary','time_multiplier'),('backup','pickup_a'),('backup','time_multiplier')]):
        tol='pickup_tolerance' if key=='pickup_a' else 'time_tolerance'
        base[stage][key]*=1+(2*u-1)*base[stage][tol]
    for stage in ['primary','backup']:base[stage]['pickup_tolerance']=0.;base[stage]['time_tolerance']=0.
    t=p.cartesian.conductor_temperatures_c;v=p.cartesian.source_voltage_factors
    condition=dict(conductor_temperature_c=t[0]+coordinates[4]*(t[1]-t[0]),source_voltage_factor=v[0]+coordinates[5]*(v[1]-v[0]))
    return ClosedLoopPlan.model_validate(base),condition


def summary(p,entries,prod):
    count=p.strata+1
    return dict(schema='power-grid-env.running-interior/v1',protocol_sha256=digest(encoded(p.model_dump(mode='json'))),producer=prod,
        complete=len(entries)==count,passed=len(entries)==count and all(e['passed'] for e in entries),
        sample_points=len(entries),native_runs=36*len(entries),solver_pairs=18*len(entries),
        measured_fault_runs=18*len(entries),duty_fine_pairs=6*len(entries),
        continuous_uncertainty_qualified=False,normative_approval=False,reward=None,
        scope='Center plus deterministic6D Latin strata over four relay pickup/TMS parameters, cable temperature and source voltage; all six owner modes and both solvers/three output meshes. Finite probes, not extrema optimization or continuous robustness proof.')


def run_store(protocol,out,workers=4,resume=False,verify_only=False):
    start=time.monotonic();p=InteriorProtocol.model_validate(protocol);prod=producer();root=Path(out)
    if not 1<=workers<=16:raise ValueError('workers must be1..16')
    if root.is_symlink():raise ValueError('symlink store forbidden')
    if verify_only:
        if {x.name for x in root.iterdir()}!={'index','points'}:raise ValueError('unexpected interior store layout')
        anchor=verify(root/'index')
    elif not root.exists():
        (root/'index').mkdir(parents=True);(root/'points').mkdir()
        (root/'index/protocol.json').write_bytes(encoded(p.model_dump(mode='json')));(root/'index/producer.json').write_bytes(encoded(prod))
    elif not resume:raise ValueError('new output required or explicit resume')
    elif (root/'index/manifest.json').exists():return run_store(p,root,workers,verify_only=True)
    if read_json(root/'index/protocol.json')!=p.model_dump(mode='json') or read_json(root/'index/producer.json')!=prod:raise ValueError('interior owner/producer mismatch')
    declared=list(points(p));entries=[]
    if (root/'points').is_symlink():raise ValueError('symlink points forbidden')
    if verify_only and {x.name for x in (root/'points').iterdir()}!={n for n,_ in declared}:raise ValueError('interior point coverage differs')
    pool=ProcessPoolExecutor(max_workers=workers)
    try:
        for name,coordinates in declared:
            base,condition=concrete(p,coordinates);jobs=list(condition_jobs(p.cartesian,base,condition));child=root/'points'/name
            before=verify(child) if verify_only else None
            if verify_only and {x.name for x in child.iterdir()}!={j['id'] for j in jobs}|{'coordinates.json','result.json','manifest.json'}:raise ValueError('interior study coverage differs')
            rows=collect(pool,[(j,str(child/j['id']),verify_only) for j in jobs],None if verify_only else start+p.wall_budget_s)
            result=finish(base,condition,child,rows)
            if producer()!=prod:raise ValueError('interior producer changed')
            if before is not None or (child/'manifest.json').exists():
                if read_json(child/'coordinates.json')!=coordinates or read_json(child/'result.json')!=result:raise ValueError('interior result/coordinates differ')
                if before is not None and verify(child)!=before:raise ValueError('interior point changed')
            else:
                (child/'coordinates.json').write_bytes(encoded(coordinates));(child/'result.json').write_bytes(encoded(result));seal(child)
            entries.append(dict(id=name,passed=result['passed'],manifest_sha256=verify(child)))
            print('interior',name,'passed=',result['passed'],flush=True)
    finally:pool.shutdown(wait=True,cancel_futures=True)
    result=summary(p,entries,prod)
    if verify_only:
        if read_json(root/'index/entries.json')!=entries or read_json(root/'index/result.json')!=result or verify(root/'index')!=anchor:raise ValueError('interior index differs')
        for e in entries:
            if verify(root/'points'/e['id'])!=e['manifest_sha256']:raise ValueError('interior point changed after audit')
        return dict(verified=True,index_manifest_sha256=anchor,**result)
    (root/'index/entries.json').write_bytes(encoded(entries));(root/'index/result.json').write_bytes(encoded(result));seal(root/'index')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--protocol',required=True);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=4)
    p.add_argument('--resume',action='store_true');p.add_argument('--verify',action='store_true');a=p.parse_args()
    r=run_store(read_json(a.protocol),a.out,a.workers,a.resume,a.verify)
    print(dict(passed=r['passed'],native_runs=r['native_runs']))
    if not r['passed']:raise SystemExit(2)
