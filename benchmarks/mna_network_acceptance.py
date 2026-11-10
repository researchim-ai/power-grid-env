"""Fresh-process before/after MNA timing with complete physical-state checks.

Reference is the frozen previous implementation, not an external simulator.
Repeated solve clocks include constructor and the complete drive workflow.
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
from benchmarks.closed_loop_acceptance import document as closed
from benchmarks.switching_thermal_cases import document as thermal
from benchmarks.mna_legacy_reference import LegacyDrive
from power_grid_env import switching_drive as runtime
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify

NAMES=('nominal','grid_sag','gate_recovery','precharge','warm_winding','saturated_four_thermal','rotor_trip','initial_trip_cooling')
REPEATS=3


def document(name):
    if name not in NAMES:raise ValueError('unregistered MNA case')
    return closed(name) if name in NAMES[:4] else thermal(name)


def identity():
    names=['mna_network_acceptance.py','mna_legacy_reference.py','closed_loop_acceptance.py','switching_thermal_cases.py','switching_extended_cases.py','switching_drive_cases.py']
    root=Path(__file__).resolve().parents[1]
    files=[root/'benchmarks'/n for n in names]+[root/'cases/studies/flux-saturated_dol.json']
    return dict(package=package_fingerprint(),sources={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files},python=platform.python_version(),numpy=np.__version__)


def worker(name,implementation):
    if not sys.platform.startswith('linux'):raise ValueError('Linux KiB RSS profile required')
    if implementation not in ['legacy','optimized']:raise ValueError('unregistered implementation')
    p=document(name);original=runtime.SwitchingDrive
    if implementation=='legacy':runtime.SwitchingDrive=LegacyDrive
    try:
        imported_peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;clock=time.perf_counter();cpu=time.process_time()
        result=runtime.run_switching_drive(p)
        measured=dict(wall_s=time.perf_counter()-clock,cpu_s=time.process_time()-cpu,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,import_peak_rss_kib=imported_peak)
    finally:runtime.SwitchingDrive=original
    return dict(case=name,implementation=implementation,result=result,measurement=measured)


def execute(name,implementation):
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    r=subprocess.run([sys.executable,'-m','benchmarks.mna_network_acceptance','--worker',name,'--implementation',implementation],capture_output=True,text=True,env=env,timeout=180)
    if r.returncode:raise RuntimeError('MNA worker failed: '+r.stderr[-2000:])
    return json.loads(r.stdout)


def cap(key):
    if key in ['thermal_trip','gates_enabled','precharge_bypassed','torque_enabled','voltage_limited'] or key.endswith('_thermal_trip'):return 0.
    if key.endswith('_vs'):return 1e-5
    if key.endswith('_temperature_c'):return .001
    if key.endswith('_resistance_ohm'):return 1e-5
    if key.endswith('_a') or key.startswith('source_current_'):return .005
    if key.endswith('_rad_s'):return .002
    if key.endswith('_nm'):return .002
    if key.endswith('_v'):return .02
    if key=='frame_angle_rad':return 1e-5
    raise ValueError('unregistered physical channel: '+key)


def compare(p,a,b):
    expected=np.arange(round(p['duration_s']/p['sample_s'])+1)*p['sample_s']
    if len(a['time_s'])!=len(expected) or len(b['time_s'])!=len(expected) or not np.allclose(a['time_s'],expected,rtol=0,atol=5e-13) or not np.allclose(b['time_s'],expected,rtol=0,atol=5e-13):raise ValueError('MNA horizon/axis differs')
    if set(a['channels'])!=set(b['channels']):raise ValueError('MNA channel inventory differs')
    errors={};stage_errors={};passed=True;inactive_limit_differences=0
    for key in a['channels']:
        x=np.array(a['channels'][key]);y=np.array(b['channels'][key])
        if x.shape!=expected.shape or y.shape!=expected.shape or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):raise ValueError('invalid MNA channel samples')
        difference=abs(x-y)
        if key.startswith('last_midpoint_'):
            # A snapshot belongs to the last accepted interval, not a common
            # physical time if near-coincident PWM edges alter tiny intervals.
            stage_errors[key]=float(difference.max());continue
        if key=='frame_angle_rad':difference=abs(np.angle(np.exp(1j*(x-y))))
        if key=='voltage_limited':
            enabled=np.array(a['channels']['gates_enabled'],bool)|np.array(b['channels']['gates_enabled'],bool)
            inactive_limit_differences=int(np.count_nonzero(difference[~enabled]));difference=difference[enabled]
        error=float(difference.max()) if difference.size else 0.;errors[key]=error;passed=passed and error<=cap(key)
    ea=a['event_log'];eb=b['event_log'];event_error=0.;events_match=len(ea)==len(eb)
    if events_match:
        for x,y in zip(ea,eb):
            events_match=events_match and all(x.get(k)==y.get(k) for k in ['kind','component','cause'])
            event_error=max(event_error,abs(x['time_s']-y['time_s']))
    energies={k:float(max(abs(np.array([e[k] for e in a['energy']])-np.array([e[k] for e in b['energy']])))) for k in ['stored_j','source_j','external_heat_j','mechanical_j']}
    physical=all(r['completed'] and r['metrics']['final_energy_relative_error']<1e-7 and r['metrics']['kcl_max_a']<5e-5 and r['metrics']['motor_constitutive_residual_v']<1e-8 for r in [a,b])
    flags=all(a[k]==b[k] for k in ['initial_winding_thermal_trips','initial_thermal_trips'])
    return dict(passed=bool(passed and physical and flags and events_match and event_error<1e-8 and max(energies.values())<1e-5),physical_channel_errors=errors,last_interval_snapshot_errors=stage_errors,inactive_voltage_limit_differences=inactive_limit_differences,events_match=bool(events_match),maximum_event_time_error_s=event_error,energy_integral_errors_j=energies)


def run(out,replay=False):
    out=Path(out);before=identity()
    if replay:
        manifest=verify(out)
        if read_json(out/'producer.json')!=before:raise ValueError('producer differs')
    else:out.mkdir(parents=True,exist_ok=False);(out/'producer.json').write_bytes(encoded(before))
    def store(name,value):
        if replay:
            if encoded(read_json(out/name))!=encoded(value):raise ValueError('MNA acceptance replay differs: '+name)
        else:(out/name).write_bytes(encoded(value))
    rows=[]
    for name in NAMES:
        p=document(name);store(name+'-study.json',p);runs={k:[] for k in ['legacy','optimized']};checks=[]
        for repeat in range(REPEATS):
            order=['legacy','optimized'] if repeat%2==0 else ['optimized','legacy']
            for implementation in order:
                file=f'{name}-{implementation}-{repeat}.json';value=execute(name,implementation)
                if replay:
                    old=read_json(out/file)
                    if encoded(old['result'])!=encoded(value['result']):raise ValueError('MNA numerical replay differs: '+file)
                    value=old
                store(file,value);runs[implementation].append(value)
            checks.append(compare(p,runs['legacy'][-1]['result'],runs['optimized'][-1]['result']))
        measurements={k:dict(wall_median_s=statistics.median(r['measurement']['wall_s'] for r in v),wall_min_s=min(r['measurement']['wall_s'] for r in v),wall_max_s=max(r['measurement']['wall_s'] for r in v),cpu_median_s=statistics.median(r['measurement']['cpu_s'] for r in v),peak_rss_max_kib=max(r['measurement']['peak_rss_kib'] for r in v)) for k,v in runs.items()}
        speedup=measurements['legacy']['wall_median_s']/measurements['optimized']['wall_median_s']
        row=dict(case=name,passed=all(c['passed'] for c in checks),checks=checks,measurements=measurements,median_speedup=speedup,optimized_linear_counts={k:runs['optimized'][0]['result']['metrics'][k] for k in ['linear_direct_solves','linear_least_squares_solves','linear_direct_fallbacks']});rows.append(row);print(encoded(dict(case=name,passed=row['passed'],median_speedup=speedup)).decode(),flush=True)
    result=dict(schema='power-grid-env.mna-before-after/v1',passed=all(r['passed'] for r in rows),rows=rows,repeats=REPEATS,trajectories=48,scope='fixed 8-node drive profile, previous full SVD network vs cached assembly plus guarded direct solves; same settings/physical inputs; fresh processes, common imports outside clocks, constructor included; RSS common imports; no CPU frequency/affinity isolation, no external simulator comparison',superiority_established=False)
    store('summary.json',result)
    if identity()!=before:raise ValueError('producer changed during measurement')
    digest=verify(out) if replay else seal(out)
    if replay and manifest!=digest:raise ValueError('artifact changed during replay')
    print(encoded(dict(passed=result['passed'],replayed=replay,manifest_sha256=digest)).decode(),flush=True)
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();g=parser.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');g.add_argument('--worker');parser.add_argument('--implementation',choices=['legacy','optimized']);a=parser.parse_args()
    if a.worker:print(encoded(worker(a.worker,a.implementation)).decode())
    else:run(a.verify or a.out,bool(a.verify))
