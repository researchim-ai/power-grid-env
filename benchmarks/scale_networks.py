"""Synthetic branching networks, distributed PQ loads and optional mesh ties.

Sizes are measurements, not blanket support claims. One process lifetime RSS
peak is reported explicitly; each case stores raw solve timings and correctness.
"""
from __future__ import annotations
import argparse
import resource
import time
from pathlib import Path
from power_grid_env.build import NetBuilder
from power_grid_env.backend_opendss import solve_network
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json


def make_network(size: int, mesh: bool):
    if size < 2:raise ValueError('at least two buses required')
    b=NetBuilder(f'scale-{size}-{mesh}','Synthetic branching network',10.,s_base_va=10e6)
    for i in range(size):b.node(f'n{i}')
    b.source('n0',.01,.05)
    for i in range(1,size):
        b.line_z1(f'n{(i-1)//2}',f'n{i}',.1+.2j,line_id=f'l{i}')
        b.load(f'n{i}',500.,100.,load_id=f'load{i}')
    if mesh:
        for i in range(50,size,50):
            b.line_z1(f'n{i}',f'n{i-25}',.2+.4j,line_id=f'tie{i}')
    return b.build()


def run(out: Path, sizes: list[int], repeats: int):
    if repeats<1:raise ValueError('positive repeat count required')
    results=[]
    for size in sizes:
        for mesh in (False,True):
            started=time.perf_counter();network=make_network(size,mesh);build_s=time.perf_counter()-started
            samples=[];checks=[]
            for _ in range(repeats):
                started=time.perf_counter();r=solve_network(network);elapsed=time.perf_counter()-started
                samples.append(elapsed)
                voltages=[abs(v.complex)/v.v_base_ln_v for v in r.voltages if v.phase in 'ABC' and v.v_base_ln_v]
                present={f.equipment_id for f in r.flows}
                wanted={l.id for l in network.loads}|{l.id for l in network.lines}|{s.id for s in network.sources}
                demand=sum(f.p_into_w for f in r.flows if f.equipment_id.startswith('load') and f.phase in 'ABC')
                injection=-sum(f.p_into_w for f in r.flows if f.equipment_id in {s.id for s in network.sources} and f.phase in 'ABC')
                losses=sum(f.p_into_w for f in r.flows if f.equipment_id.startswith(('l','tie')) and not f.equipment_id.startswith('load') and f.phase in 'ABC')
                error=abs(demand-500*(size-1))/network.s_base_va
                balance=abs(injection-demand-losses)/network.s_base_va
                checks.append({'status':r.status,'kcl_pu':r.diagnostics.get('kcl_residual_pu'),
                               'pq_error_pu':r.diagnostics.get('pq_setpoint_error_pu'),'demand_error_pu':error,
                               'power_balance_error_pu':balance,'min_voltage_pu':min(voltages,default=0.),
                               'voltage_channels':len(voltages),'missing_equipment':sorted(wanted-present)})
            passed=all(c['status']=='converged' and c['kcl_pu']<=1e-8 and c['pq_error_pu']<=1e-5
                       and c['demand_error_pu']<=1e-5 and c['power_balance_error_pu']<=1e-8
                       and not c['missing_equipment'] and c['voltage_channels']==3*size for c in checks)
            item={'nodes':size,'family':'meshed_binary_tree' if mesh else 'radial_binary_tree',
                  'load_w_per_nonroot_bus':500.,'build_s':build_s,'solve_seconds':samples,
                  'checks':checks,'verdict':'pass' if passed else 'fail',
                  'process_lifetime_peak_rss_kib_linux':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
            results.append(item);write_json(out/f'{size}-{mesh}.json',item)
    result={'verdict':'pass' if all(c['verdict']=='pass' for c in results) else 'fail',
            'code_sha256':package_fingerprint(),'evidence':'synthetic_correctness_and_resource_measurement',
            'cases':results,'repeats':repeats,'field_validated':False,'arbitrary_scale_claim':False,
            'independent_large_network_oracle':False,'cross_simulator_speed_claim':False}
    write_json(out/'summary.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--sizes',type=int,nargs='+',default=[100,1000,10000])
    p.add_argument('--repeats',type=int,default=3);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();result=run(a.out,a.sizes,a.repeats);print({'verdict':result['verdict'],'cases':len(result['cases'])})
    raise SystemExit(0 if result['verdict']=='pass' else 1)
