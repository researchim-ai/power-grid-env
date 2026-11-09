"""Frozen IEEE-case public-API AC comparisons and local latency observations.

Only balanced single-slack, fixed-PQ/PV, pi transformers, Q limits disabled.
Latency excludes imports/input decoding/model copy, includes the public solve
call and its own preprocessing/diagnostics. Not an NR-kernel or global ranking.
No downloads. Each backend/case uses its own sequential bounded subprocess.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time
import numpy as np

from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint

CASES=('case14','case30','case118','case300')
BACKENDS=('primary','primary_dense','primary_sparse','pandapower')
LIMITS=dict(voltage_pu=1e-8,angle_rad=1e-8,branch_power_mva=1e-5,independent_balance_mva=1e-6)
ROOT=Path(__file__).resolve().parents[1]
TIMING_SCOPE='prepared-model flat-start public solve API; imports, input decoding and model copies excluded; solver preprocessing and diagnostics included'
MEMORY_SCOPE='Linux /proc/self/status VmHWM after exec, including imports/preparation/warmups; rusage high watermark recorded separately and may inherit launcher memory; not incremental solver allocation'
PERFORMANCE_SCOPE='local observations on one CPU affinity, BLAS/OMP threads=1; host load/turbo/governor not controlled; no global ranking'


def canonical_case(ppc,name):
    from power_grid_env.network_opf import Bus,Branch,GeneratorSpec,OpfCase
    from power_grid_env.balanced_ac import case_document
    base=float(ppc['baseMVA'])
    return case_document(OpfCase(name,base,
        [Bus(int(b[0]),int(b[1]),float(b[2]),float(b[3]),float(b[4]/base),float(b[5]/base),float(b[7]),float(b[8]),float(b[12]),float(b[11])) for b in ppc['bus']],
        [GeneratorSpec(int(g[0]),float(g[4]),float(g[3]),float(g[9]),float(g[8]),(0.,),pg=float(g[1]),qg=float(g[2])) for g in ppc['gen'] if g[7]],
        [Branch(int(b[0]),int(b[1]),float(b[2]),float(b[3]),float(b[4]),float(b[5]),float(b[8]),float(b[9]),int(b[10]),float(b[11]),float(b[12])) for b in ppc['branch']] ))


def registered_input(name):
    import pandapower as pp
    import pandapower.networks as nw
    from pandapower.converter.pypower.to_ppc import to_ppc
    if name not in CASES or pp.__version__!='3.5.6':raise ValueError('registered IEEE cases and pandapower 3.5.6 required')
    net=getattr(nw,name)();pp.reset_results(net)
    ppc=to_ppc(net,init='flat',calculate_voltage_angles=True,trafo_model='pi',voltage_depend_loads=False)
    return dict(id=name,case=canonical_case(ppc,name),pandapower_json=pp.to_json(net),
        origin='pandapower 3.5.6 packaged IEEE variant; exported pi equivalent, not an untouched original MATPOWER file',
        shunts='canonical gs/bs are pu admittance; MATPOWER GS/BS are MW/Mvar at 1 pu',angle_alignment='subtract each result slack angle; absolute source angle is arbitrary')


def independent_balance(case,result):
    """Direct branch-terminal sums; no production or reference Ybus/helpers."""
    buses=case['buses'];idx={b['id']:i for i,b in enumerate(buses)};base=case['base_mva']
    v=np.array(result['voltage_pu'])*np.exp(1j*np.array(result['angle_rad']))
    outgoing=np.zeros(len(buses),complex)
    for line in case['branches']:
        if not line['status']:continue
        f,t=idx[line['fbus']],idx[line['tbus']]
        y=1/complex(line['r'],line['x']);tap=(line['ratio'] or 1.)*np.exp(1j*np.deg2rad(line['angle_deg']))
        i_f=(y+1j*line['b']/2)/abs(tap)**2*v[f]-y/np.conj(tap)*v[t]
        i_t=(y+1j*line['b']/2)*v[t]-y/tap*v[f]
        outgoing[f]+=v[f]*np.conj(i_f)*base;outgoing[t]+=v[t]*np.conj(i_t)*base
    scheduled=np.array([-complex(b['pd'],b['qd']) for b in buses])
    for bus,p,q in result['generation_by_bus']:scheduled[idx[int(bus)]]+=complex(p,q)
    outgoing+=np.array([complex(b['gs'],-b['bs'])*base for b in buses])*abs(v)**2
    error=float(max(abs(scheduled-outgoing),default=0))
    if not math.isfinite(error):raise ValueError('nonfinite independent power mismatch')
    return error


def normalize_primary(case,result):
    if not result.get('completed'):raise ValueError('primary AC solve did not converge: '+str(result.get('status')))
    values=np.deg2rad(result['va_deg']);slack=next(i for i,b in enumerate(case['buses']) if b['type_code']==3)
    gen={b['id']:[0.,0.] for b in case['buses']}
    for g,p,q in zip(case['generators'],result['pg_mw'],result['qg_mvar'],strict=True):gen[g['bus']][0]+=p;gen[g['bus']][1]+=q
    return dict(voltage_pu=result['vm_pu'],angle_rad=(values-values[slack]).tolist(),
        generation_by_bus=[[bus,*pq] for bus,pq in gen.items()],
        branch_power_mva=[[row[k] for k in ('p_from_mw','q_from_mvar','p_to_mw','q_to_mvar')] for row in result['branches']],iterations=result['iterations'])


def normalize_reference(case,net):
    if not net.converged:raise ValueError('reference AC solve did not converge')
    ppc=net._ppc;bus=ppc['bus'];branch=ppc['branch'];gen=ppc['gen']
    if [int(b[0]) for b in bus]!=[b['id'] for b in case['buses']] or len(branch)!=len(case['branches']):raise ValueError('reference bus/branch inventory mismatch')
    for row,line in zip(branch,case['branches'],strict=True):
        if (int(row[0].real),int(row[1].real),int(row[10].real))!=(line['fbus'],line['tbus'],line['status']):raise ValueError('reference branch order mismatch')
    slack=next(i for i,b in enumerate(case['buses']) if b['type_code']==3);angles=np.deg2rad(bus[:,8])
    units={b['id']:[0.,0.] for b in case['buses']}
    for g in gen:
        if g[7]:units[int(g[0])][0]+=float(g[1]);units[int(g[0])][1]+=float(g[2])
    return dict(voltage_pu=bus[:,7].tolist(),angle_rad=(angles-angles[slack]).tolist(),generation_by_bus=[[bus,*pq] for bus,pq in units.items()],
        branch_power_mva=branch[:,13:17].real.tolist(),iterations=int(ppc['iterations']))


def worker(document,backend,repeats=5):
    if backend not in BACKENDS or not 3<=repeats<=15:raise ValueError('bounded registered backend/repetition count required')
    affinity=sorted(os.sched_getaffinity(0));os.sched_setaffinity(0,{affinity[0]})
    case=document['case']
    initialization=document.get('initialization','flat')
    if initialization not in {'flat','dc_seed'}:raise ValueError('unknown initial-guess policy')
    if backend.startswith('primary'):
        from power_grid_env.balanced_ac import case_from_document,solve_power_flow
        model=case_from_document(case)
        def prepare():return copy.deepcopy(model)
        def solve(obj):return solve_power_flow(obj,tolerance_pu=1e-8/case['base_mva'],max_iterations=40,enforce_q_limits=False,linear_solver={'primary':'auto','primary_dense':'dense','primary_sparse':'sparse'}[backend])
        def normalize(obj,result):return normalize_primary(case,result)
        version='0.1.0'
    else:
        import pandapower as pp
        if pp.__version__!='3.5.6':raise ValueError('pinned pandapower 3.5.6 required')
        model=pp.from_json_string(document['pandapower_json'])
        def prepare():return copy.deepcopy(model)
        def solve(obj):
            pp.runpp(obj,algorithm='nr',init='results' if initialization=='dc_seed' else 'flat',trafo_model='pi',calculate_voltage_angles=True,tolerance_mva=1e-8/case['base_mva'],max_iteration=40,
                enforce_q_lims=False,voltage_depend_loads=False,distributed_slack=False,numba=True,lightsim2grid=False)
        def normalize(obj,result):return normalize_reference(case,obj)
        version=pp.__version__
    timings=[];outputs=[];warmups=[]
    for i in range(2+repeats):
        obj=prepare();start=time.perf_counter();cpu=time.process_time();raw=solve(obj)
        clock=time.perf_counter()-start;cpu=time.process_time()-cpu
        result=normalize(obj,raw);independent=independent_balance(case,result)
        if independent>LIMITS['independent_balance_mva']:raise ValueError('independent power balance failed')
        # Ignore iteration counters in deterministic physical-output comparison.
        physical={k:v for k,v in result.items() if k!='iterations'}
        outputs.append(hashlib.sha256(encoded(physical)).hexdigest())
        (warmups if i<2 else timings).append(dict(wall_s=clock,cpu_s=cpu))
    if len(set(outputs))!=1:raise ValueError('repeated physical results differ')
    return dict(completed=True,backend=backend,initialization=initialization,version=version,linear_solver=raw['linear_solver'] if backend.startswith('primary') else None,result=result,independent_balance_mva=independent,
        samples=timings,warmup_samples=warmups,median_wall_s=statistics.median(t['wall_s'] for t in timings),median_cpu_s=statistics.median(t['cpu_s'] for t in timings),
        process_peak_rss_mib=next(float(line.split()[1])/1024 for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmHWM:')),
        rusage_high_watermark_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
        repetition_physical_hashes=outputs,case_input_sha256=hashlib.sha256(encoded(document)).hexdigest(),
        threads={k:os.environ.get(k) for k in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMBA_NUM_THREADS']},
        cpu_affinity=sorted(os.sched_getaffinity(0)),repeat_physical_hash=outputs[-1],producer_sha256=package_fingerprint())


def compare_outputs(document,a,b):
    case=document['case'];expected_buses=len(case['buses']);expected_branches=len(case['branches']);errors={}
    for key,shape in [('voltage_pu',(expected_buses,)),('angle_rad',(expected_buses,)),('branch_power_mva',(expected_branches,4))]:
        x=np.asarray(a['result'][key],float);y=np.asarray(b['result'][key],float)
        if x.shape!=shape or y.shape!=shape or not np.isfinite(x).all() or not np.isfinite(y).all():raise ValueError('complete finite physical inventory required')
        errors[key]=float(max(abs(x-y).ravel(),default=0))
    balances={role:independent_balance(case,r['result']) for role,r in [('primary',a),('reference',b)]}
    return dict(passed=all(v<=LIMITS[k] for k,v in errors.items()) and all(v<=LIMITS['independent_balance_mva'] for v in balances.values()),
        maximum_errors=errors,independent_balance_mva=balances,limits=LIMITS)



def case_row(name,document,results):
    comparison=compare_outputs(document,results['primary'],results['pandapower'])
    variants={role:compare_outputs(document,results[role],results['pandapower']) for role in ['primary_dense','primary_sparse']}
    comparison['passed']=comparison['passed'] and all(v['passed'] for v in variants.values())
    return dict(id=name,status='completed',**comparison,variants=variants,
        linear_algebra={role:r['linear_solver'] for role,r in results.items() if role.startswith('primary')},
        latency={role:{k:r[k] for k in ['median_wall_s','median_cpu_s','process_peak_rss_mib']} for role,r in results.items()})


def run(out,repeats=5):
    from power_grid_env.ipc import kill_process_group
    if not 3<=repeats<=15:raise ValueError('3..15 repetitions required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint()
    import pandapower as pp
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ['power_grid_env','benchmarks'] for p in (ROOT/folder).glob('*.py')}
    pp_sources={str(p.relative_to(Path(pp.__file__).parent)):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(pp.__file__).parent.rglob('*.py')}
    def write(name,value):
        p=out/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(encoded(value))
    documents={name:registered_input(name) for name in CASES}
    for name,document in documents.items():write(name+'/input.json',document)
    write('protocol.json',dict(schema='power-grid-env.ieee-ac-public-api/v2',cases=list(CASES),backends=list(BACKENDS),repeats=repeats,warmups=2,limits=LIMITS,producer_sha256=code,
        source_sha256=sources,pandapower_sources=pp_sources,input_sha256={name:hashlib.sha256(encoded(d)).hexdigest() for name,d in documents.items()},platform=platform.platform(),python=sys.version,cpu_affinity=[min(os.sched_getaffinity(0))],
        timing_scope=TIMING_SCOPE,
        memory_scope=MEMORY_SCOPE,
        performance_scope=PERFORMANCE_SCOPE,
        numba=True,lightsim2grid=False,reactive_limits=False,transformer_model='pi',target_mismatch_mva=1e-8,newton_tolerance='1e-8/baseMVA in both solvers; pinned pandapower 3.5.6 newtonpf applies its tolerance_mva option to the pu mismatch vector',max_iterations=40))
    rows=[]
    for i,(name,document) in enumerate(documents.items()):
        results={};failure=None
        for backend in (BACKENDS if i%2==0 else BACKENDS[::-1]):
            target=out/name/(backend+'.json');log=out/name/(backend+'.log')
            env={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMBA_NUM_THREADS':'1','PYTHONPATH':str(ROOT),'MPLCONFIGDIR':'/tmp/pge-ieee-mpl'}
            with log.open('wb') as stream:
                proc=subprocess.Popen([sys.executable,'-m','benchmarks.ieee_ac_comparison','--worker',backend,'--input',str((out/name/'input.json').resolve()),'--out',str(target.resolve()),'--repeats',str(repeats)],cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
                try:proc.wait(timeout=180)
                except subprocess.TimeoutExpired:kill_process_group(proc);failure='worker_timeout'
            if proc.returncode!=0 or not target.exists() or target.stat().st_size>8000000:failure=failure or 'worker_failed';continue
            results[backend]=read_json(target)
        if len(results)==len(BACKENDS):
            row=case_row(name,document,results)
        else:row=dict(id=name,status='failed',passed=False,reason=failure)
        write(name+'/comparison.json',row);rows.append(row)
    unchanged=package_fingerprint()==code and all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value for name,value in sources.items()) and all(hashlib.sha256((Path(pp.__file__).parent/name).read_bytes()).hexdigest()==value for name,value in pp_sources.items())
    summary=dict(passed=unchanged and all(r['passed'] for r in rows),cases=rows,producer_sha256=code,source_unchanged=unchanged,
        superiority_proven=False,published_paper_reproduced=False,scope='four pandapower-packaged IEEE balanced AC variants; no OPF, dynamic/EMT, batch or agent performance claim')
    write('summary.json',summary);seal(out);return summary


def verify_result(directory):
    directory=Path(directory);identity=verify(directory);protocol=read_json(directory/'protocol.json');summary=read_json(directory/'summary.json')
    policy=dict(schema='power-grid-env.ieee-ac-public-api/v2',warmups=2,timing_scope=TIMING_SCOPE,memory_scope=MEMORY_SCOPE,performance_scope=PERFORMANCE_SCOPE,
        numba=True,lightsim2grid=False,reactive_limits=False,transformer_model='pi',target_mismatch_mva=1e-8,max_iterations=40)
    if any(protocol.get(k)!=v for k,v in policy.items()) or not 3<=protocol['repeats']<=15:raise ValueError('declared benchmark policy differs')
    if protocol['cases']!=list(CASES) or protocol['limits']!=LIMITS or protocol['producer_sha256']!=package_fingerprint():raise ValueError('registered current-producer corpus required')
    if protocol['source_sha256']['benchmarks/ieee_ac_comparison.py']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('benchmark source differs')
    rows=[]
    for i,name in enumerate(CASES):
        d=read_json(directory/name/'input.json')
        if d['id']!=name or hashlib.sha256(encoded(d)).hexdigest()!=protocol['input_sha256'][name]:raise ValueError('frozen input identity differs')
        results={}
        for backend in (BACKENDS if i%2==0 else BACKENDS[::-1]):
            r=read_json(directory/name/(backend+'.json'))
            if not r['completed'] or r['backend']!=backend or r['producer_sha256']!=package_fingerprint() or r['case_input_sha256']!=protocol['input_sha256'][name] or r['version']!=('0.1.0' if backend.startswith('primary') else '3.5.6'):raise ValueError('backend result identity differs')
            if not isinstance(r['process_peak_rss_mib'],(int,float)) or not math.isfinite(r['process_peak_rss_mib']) or r['process_peak_rss_mib']<=0:raise ValueError('invalid reported process memory')
            if r['cpu_affinity']!=protocol['cpu_affinity'] or set(r['threads'].values())!={'1'}:raise ValueError('timing conditions differ')
            if len(r['samples'])!=protocol['repeats'] or len(r['warmup_samples'])!=2:raise ValueError('full timing sample coverage required')
            for sample in r['samples']+r['warmup_samples']:
                if set(sample)!={'wall_s','cpu_s'} or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in sample.values()):raise ValueError('invalid timing sample')
            for key,field in [('median_wall_s','wall_s'),('median_cpu_s','cpu_s')]:
                if r[key]!=statistics.median(t[field] for t in r['samples']):raise ValueError('timing median differs')
            physical={k:v for k,v in r['result'].items() if k!='iterations'};physical_hash=hashlib.sha256(encoded(physical)).hexdigest()
            if r['repeat_physical_hash']!=physical_hash or r['repetition_physical_hashes']!=[physical_hash]*(2+protocol['repeats']):raise ValueError('repeated result inventory differs')
            if independent_balance(d['case'],r['result'])!=r['independent_balance_mva']:raise ValueError('independent balance differs')
            results[backend]=r
        row=case_row(name,d,results)
        if row!=read_json(directory/name/'comparison.json'):raise ValueError('comparison differs from raw measurements')
        rows.append(row)
    expected=dict(passed=all(r['passed'] for r in rows),cases=rows,producer_sha256=package_fingerprint(),source_unchanged=True,
        superiority_proven=False,published_paper_reproduced=False,scope='four pandapower-packaged IEEE balanced AC variants; no OPF, dynamic/EMT, batch or agent performance claim')
    if summary!=expected:raise ValueError('summary differs from raw comparisons')
    return dict(verified=True,manifest_sha256=identity,passed=expected['passed'],scope='recomputed normalized accuracy, independent balance and reported timing statistics; solvers/timings not re-executed; no source authorship authentication')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out');p.add_argument('--verify');p.add_argument('--repeats',type=int,default=5);p.add_argument('--worker',choices=BACKENDS);p.add_argument('--input')
    a=p.parse_args()
    if a.verify:print(json.dumps(verify_result(a.verify)));raise SystemExit(0)
    if not a.out:p.error('--out required for execution')
    if a.worker:Path(a.out).write_bytes(encoded(worker(read_json(a.input),a.worker,a.repeats)))
    else:
        result=run(a.out,a.repeats);print(json.dumps(result,ensure_ascii=False));raise SystemExit(0 if result['passed'] else 2)
