"""Paired fresh-process timing of R06.13 vs cached Newton hot paths.

Fixed inputs, output generation included, common imports/BLAS warm-up outside
clocks, constructor inside clocks. No CPU-frequency/affinity isolation, no
external simulator comparison, and no claim of fewer integration steps.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time
import numpy as np
from benchmarks.extended_closed_loop_acceptance import document as base
from benchmarks.switching_hotpath_reference import LegacyDrive
from benchmarks.mna_network_acceptance import compare
from power_grid_env import switching_drive as runtime
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify

NAMES=('saturated_single_core','double_cage','saturated_four_core','warm_four_winding','deep_double_cage')
REPEATS=3


def document(name):
    if name not in NAMES:raise ValueError('unregistered hot-path workload')
    p=base('double_cage' if name=='deep_double_cage' else name)
    p['max_step_s']=3.90625e-8 if name=='deep_double_cage' else 1e-5
    if name=='deep_double_cage':p['step_budget']=2000000
    return p


def identity():
    root=Path(__file__).resolve().parents[1]
    files=[Path(__file__),*[Path(__file__).with_name(n) for n in ['switching_hotpath_reference.py','mna_network_acceptance.py','extended_closed_loop_acceptance.py','switching_extended_cases.py','switching_thermal_cases.py','switching_drive_cases.py']],root/'cases/studies/flux-saturated_dol.json']
    return dict(package=package_fingerprint(),sources={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def worker(name,implementation):
    p=document(name);original=runtime.SwitchingDrive
    if implementation=='legacy':runtime.SwitchingDrive=LegacyDrive
    try:
        np.linalg.solve(np.eye(2),np.ones(2));np.linalg.lstsq(np.eye(2),np.ones(2),rcond=1e-14)
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;wall=time.perf_counter();cpu=time.process_time()
        result=runtime.run_switching_drive(p)
        measurement=dict(wall_s=time.perf_counter()-wall,cpu_s=time.process_time()-cpu,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,import_peak_rss_kib=peak)
    finally:runtime.SwitchingDrive=original
    return dict(case=name,implementation=implementation,result=result,measurement=measurement)


def execute(name,implementation):
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    r=subprocess.run([sys.executable,'-m','benchmarks.switching_hotpath_acceptance','--worker',name,'--implementation',implementation],capture_output=True,text=True,env=env,timeout=1200)
    if r.returncode:raise RuntimeError('hot-path worker failed: '+r.stderr[-3000:])
    return json.loads(r.stdout)


def run(out,replay=False):
    if not sys.platform.startswith('linux'):raise ValueError('this timing protocol requires Linux ru_maxrss semantics')
    out=Path(out);before=identity()
    if replay:
        manifest=verify(out)
        if read_json(out/'producer.json')!=before:raise ValueError('hot-path producer differs')
    else:out.mkdir(parents=True,exist_ok=False);(out/'producer.json').write_bytes(encoded(before))
    def store(name,value):
        if replay:
            if encoded(read_json(out/name))!=encoded(value):raise ValueError('hot-path replay differs: '+name)
        else:(out/name).write_bytes(encoded(value))
    rows=[]
    for case_index,name in enumerate(NAMES):
        p=document(name);store(name+'-study.json',p);runs={k:[] for k in ['legacy','optimized']};checks=[]
        for repeat in range(REPEATS):
            order=['legacy','optimized'] if (case_index+repeat)%2==0 else ['optimized','legacy']
            for implementation in order:
                file=f'{name}-{implementation}-{repeat}.json';value=execute(name,implementation)
                if replay:
                    saved=read_json(out/file)
                    if encoded(saved['result'])!=encoded(value['result']):raise ValueError('hot-path numerical replay differs: '+file)
                    value=saved
                store(file,value);runs[implementation].append(value)
            check=compare(p,runs['legacy'][-1]['result'],runs['optimized'][-1]['result'])
            check['exact_result_values']=encoded(runs['legacy'][-1]['result'])==encoded(runs['optimized'][-1]['result']);checks.append(check)
            print(encoded(dict(case=name,repeat=repeat,passed=check['passed'],exact_result_values=check['exact_result_values'])).decode(),flush=True)
        measurement={k:dict(wall_median_s=statistics.median(v['measurement']['wall_s'] for v in values),wall_min_s=min(v['measurement']['wall_s'] for v in values),wall_max_s=max(v['measurement']['wall_s'] for v in values),cpu_median_s=statistics.median(v['measurement']['cpu_s'] for v in values),peak_rss_max_kib=max(v['measurement']['peak_rss_kib'] for v in values)) for k,values in runs.items()}
        repeated={k:all(encoded(v['result'])==encoded(values[0]['result']) for v in values) for k,values in runs.items()}
        row=dict(case=name,passed=all(c['passed'] for c in checks),checks=checks,measurements=measurement,median_speedup=measurement['legacy']['wall_median_s']/measurement['optimized']['wall_median_s'],repeated_values_exact=repeated,accepted_steps={k:values[0]['result']['metrics']['steps'] for k,values in runs.items()});rows.append(row)
        print(encoded(dict(case=name,passed=row['passed'],median_speedup=row['median_speedup'])).decode(),flush=True)
    summary=dict(schema='power-grid-env.switching-hotpath-before-after/v1',passed=all(r['passed'] for r in rows),rows=rows,repeats=REPEATS,trajectories=2*REPEATS*len(NAMES),scope='fixed 40-ms full workflows; 4 at 10 us and separate 39.0625 ns double-cage; prior implementation vs optimized same equations/timestep; independent worker processes; common imports/BLAS warm-up excluded, constructor/output generation included; serialization excluded; CPU affinity/frequency not isolated; no external simulator ranking or adaptive step reduction',superiority_established=False,environment=dict(python=platform.python_version(),numpy=np.__version__,platform=platform.platform(),threads=1))
    store('summary.json',summary)
    if identity()!=before:raise ValueError('hot-path producer changed during measurement')
    digest=verify(out) if replay else seal(out)
    if replay and digest!=manifest:raise ValueError('hot-path artifact changed during replay')
    print(encoded(dict(passed=summary['passed'],replayed=replay,manifest_sha256=digest)).decode(),flush=True)
    if not summary['passed']:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--out');group.add_argument('--verify');group.add_argument('--worker',choices=NAMES);parser.add_argument('--implementation',choices=['legacy','optimized']);args=parser.parse_args()
    if args.worker:
        if args.implementation is None:parser.error('--worker requires --implementation')
        print(encoded(worker(args.worker,args.implementation)).decode())
    else:run(args.verify or args.out,bool(args.verify))
