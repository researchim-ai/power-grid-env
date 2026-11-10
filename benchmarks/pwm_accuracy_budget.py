"""Fixed error-budget comparison against pinned current motulator, fresh workers.

Timings are repeated only after calibration; failures cannot become exclusions.
RSS includes a common imported-library baseline, not isolated array allocation.
"""
import argparse
import hashlib
import importlib.metadata
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
from benchmarks import pwm_motor_comparison as model
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify

VERSION='0.23.1'
WHEEL_SHA='dc184858cead49aa31c2e9f4bfbb149b6489850aad2360f1005b012fa3218000'
CANDIDATES={'primary':[1e-4,5e-5,2e-5,1e-5], 'reference':[1e-4,1e-6,1e-8]}
REPEATS=3


def protocol(name):
    p=model.protocol(name)
    p['reference'].update(version=VERSION,wheel_sha256=WHEEL_SHA,max_step_s=.0002,rtol_grid=CANDIDATES['reference'],atol_rule='rtol*1e-3')
    p['reference'].pop('rtol');p['reference'].pop('atol');p.pop('levels')
    p['primary_steps_s']=CANDIDATES['primary'];p['measured_repeats']=REPEATS
    p['oracle_refinement']=[dict(rtol=1e-12,atol=1e-13,max_step_s=2e-5),dict(rtol=1e-13,atol=1e-14,max_step_s=1e-5)]
    return p


def identity(wheel):
    return dict(package=package_fingerprint(),sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path(model.__file__)]},reference_sources=model.authenticate(wheel,VERSION,WHEEL_SHA),versions={n:importlib.metadata.version(n) for n in ['numpy','scipy','motulator','pydantic']},python=platform.python_version())


def worker(request,wheel):
    if not sys.platform.startswith('linux'):raise ValueError('RSS profile requires Linux KiB units')
    model.authenticate(wheel,VERSION,WHEEL_SHA)
    # Common library/import baseline, all completed outside measured solve.
    from motulator.drive.model import InductionMachine,InductionMachinePars
    name=request['case'];provider=request['provider'];value=request['value'];spec=protocol(name)
    if provider not in {'oracle','primary','reference'}:raise ValueError('unknown provider')
    if provider!='oracle' and value not in CANDIDATES[provider]:raise ValueError('unregistered candidate')
    if provider=='oracle' and value not in [0,1]:raise ValueError('unregistered oracle')
    baseline=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    start=time.perf_counter();cpu=time.process_time()
    if provider=='primary':trajectory=model.native(spec,value)
    elif provider=='reference':trajectory=model.external(spec,rtol=value,atol=value*1e-3,max_step=.0002)
    else:trajectory=model.external(spec,rtol=1e-12 if value==0 else 1e-13,atol=1e-13 if value==0 else 1e-14,max_step=2e-5 if value==0 else 1e-5)
    measurement=dict(wall_s=time.perf_counter()-start,cpu_s=time.process_time()-cpu,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,import_baseline_peak_rss_kib=baseline)
    return dict(request=request,trajectory=trajectory,measurement=measurement)


def execute(request,wheel):
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    command=[sys.executable,'-m','benchmarks.pwm_accuracy_budget','--worker','--wheel',str(Path(wheel).resolve())]
    result=subprocess.run(command,input=encoded(request).decode(),capture_output=True,text=True,env=env,timeout=120)
    if result.returncode:raise RuntimeError('comparison worker failed: '+result.stderr[-2000:])
    return json.loads(result.stdout)


def choose(rows):
    eligible=[r for r in rows if r['check']['passed']]
    if not eligible:raise ValueError('no candidate meets the fixed error budget')
    return min(eligible,key=lambda r:r['measurement']['wall_s'])['request']['value']


def summary_case(name,calibration,repetitions,oracle_check):
    chosen={p:choose([r for r in calibration if r['request']['provider']==p]) for p in CANDIDATES}
    measurements={}
    for provider in CANDIDATES:
        runs=[r for r in repetitions if r['request']['provider']==provider]
        if len(runs)!=REPEATS or any(r['request']['value']!=chosen[provider] for r in runs):raise ValueError('repeat inventory differs')
        measurements[provider]=dict(selected_parameter=chosen[provider],wall_median_s=statistics.median(r['measurement']['wall_s'] for r in runs),wall_min_s=min(r['measurement']['wall_s'] for r in runs),wall_max_s=max(r['measurement']['wall_s'] for r in runs),cpu_median_s=statistics.median(r['measurement']['cpu_s'] for r in runs),peak_rss_max_kib=max(r['measurement']['peak_rss_kib'] for r in runs),all_repeats_within_budget=all(r['check']['passed'] for r in runs))
    passed=all(v['all_repeats_within_budget'] for v in measurements.values()) and all(oracle_check['errors'][k]<model.LIMITS[k]*.01 for k in model.LIMITS)
    return dict(case=name,passed=passed,measurements=measurements,primary_vs_reference_wall_ratio=measurements['primary']['wall_median_s']/measurements['reference']['wall_median_s'],oracle_refinement=oracle_check)


def run(out,wheel,replay=False):
    out=Path(out);before=identity(wheel)
    if replay:
        manifest=verify(out)
        if read_json(out/'producer.json')!=before:raise ValueError('producer/environment differs')
    else:
        out.mkdir(parents=True,exist_ok=False);(out/'producer.json').write_bytes(encoded(before))
    def obtain(filename,request):
        value=execute(request,wheel)
        if replay:
            stored=read_json(out/filename)
            if encoded(stored['trajectory'])!=encoded(value['trajectory']) or stored['request']!=request:raise ValueError('numerical replay differs: '+filename)
            return stored
        return value
    def store(filename,value):
        if replay:
            if encoded(read_json(out/filename))!=encoded(value):raise ValueError('recomputed acceptance differs: '+filename)
        else:(out/filename).write_bytes(encoded(value))
    rows=[]
    for name in model.NAMES:
        spec=protocol(name);store(name+'-protocol.json',spec)
        oracles=[]
        for level in [0,1]:
            file=f'{name}-oracle-{level}.json';value=obtain(file,dict(case=name,provider='oracle',value=level));store(file,value);oracles.append(value['trajectory'])
        check=model.compare(oracles[0],oracles[1],spec)
        calibration=[]
        for provider,values in CANDIDATES.items():
            for index,value in enumerate(values):
                file=f'{name}-{provider}-calibration-{index}.json';r=obtain(file,dict(case=name,provider=provider,value=value));r['check']=model.compare(r['trajectory'],oracles[1],spec);store(file,r);calibration.append(r)
        selected={p:choose([r for r in calibration if r['request']['provider']==p]) for p in CANDIDATES};repeats=[]
        for repeat in range(REPEATS):
            order=['primary','reference'] if repeat%2==0 else ['reference','primary']
            for provider in order:
                file=f'{name}-{provider}-repeat-{repeat}.json';r=obtain(file,dict(case=name,provider=provider,value=selected[provider]));r['check']=model.compare(r['trajectory'],oracles[1],spec);store(file,r);repeats.append(r)
        row=summary_case(name,calibration,repeats,check);rows.append(row);print(encoded(row).decode(),flush=True)
    summary=dict(schema='power-grid-env.pwm-accuracy-budget/v1',passed=all(r['passed'] for r in rows),reference_version=VERSION,limits=model.LIMITS,repeats=REPEATS,rows=rows,external_trajectories=32,primary_trajectories=28,superiority_established=False,scope='registered imposed-PWM motor+shaft only; finite candidate grid, fastest passing single-run calibration then three fresh-process repeats; common imports excluded from solve clocks but included in process peak RSS; no full-drive/system ranking')
    store('summary.json',summary)
    if identity(wheel)!=before:raise ValueError('producer changed during run')
    digest=verify(out) if replay else seal(out)
    if replay and manifest!=digest:raise ValueError('artifact changed during replay')
    print(encoded(dict(passed=summary['passed'],replayed=replay,manifest_sha256=digest)).decode(),flush=True)
    if not summary['passed']:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--out');group.add_argument('--verify');group.add_argument('--worker',action='store_true');parser.add_argument('--wheel',required=True);args=parser.parse_args()
    if args.worker:print(encoded(worker(json.load(sys.stdin),args.wheel)).decode())
    else:run(args.verify or args.out,args.wheel,bool(args.verify))
