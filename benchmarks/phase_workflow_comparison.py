"""Cold compile+fundamental-solve comparison on one authored four-wire case.

Each sample is a fresh process. Package import/Python startup is outside the
solve timer; peak RSS includes them. Both engines consume the same JSON. This
profile establishes no broad superiority over OpenDSS or any other simulator.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time


def ours(payload):
    from power_grid_env.phase_network import PhaseCase,harmonic_study
    case=PhaseCase.model_validate(payload)
    result=harmonic_study(case,[1])['frames'][0]
    return dict(voltages=[result['voltage'][n] for n in ['a','b','c','n']],currents=[result['inductor_current']['wire'+str(i)] for i in range(4)])


def native(payload):
    import math
    import numpy as np
    from opendssdirect.OpenDSSDirect import OpenDSSDirect
    dss=OpenDSSDirect().NewContext()
    def command(text):
        dss.Text.Command(text)
        if dss.Error.Number():raise ValueError(dss.Error.Description())
    def matrix(a):return '['+' | '.join(' '.join(str(x) for x in row[:i+1]) for i,row in enumerate(a))+']'
    # Deliberately specialized native adapter. Refuse a different topology rather than dropping it.
    if payload['id']!='authored-mutual-neutral' or payload['nodes']!=['sa','sb','sc','a','b','c','n'] or len(payload['inductors'])!=4 or len(payload['resistors'])!=3 or len(payload['capacitors'])!=4 or payload.get('converters') or payload.get('events'):
        raise ValueError('native adapter requires the frozen authored four-wire case')
    command('set defaultbasefrequency='+str(payload['frequency_hz']))
    rms=payload['sources'][0]['tones'][0]['rms']
    command(f'new circuit.ref phases=3 bus1=source.1.2.3 basekv={rms*math.sqrt(3)/1000} pu=1 r1=1e-9 x1=1e-9 r0=1e-9 x0=1e-9')
    r=np.diag([b['resistance_ohm'] for b in payload['inductors']]);x=2*np.pi*payload['frequency_hz']*np.array(payload['inductance_h'])
    command('new reactor.wire phases=4 bus1=source.1.2.3.0 bus2=load.1.2.3.4 rmatrix='+matrix(r)+' xmatrix='+matrix(x))
    for i,load in enumerate(payload['resistors'],start=1):
        command(f'new load.r{i} phases=1 bus1=load.{i}.4 conn=wye kv={rms/1000} kw={rms**2/load["resistance_ohm"]/1000} kvar=0 model=2 vminpu=.1 vmaxpu=2')
    for i,cap in enumerate(payload['capacitors'],start=1):
        frm='load.'+str(i) if i<4 else 'load.4';to='load.4' if i<4 else 'load.0'
        command(f'new capacitor.c{i} phases=1 bus1={frm} bus2={to} cuf={cap["capacitance_f"]*1e6} kv={rms/1000}')
    command('set tolerance=1e-12 maxiterations=200');dss.Solution.Solve()
    if not dss.Solution.Converged():raise ValueError('native OpenDSS did not converge')
    dss.Circuit.SetActiveBus('load');nodes=dss.Bus.Nodes();values=dss.Bus.Voltages();voltage={k:values[2*i:2*i+2] for i,k in enumerate(nodes)}
    dss.Circuit.SetActiveElement('reactor.wire');current=dss.CktElement.Currents()
    return dict(voltages=[voltage[i] for i in range(1,5)],currents=[current[2*i:2*i+2] for i in range(4)])


def child(engine,path):
    payload=json.loads(Path(path).read_text())['case']
    # Preimport outside the compile/solve timer for both Python APIs.
    if engine=='ours':
        import power_grid_env.phase_network
    else:
        import opendssdirect
    started=time.perf_counter();result=(ours if engine=='ours' else native)(payload);elapsed=time.perf_counter()-started
    return dict(engine=engine,elapsed_s=elapsed,process_peak_rss_kib_linux=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,result=result)


def run(out,repeats=9):
    from benchmarks.release_candidate import source_hashes
    from power_grid_env.evidence import package_fingerprint
    if type(repeats) is not int or not 3<=repeats<=31:raise ValueError('3..31 repeats required')
    out=Path(out).resolve()
    if out.exists():raise ValueError('new comparison output required')
    out.mkdir(parents=True);repo=Path(__file__).resolve().parents[1];path=repo/'cases/studies/phase_four_wire.json'
    sources=source_hashes(repo);code=package_fingerprint();rows=[];env=os.environ.copy();env['PYTHONPATH']=str(repo)
    for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']:env[key]='1'
    for i in range(repeats):
        for engine in (['ours','native'] if i%2==0 else ['native','ours']):
            p=subprocess.run([sys.executable,'-m','benchmarks.phase_workflow_comparison','--child',engine,'--case',str(path)],cwd=out,env=env,capture_output=True,text=True,timeout=30)
            (out/f'{i}-{engine}.log').write_text(p.stdout+'\nSTDERR\n'+p.stderr)
            if p.returncode:raise RuntimeError('fresh comparison worker failed')
            row=json.loads(p.stdout);rows.append(row)
    reference=rows[0]['result'];worst_v=0.;worst_i=0.
    for row in rows:
        worst_v=max(worst_v,max(abs(complex(*a)-complex(*b)) for a,b in zip(row['result']['voltages'],reference['voltages'])))
        worst_i=max(worst_i,max(abs(complex(*a)-complex(*b)) for a,b in zip(row['result']['currents'],reference['currents'])))
    from statistics import median
    own=median(r['elapsed_s'] for r in rows if r['engine']=='ours');native_time=median(r['elapsed_s'] for r in rows if r['engine']=='native')
    result=dict(passed=worst_v<1e-5 and worst_i<1e-6 and source_hashes(repo)==sources and code==package_fingerprint(),code_sha256=code,
                case_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),repeats=repeats,raw=rows,
                voltage_error_v=worst_v,current_error_a=worst_i,median_ours_s=own,median_native_s=native_time,native_over_ours=native_time/own,
                measured_cold_workflow_advantage=own<native_time,opendss_version=importlib.metadata.version('opendssdirect.py'),
                scope='one authored four-wire unbalanced RLC compile+fundamental-solve; three phase+neutral, mutual inductance, capacitor/load network',
                caveats=['Not a comparison of cached warm solvers','No converter/EMT/field or broad superiority claim','Native ideal supply has explicit 1e-9 ohm source impedance','Peak RSS is fresh-process lifetime, not incremental solver allocation'],policy='local_only_no_GitHub_no_CI')
    (out/'comparison.json').write_text(json.dumps(result,indent=2)+'\n');(out/'sources.json').write_text(json.dumps(sources,indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps({p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()},indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(__doc__);p.add_argument('--out',type=Path);p.add_argument('--repeats',type=int,default=9);p.add_argument('--child',choices=['ours','native']);p.add_argument('--case',type=Path);a=p.parse_args()
    if a.child:print(json.dumps(child(a.child,a.case)))
    else:
        if a.out is None:p.error('--out required')
        r=run(a.out,a.repeats);print(json.dumps({k:r[k] for k in ['passed','native_over_ours','voltage_error_v','current_error_a','measured_cold_workflow_advantage']}))

if __name__=='__main__':main()
