"""1354/1888-bus matched sparse AC profiles, load variants and retained failure.

1888 RTE flat start fails in both implementations: keep that explicit negative
case. Positive RTE runs share frozen precomputed pandapower DC voltage guesses;
this is not qualification of an independent end-to-end initializer.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import numpy as np
from benchmarks.ieee_ac_comparison import canonical_case,worker as measured_worker,compare_outputs,LIMITS,normalize_primary
from power_grid_env.balanced_ac import case_from_document,solve_power_flow
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.ipc import kill_process_group

ROOT=Path(__file__).resolve().parents[1]
SPECS=tuple((name,factor,'dc_seed' if name=='case1888rte' else 'flat') for name in ('case1354pegase','case1888rte') for factor in (.9,1.,1.1))+ (('case1888rte',1.,'flat'),)
BACKENDS=('primary_sparse','pandapower')


def identifier(spec):return spec[0]+'-'+str(round(spec[1]*100))+'-'+spec[2].replace('_','-')


def document(spec):
    import pandapower as pp
    import pandapower.networks as nw
    from pandapower.converter.pypower.to_ppc import to_ppc
    if spec not in SPECS or pp.__version__!='3.5.6':raise ValueError('registered large AC spec and pinned pandapower required')
    name,factor,init=spec;net=getattr(nw,name)();net.load['p_mw']*=factor;net.load['q_mvar']*=factor
    pp.reset_results(net)
    if init=='dc_seed':pp.rundcpp(net,trafo_model='pi')
    ppc=to_ppc(net,init='results' if init=='dc_seed' else 'flat',trafo_model='pi',voltage_depend_loads=False,mode='pf')
    # Pinned public to_ppc forces enforce_q_lims=True even in PF mode.
    # Rebuild the same PF tables with our declared Q-limits-disabled policy.
    from pandapower.pd2ppc import _pd2ppc
    net._options['enforce_q_lims']=False
    _,ppc=_pd2ppc(net)
    if list(net.bus.index)!=list(range(len(ppc['bus']))):raise ValueError('large profile requires explicit contiguous bus mapping')
    # PF export does not retain OPF voltage bounds. Preserve the original
    # bus-table bounds for diagnostics; they are not enforced by this PF run.
    for b in ppc['bus']:
        row=net.bus.loc[int(b[0])]
        lo=float(row.get('min_vm_pu',.02));hi=float(row.get('max_vm_pu',3.))
        b[12]=lo if np.isfinite(lo) and lo>0 else .02
        b[11]=hi if np.isfinite(hi) and hi>=b[12] else max(3.,b[12])
    return dict(conversion_mode='pf',reactive_limits=False,conversion_note='pinned _pd2ppc rerun after public exporter to disable its forced Q-limit policy',voltage_bounds='original net.bus limits for diagnostics only; missing/nonpositive lower limits default to .02',id=identifier(spec),case=canonical_case(ppc,identifier(spec)),pandapower_json=pp.to_json(net),initialization=init,
        load_factor=factor,expected='both_not_converged' if name=='case1888rte' and init=='flat' else 'converged_match',
        origin='pandapower 3.5.6 packaged PEGASE/RTE variant, exported pi equivalent',
        initialization_scope='identical frozen DC guess prepared by pandapower outside timing; no independent initializer claim' if init=='dc_seed' else 'flat start',
        scope='load table P/Q scaled; generator schedules and static generation left unchanged; no Q limits, no engineering feasibility approval')


def negative_worker(d,backend):
    if backend=='primary_sparse':
        r=solve_power_flow(case_from_document(d['case']),tolerance_pu=1e-8/d['case']['base_mva'],enforce_q_limits=False,linear_solver='sparse')
        status=r['status'];iterations=r.get('iterations');actual=r['completed'];diagnostic=r
    else:
        import pandapower as pp
        from pandapower.auxiliary import LoadflowNotConverged
        if pp.__version__!='3.5.6':raise ValueError('pinned reference required')
        net=pp.from_json_string(d['pandapower_json']);actual=False;status='execution_error';diagnostic={}
        try:
            pp.runpp(net,init='results' if d['initialization']=='dc_seed' else 'flat',trafo_model='pi',numba=True,lightsim2grid=False,tolerance_mva=1e-8/d['case']['base_mva'],max_iteration=40,enforce_q_lims=False,voltage_depend_loads=False)
            actual=bool(net.converged);status='converged' if actual else 'reference_not_converged'
        except LoadflowNotConverged as exc:status='reference_not_converged';diagnostic=dict(message=str(exc))
        iterations=int(net._ppc.get('iterations',0))
    return dict(backend=backend,completed=actual,status=status,iterations=iterations,diagnostic=diagnostic,
        producer_sha256=package_fingerprint(),case_input_sha256=hashlib.sha256(encoded(d)).hexdigest(),voltage_collapse_proven=False,engineering_acceptable=False)


def row_for(d,results):
    if d['expected']=='both_not_converged':
        a,b=results['primary_sparse'],results['pandapower']
        passed=not a['completed'] and not b['completed'] and a['status']=='newton_not_converged' and b['status']=='reference_not_converged'
        return dict(id=d['id'],expected=d['expected'],passed=passed,solved=bool(a['completed'] and b['completed']),engineering_acceptable=False,
            statuses={k:v['status'] for k,v in results.items()},voltage_collapse_proven=False)
    if not all(r['completed'] for r in results.values()):
        return dict(id=d['id'],expected=d['expected'],passed=False,solved=False,status='paired_nonconvergence' if not any(r['completed'] for r in results.values()) else 'unequal_convergence',statuses={k:r.get('status','converged') for k,r in results.items()},voltage_collapse_proven=False)
    comparison=compare_outputs(d,results['primary_sparse'],results['pandapower'])
    return dict(id=d['id'],expected=d['expected'],solved=True,**comparison,
        buses=len(d['case']['buses']),branches=len(d['case']['branches']),initialization=d['initialization'],
        latency={k:{key:v[key] for key in ['median_wall_s','median_cpu_s','process_peak_rss_mib']} for k,v in results.items()},
        linear_solver=results['primary_sparse']['linear_solver'],engineering_feasibility_assessed=False)


def run(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint()
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ['power_grid_env','benchmarks'] for p in (ROOT/folder).glob('*.py')}
    import pandapower as pp
    pp_root=Path(pp.__file__).parent;ref_sources={str(p.relative_to(pp_root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in pp_root.rglob('*.py')}
    def write(name,value):
        p=out/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(encoded(value))
    documents=[document(spec) for spec in SPECS]
    for d in documents:write(d['id']+'/input.json',d)
    protocol=dict(schema='power-grid-env.large-ac/v1',cases=[d['id'] for d in documents],producer_sha256=code,source_sha256=sources,reference_sources=ref_sources,
        input_sha256={d['id']:hashlib.sha256(encoded(d)).hexdigest() for d in documents},limits=LIMITS,repeats=3,warmups=2,backends=list(BACKENDS),
        timing_scope='prepared model public API; DC initialization, imports and copies excluded; flat or identical shared DC seed specified per case',
        memory_scope='Linux post-exec VmHWM process footprint, not incremental matrix memory',
        negative_scope='one retained both-not-converged regression; does not establish physical impossibility',host_load_controlled=False)
    write('protocol.json',protocol);rows=[]
    for i,d in enumerate(documents):
        results={}
        for backend in (BACKENDS if i%2==0 else BACKENDS[::-1]):
            target=out/d['id']/(backend+'.json');log=out/d['id']/(backend+'.log')
            env={**os.environ,'PYTHONPATH':str(ROOT),'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMBA_NUM_THREADS':'1','MPLCONFIGDIR':'/tmp/pge-large-mpl'}
            with log.open('wb') as stream:
                process=subprocess.Popen([sys.executable,'-m','benchmarks.large_ac_acceptance','--worker',backend,'--input',str((out/d['id']/'input.json').resolve()),'--out',str(target.resolve())],cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
                try:process.wait(timeout=180)
                except subprocess.TimeoutExpired:kill_process_group(process)
            if process.returncode==0 and target.exists() and target.stat().st_size<=16000000:results[backend]=read_json(target)
        row=row_for(d,results) if len(results)==2 else dict(id=d['id'],passed=False,solved=False,status='execution_failed')
        write(d['id']+'/comparison.json',row);rows.append(row)
    unchanged=package_fingerprint()==code and all(hashlib.sha256((ROOT/k).read_bytes()).hexdigest()==v for k,v in sources.items()) and all(hashlib.sha256((pp_root/k).read_bytes()).hexdigest()==v for k,v in ref_sources.items())
    result=dict(passed=unchanged and all(r['passed'] for r in rows),cases=rows,producer_sha256=code,source_unchanged=unchanged,
        expected_positive_count=6,expected_negative_count=1,all_solved=all(r['solved'] for r in rows),dense_limit_extended=False,independent_initialization_for_rte=False,superiority_proven=False)
    write('summary.json',result);seal(out);return result


def verify_result(directory):
    import statistics,math
    directory=Path(directory);identity=verify(directory);p=read_json(directory/'protocol.json');summary=read_json(directory/'summary.json')
    if p['cases']!=[identifier(s) for s in SPECS] or p['limits']!=LIMITS or p['producer_sha256']!=package_fingerprint() or p['repeats']!=3 or p['warmups']!=2:raise ValueError('registered current-producer large corpus required')
    if p['source_sha256']['benchmarks/large_ac_acceptance.py']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('large benchmark source changed')
    rows=[]
    for name in p['cases']:
        d=read_json(directory/name/'input.json')
        if hashlib.sha256(encoded(d)).hexdigest()!=p['input_sha256'][name] or d['conversion_mode']!='pf' or d['reactive_limits'] is not False:raise ValueError('frozen PF input/policy differs')
        results={}
        for backend in BACKENDS:
            r=read_json(directory/name/(backend+'.json'))
            if r['backend']!=backend or r['producer_sha256']!=package_fingerprint() or r['case_input_sha256']!=p['input_sha256'][name]:raise ValueError('large result identity differs')
            if r['completed'] and 'samples' in r:
                if r['version']!=('0.1.0' if backend=='primary_sparse' else '3.5.6') or r['initialization']!=d['initialization']:raise ValueError('solver version/initialization differs')
                if len(r['samples'])!=3 or len(r['warmup_samples'])!=2 or set(r['threads'].values())!={'1'} or len(r['cpu_affinity'])!=1:raise ValueError('timing inventory/conditions differ')
                for sample in r['samples']+r['warmup_samples']:
                    if any(not math.isfinite(v) or v<0 for v in sample.values()):raise ValueError('invalid timing sample')
                for metric,key in [('median_wall_s','wall_s'),('median_cpu_s','cpu_s')]:
                    if r[metric]!=statistics.median(sample[key] for sample in r['samples']):raise ValueError('timing median differs')
                physical={k:v for k,v in r['result'].items() if k!='iterations'};h=hashlib.sha256(encoded(physical)).hexdigest()
                if r['repeat_physical_hash']!=h or r['repetition_physical_hashes']!=[h]*5:raise ValueError('physical repeat coverage differs')
            results[backend]=r
        row=row_for(d,results)
        if row!=read_json(directory/name/'comparison.json'):raise ValueError('large comparison differs from measurements')
        rows.append(row)
    expected=dict(passed=all(r['passed'] for r in rows),cases=rows,producer_sha256=package_fingerprint(),source_unchanged=True,
        expected_positive_count=6,expected_negative_count=1,all_solved=all(r['solved'] for r in rows),dense_limit_extended=False,independent_initialization_for_rte=False,superiority_proven=False)
    if summary!=expected:raise ValueError('large summary differs')
    return dict(verified=True,manifest_sha256=identity,corpus_passed=expected['passed'],matched_converged_cases=sum(r['solved'] and r['passed'] for r in rows),
        open_case_ids=[r['id'] for r in rows if not r['passed']],scope='recomputed comparisons/declared outcomes and reported medians; no solver reintegration, no authorship authentication')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out');p.add_argument('--verify');p.add_argument('--input');p.add_argument('--worker',choices=BACKENDS);a=p.parse_args()
    if a.verify:print(json.dumps(verify_result(a.verify)));raise SystemExit(0)
    if not a.out:p.error('--out required for execution')
    if a.worker:
        d=read_json(a.input)
        try:r=negative_worker(d,a.worker) if d['expected']=='both_not_converged' else measured_worker(d,a.worker,repeats=3)
        except Exception as exc:
            if type(exc).__name__=='LoadflowNotConverged' or (isinstance(exc,ValueError) and str(exc).startswith('primary AC solve did not converge')):
                r=negative_worker(d,a.worker);r['latency_measured']=False;r['measurement_failure']=str(exc)
            else:raise
        Path(a.out).write_bytes(encoded(r))
    else:
        r=run(a.out);print('passed:',r['passed']);raise SystemExit(0 if r['passed'] else 2)
