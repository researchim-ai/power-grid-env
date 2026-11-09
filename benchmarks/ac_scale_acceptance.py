"""Finite size/resource matrix for the complete distributed CPF/envelope workflow.

Positive qualifications and unresolved diagnostic inputs are kept separate.
Timing covers run_envelope including preparation, audits, serialization/sealing;
verification is timed separately. Linux process VmHWM is not matrix allocation.
"""
import argparse,hashlib,json,os,platform,statistics,subprocess,sys,time
from dataclasses import replace
from pathlib import Path
import numpy as np
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint
ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'cases/ac-scale'
SPECS=(('ieee118','qualification'),('synthetic600','qualification'),('pegase1354','qualification'),('synthetic2048','qualification'),('ieee300','diagnostic'),('rte1888','diagnostic'))
STEPS=(.1,.05)


def synthetic(n):
    from power_grid_env.network_opf import Bus,Branch,GeneratorSpec,OpfCase
    sites=list(range(0,n,32))
    buses=[Bus(i,3 if i==0 else 2 if i in sites else 1,.001 if i else 0.,0.,0.,0.,1.,0.,.8,1.2) for i in range(n)]
    gens=[GeneratorSpec(i,-100.,100.,0.,100.,(0.,),pg=0.,qg=0.) for i in sites]
    lines=[Branch(i,i+1,.001,.01,0.,1000.,0.,0.,1,-90.,90.) for i in range(n-1)]
    lines.append(Branch(n-1,0,.001,.01,0.,1000.,0.,0.,1,-90.,90.))
    for i in range(0,n-1,128):lines.append(lines[i])
    for i in range(0,n-1,256):lines.append(replace(lines[i],status=0))
    return OpfCase('authored-ring-'+str(n),100.,buses,gens,lines)


def prepare():
    from benchmarks.ieee_ac_comparison import registered_input
    from benchmarks.large_ac_acceptance import document
    from power_grid_env.balanced_ac import case_document,case_from_document
    from benchmarks.ac_dispatch_sparse_reference import solve_reference
    DATA.mkdir(parents=True,exist_ok=False);origins={}
    for name,role in SPECS:
        if name.startswith('synthetic'):
            base=synthetic(int(name.removeprefix('synthetic')));origin='authored lightly loaded ring with parallel/offline branches, generator every 32 nodes; not a real feeder';factor=1.1
        elif name=='ieee118':
            base=case_from_document(registered_input('case118')['case']);origin='pandapower 3.5.6 packaged IEEE118 pi-equivalent';factor=1.02
        elif name=='ieee300':
            base=case_from_document(registered_input('case300')['case']);origin='pandapower 3.5.6 packaged IEEE300 pi-equivalent';factor=1.02
        elif name=='pegase1354':
            base=case_from_document(document(('case1354pegase',1.,'flat'))['case']);origin='pandapower 3.5.6 packaged PEGASE1354 PF/pi export; original schedules and bounds';factor=1.02
        else:
            base=case_from_document(read_json(ROOT/'cases/ac-continuation/reference-inputs/rte-100.json')['case']);origin='frozen RTE1888 common DC seed from prior profile; three Pg schedules violate stated P bounds';factor=1.01
        target=replace(base,buses=[replace(b,pd=b.pd*factor,qd=b.qd*factor) for b in base.buses]);options=dict(participants=[dict(generator_index=i,weight=1.) for i,g in enumerate(base.generators) if g.pmax>g.pmin])
        currents=[]
        if role=='qualification':
            ref=solve_reference(base,options)['result'];idx={b.id:i for i,b in enumerate(base.buses)}
            chosen=next(i for i,b in enumerate(base.branches) if b.status and np.hypot(*ref['branch_power_mva'][i][:2])>1e-3)
            for side,k,bus in [('from',0,base.branches[chosen].fbus),('to',2,base.branches[chosen].tbus)]:
                p=ref['branch_power_mva'][chosen];i=np.hypot(p[k],p[k+1])/base.base_mva/ref['voltage_pu'][idx[bus]]
                currents.append(dict(branch_index=chosen,terminal=side,maximum_pu=float(i*1.05)))
        d=dict(start=case_document(base),target=case_document(target),options=dict(enforce_q_limits=True,dispatch=options,maximum_wall_s=240),monitor=dict(current_limits=currents,maximum_wall_s=240),provenance=dict(origin=origin,policy='author-defined equal positive P participation; Q limits include former slack; demonstration current thresholds 1.05 independent initial current, not ampere ratings'))
        raw=encoded(d);(DATA/(name+'.json')).write_bytes(raw);origins[name]=dict(role=role,origin=origin,buses=len(base.buses),branches=len(base.branches),generators=len(base.generators),target_load_factor=factor,sha256=hashlib.sha256(raw).hexdigest())
    (DATA/'origin.json').write_bytes(encoded(origins))


def physical(study):
    return {key:study['trace'][key] for key in ['points','final_result','attempts','turns','control_changes','p_control_changes','completed','reason']}


def peak_mib():
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith('VmHWM:'):return int(line.split()[1])/1024.
    raise ValueError('Linux process VmHWM required')


def worker(input_path,out):
    from power_grid_env.ac_envelope import run_envelope,verify_envelope
    from power_grid_env.ac_pseudo_arc import make_family,PseudoArcOptions
    from power_grid_env.balanced_ac import case_from_document
    from power_grid_env.balanced_sparse import stored_bytes
    affinity=sorted(os.sched_getaffinity(0));os.sched_setaffinity(0,{affinity[0]});d=read_json(input_path);out=Path(out);out.mkdir(parents=True,exist_ok=False);samples=[];hashes=[];last=None
    for i in range(3):
        now=time.perf_counter();cpu=time.process_time()
        try:study=run_envelope(d,out/str(i))
        except ValueError as exc:
            last=dict(completed=False,status='input_or_execution_rejected',error=str(exc));samples.append(dict(wall_s=time.perf_counter()-now,cpu_s=time.process_time()-cpu,verification_wall_s=None));break
        wall=time.perf_counter()-now;cpu=time.process_time()-cpu;start=time.perf_counter();v=verify_envelope(out/str(i));validation=time.perf_counter()-start
        digest=hashlib.sha256(encoded(physical(study))).hexdigest();hashes.append(digest)
        samples.append(dict(wall_s=wall,cpu_s=cpu,verification_wall_s=validation));last=dict(completed=bool(study['trace']['completed'] and study['envelope']['completed']),status=study['trace']['reason'],monitor_status=study['envelope']['reason'],accepted_points=len(study['trace']['points']),operating_events=len(study['envelope']['events']),p_events=len(study['trace']['p_control_changes']),q_events=len(study['trace']['control_changes']),timeline_complete=study['envelope']['timeline_complete'],verified=v['verified'])
        del study
    matrix=None
    if hashes:
        r=read_json(out/'0/trace.json')
        if r['points']:
            o=PseudoArcOptions.model_validate(d['options']);f=make_family(case_from_document(d['start']),case_from_document(d['target']),o);point=r['points'][0];u=f.pack(point['result'],0.);_,j=f.evaluate(u,True);border=f.bordered(j,np.array(point['tangent']))
            matrix=dict(sample='initial accepted state, not maximum fill or peak allocation',shape=list(j.shape),jacobian_nnz=j.nnz,jacobian_bytes=stored_bytes(j),bordered_bytes=stored_bytes(border),ybus_bytes=stored_bytes(f.system.y),hypothetical_dense_jacobian_bytes=int(j.shape[0]*j.shape[1]*8),dense_allocated=False)
    result=dict(producer_sha256=package_fingerprint(),input_sha256=hashlib.sha256(encoded(d)).hexdigest(),result=last,samples=samples,physical_hashes=hashes,repeats=3,median_wall_s=statistics.median(s['wall_s'] for s in samples),median_cpu_s=statistics.median(s['cpu_s'] for s in samples),process_peak_rss_mib=peak_mib(),matrix=matrix,affinity=[affinity[0]],threads={key:os.environ.get(key) for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']},host_load_controlled=False,memory_scope='post-exec Linux process VmHWM including imports, three runs, verification and initial matrix inspection; not incremental solver allocation')
    (out/'measurement.json').write_bytes(encoded(result));seal(out);return result


def source_hashes():
    paths=[Path(__file__),Path(__file__).with_name('ac_dispatch_sparse_reference.py'),Path(__file__).with_name('ac_dispatch_acceptance.py'),Path(__file__).with_name('ac_dispatch_arc_acceptance.py'),*sorted(DATA.glob('*.json'))]
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def compare_states(d,folder):
    from power_grid_env.ac_pseudo_arc import make_family,PseudoArcOptions
    from power_grid_env.balanced_ac import case_from_document
    from benchmarks.ac_dispatch_sparse_reference import solve_reference
    from benchmarks.ac_dispatch_arc_acceptance import states,compare,event_estimates
    study=dict(trace=read_json(folder/'trace.json'),envelope=read_json(folder/'envelope.json'));o=PseudoArcOptions.model_validate(d['options']);family=make_family(case_from_document(d['start']),case_from_document(d['target']),o);refs=[];checks=[];cache={}
    for label,lam,state in states(study):
        if lam not in cache:cache[lam]=solve_reference(family.load_case(lam),o.dispatch.model_dump(mode='json'))
        ref=cache[lam];c=compare(family,lam,state,ref);refs.append(dict(label=label,reference=ref,comparison=c));checks.append(c)
    return refs,dict(states=len(checks),passed=all(c['passed'] for c in checks),maximum_voltage_error_pu=max((c['errors']['voltage_pu'] for c in checks),default=0.),maximum_pg_error_mw=max((c['errors']['pg_mw'] for c in checks),default=0.),maximum_delta_error_mw=max((c['delta_error_mw'] for c in checks),default=0.),events=event_estimates(study))


def run(out):
    from power_grid_env.ipc import kill_process_group
    from benchmarks.ac_dispatch_arc_acceptance import step_comparison
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();sources=source_hashes();rows=[]
    for name,role in SPECS:
        levels=[]
        for step in STEPS if role=='qualification' else STEPS[:1]:
            d=read_json(DATA/(name+'.json'));d['options'].update(step=step,maximum_step=step);folder=out/name/str(step);folder.mkdir(parents=True);(folder/'input.json').write_bytes(encoded(d))
            env={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMBA_NUM_THREADS':'1','MPLCONFIGDIR':'/tmp/pge-scale-mpl'}
            with (folder/'worker.log').open('wb') as log:
                p=subprocess.Popen([sys.executable,'-m','benchmarks.ac_scale_acceptance','--worker','--input',str((folder/'input.json').resolve()),'--out',str((folder/'runs').resolve())],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                try:p.wait(timeout=600)
                except subprocess.TimeoutExpired:kill_process_group(p)
            if p.returncode!=0 or not (folder/'runs/measurement.json').exists():raise ValueError('scale worker execution failed: '+name)
            measured=read_json(folder/'runs/measurement.json');repeat_ok=len(measured['physical_hashes'])==3 and len(set(measured['physical_hashes']))==1
            row=dict(case=name,role=role,step=step,buses=len(d['start']['buses']),generators=len(d['start']['generators']),branches=len(d['start']['branches']),completed=measured['result']['completed'],status=measured['result']['status'],repeat_physical_equal=repeat_ok,median_wall_s=measured['median_wall_s'],median_cpu_s=measured['median_cpu_s'],process_peak_rss_mib=measured['process_peak_rss_mib'],matrix=measured['matrix'])
            if role=='qualification':
                refs,comparison=compare_states(d,folder/'runs/0');(folder/'references.json').write_bytes(encoded(refs));row.update(comparison);row['passed']=bool(row['completed'] and repeat_ok and comparison['passed'] and all(e['localized'] for e in row['events']));levels.append(row)
            else:
                from benchmarks.ac_dispatch_sparse_reference import solve_reference
                from power_grid_env.balanced_ac import case_from_document
                try:
                    reference=solve_reference(case_from_document(d['start']),d['options']['dispatch']);note=dict(completed=True,maximum_residual_pu=reference['maximum_residual_pu'])
                except ValueError as exc:note=dict(completed=False,error=str(exc))
                (folder/'diagnostic-reference.json').write_bytes(encoded(note));row['reference_base_completed']=note['completed'];row['qualification_claimed']=False
            rows.append(row);print(json.dumps(row),flush=True)
        if role=='qualification':rows.append(step_comparison(name,levels))
    unchanged=code==package_fingerprint() and source_hashes()==sources;qualified=all(r['passed'] for r in rows if r.get('role')=='qualification' or r.get('step_refinement'))
    s=dict(schema='pge-ac-scale/v1',producer_sha256=code,source_hashes=sources,rows=rows,qualified_profiles_passed=bool(qualified and unchanged),source_unchanged=unchanged,all_registered_profiles_completed=all(r['completed'] for r in rows if 'completed' in r),open_diagnostics=[r['case'] for r in rows if r.get('role')=='diagnostic' and not r['completed']],reference='separate sparse Newton + original PP 3.5.6 Ybus/dSbus_dV, separately initialized classical PF; no primary state or electrical implementation',reference_continuation_executed=False,superiority_proven=False,host_load_controlled=False)
    (out/'summary.json').write_bytes(encoded(s));seal(out);return s


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--prepare',action='store_true');p.add_argument('--worker',action='store_true');p.add_argument('--input');p.add_argument('--out');a=p.parse_args()
    if a.prepare:prepare()
    elif a.worker:worker(a.input,a.out)
    else:
        r=run(a.out);print(json.dumps(r));raise SystemExit(0 if r['qualified_profiles_passed'] else 2)
