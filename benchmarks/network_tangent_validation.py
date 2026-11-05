"""Direct network elimination against cached ports, with fresh-process evidence."""
from pathlib import Path
import argparse,json,os,subprocess,sys,time,tracemalloc
import numpy as np
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from benchmarks.native_online_validation import source_identity
from benchmarks.sparse_jacobian_validation import NAMES as PREVIOUS,case as previous
from benchmarks.eight_machine_validation import case as eight_case
from benchmarks.coupled_jacobian_validation import compare
from benchmarks.jacobian_scaling_validation import current_exec_peak_rss_kib

NAMES=PREVIOUS+('motor_trip_latch','motor_heat','motor_coast')
MEMORY=((2000,400),(20000,2000))


def case(name):
    if name in PREVIOUS:s=previous(name)
    elif name=='motor_trip_latch':s=eight_case(name)
    elif name in ('motor_heat','motor_coast'):
        filename='thermal_blocked_reclose' if name=='motor_heat' else 'motor_coast_short'
        s=StudySpec.model_validate_json(Path('cases/studies/'+filename+'.json').read_text())
        if name=='motor_heat':s.induction.events=[e for e in s.induction.events if e.kind!='motor_open']
        s.induction.jacobian_format='csc'
    else:raise ValueError('registered network tangent case required')
    return s


def worker(study,mode,out):
    from unittest.mock import patch
    import power_grid_env.network_sensitivity as sensitivity
    s=StudySpec.model_validate_json(Path(study).read_text());settings=s.coupled or s.induction
    settings.jacobian_network=mode;s=StudySpec.model_validate(s.model_dump(mode='json'))
    counts=dict(direct=0,cached=0);direct=sensitivity.directional_response;cached=sensitivity.port_response
    def action(*a,**kw):counts['direct']+=1;return direct(*a,**kw)
    def ports(*a,**kw):
        counts['cached']+=1
        if mode=='direct_solve':raise ValueError('direct integration requested a port-square response')
        return cached(*a,**kw)
    started=time.perf_counter()
    with patch.object(sensitivity,'directional_response',action),patch.object(sensitivity,'port_response',ports):a=run_study(s)
    out=Path(out);out.mkdir(parents=True,exist_ok=False);(out/'trace.json').write_text(json.dumps(a,allow_nan=False)+'\n')
    if not a.get('completed') or a['integration'].get('jacobian_network_response')!=mode or a['integration']['jacobian_assembly']!='column_blocks':raise ValueError('integration path or completion mismatch')
    if counts['direct' if mode=='direct_solve' else 'cached']==0:raise ValueError('requested network path not exercised')
    (out/'metrics.json').write_text(json.dumps(dict(passed=True,code_sha256=package_fingerprint(),case_sha256=canonical_hash(s),mode=mode,calls=counts,wall_s=time.perf_counter()-started,current_exec_peak_rss_kib_linux=current_exec_peak_rss_kib()),indent=2)+'\n')


def comparison(left,right,out):
    a=json.loads(Path(left,'trace.json').read_text());b=json.loads(Path(right,'trace.json').read_text())
    if a['code_sha256']!=package_fingerprint() or b['code_sha256']!=package_fingerprint():raise ValueError('trajectory producer mismatch')
    result=compare(a,b)
    # Discrete channels must agree exactly, not merely within state tolerance.
    for k in a['channels']:
        if k.endswith(('.connected','.thermal_latched','.stator_thermal_latched','.field_thermal_latched','.field_voltage_enabled','.voltage_gate','_active','_expired')) and result['channel_errors'][k]!=0:result['passed']=False
    Path(out).write_text(json.dumps(result,indent=2)+'\n')
    if not result['passed']:raise ValueError('network tangent trajectories differ')


def memory_worker(nodes,ports,mode,out):
    from types import SimpleNamespace
    from scipy.sparse import diags
    from scipy.sparse.linalg import splu
    from power_grid_env.network_sensitivity import directional_response,port_response
    if (nodes,ports) not in MEMORY:raise ValueError('registered algebraic memory tuple required')
    a=diags([np.full(nodes-1,-.2+.01j),np.full(nodes,2.+.1j),np.full(nodes-1,-.2+.01j)],[-1,0,1],format='csc');lu=splu(a)
    maximum=0
    def solve(rhs):
        nonlocal maximum;maximum=max(maximum,rhs.shape[1]);return lu.solve(rhs)
    model=SimpleNamespace(bus_names=range(nodes),lu=SimpleNamespace(solve=solve));buses=np.arange(ports,dtype=int)*nodes//ports;y=np.ones(ports,dtype=complex);u=np.random.default_rng(91).normal(size=(ports,3))
    tracemalloc.start();started=time.perf_counter()
    if mode=='direct_solve':result=directional_response(model,buses,y,buses,u)
    elif mode=='cached_ports':result=port_response(model,buses,y,buses)@u
    else:raise ValueError('invalid memory mode')
    elapsed=time.perf_counter()-started;_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    out=Path(out);out.mkdir(parents=True,exist_ok=False);np.save(out/'result.npy',result)
    (out/'metrics.json').write_text(json.dumps(dict(nodes=nodes,ports=ports,mode=mode,temporary_network_RHS_maximum_columns=maximum,numpy_tracemalloc_peak_bytes=peak,wall_s=elapsed,current_exec_peak_rss_kib_linux=current_exec_peak_rss_kib(),code_sha256=package_fingerprint(),scope='algebraic derivative probe, not a dynamic trajectory'),indent=2)+'\n')


def qualify(out):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();sources=source_identity();rows=[];memory=[]
    specs={n:case(n) for n in NAMES}
    (out/'protocol.json').write_text(json.dumps(dict(code_sha256=code,source_sha256=sources,cases={n:s.model_dump(mode='json') for n,s in specs.items()},repeats=2,modes=['cached_ports','direct_solve'],memory_tuples=MEMORY,worker_timeout_s=600,no_port_square_in_direct_worker=True,whole_DAE_solver=False,industrial_release_qualified=False),indent=2)+'\n')
    (out/'summary.json').write_text(json.dumps(dict(passed=False,status='running'))+'\n')
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    def execute(args,log):
        r=subprocess.run([sys.executable,'-m','benchmarks.network_tangent_validation',*args],env=env,capture_output=True,text=True,timeout=600);log.write_text(r.stdout+'\n'+r.stderr)
        if r.returncode:raise ValueError('worker failed: '+str(log))
    try:
        for nodes,ports in MEMORY:
            for repeat in range(2):
                folder=out/f'memory-{nodes}-{ports}-{repeat}';folder.mkdir()
                for mode in ['cached_ports','direct_solve']:execute(['--memory','--nodes',str(nodes),'--ports',str(ports),'--mode',mode,'--out',str(folder/mode)],folder/(mode+'.log'))
                a=np.load(folder/'cached_ports/result.npy');b=np.load(folder/'direct_solve/result.npy');err=float(np.max(abs(a-b)));left=json.loads((folder/'cached_ports/metrics.json').read_text());right=json.loads((folder/'direct_solve/metrics.json').read_text())
                passed=err<=1e-12 and right['numpy_tracemalloc_peak_bytes']<left['numpy_tracemalloc_peak_bytes'] and right['temporary_network_RHS_maximum_columns']<=8
                memory.append(dict(nodes=nodes,ports=ports,repeat=repeat,passed=passed,error=err,cached=left,direct=right))
                if not passed:raise ValueError('memory/action probe failed')
        for name,s in specs.items():
            folder=out/name;folder.mkdir();study=folder/'study.json';study.write_text(s.model_dump_json())
            for repeat in range(2):
                r=folder/str(repeat);r.mkdir()
                for mode in ['cached_ports','direct_solve']:execute(['--worker','--study',str(study),'--mode',mode,'--out',str(r/mode)],r/(mode+'.log'))
                target=r/'comparison.json';execute(['--compare','--left',str(r/'cached_ports'),'--right',str(r/'direct_solve'),'--out',str(target)],r/'comparison.log')
                check=json.loads(target.read_text());rows.append(dict(case=name,repeat=repeat,**check));print(name,repeat,check['passed'],flush=True)
        if code!=package_fingerprint() or sources!=source_identity():raise ValueError('producer changed during qualification')
        result=dict(passed=True,status='complete',code_sha256=code,source_sha256=sources,cases=rows,memory=memory,integration_fresh_processes=60,comparison_fresh_processes=30,algebraic_memory_fresh_processes=8,industrial_release_qualified=False)
        (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n');return result
    except BaseException as exc:
        (out/'summary.json').write_text(json.dumps(dict(passed=False,status='failed',cases=rows,memory=memory,error=type(exc).__name__+': '+str(exc)),indent=2)+'\n');raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');p.add_argument('--compare',action='store_true');p.add_argument('--memory',action='store_true');p.add_argument('--study',type=Path);p.add_argument('--mode',choices=['cached_ports','direct_solve']);p.add_argument('--nodes',type=int);p.add_argument('--ports',type=int);p.add_argument('--left',type=Path);p.add_argument('--right',type=Path);a=p.parse_args()
    if a.worker:worker(a.study,a.mode,a.out)
    elif a.compare:comparison(a.left,a.right,a.out)
    elif a.memory:memory_worker(a.nodes,a.ports,a.mode,a.out)
    else:qualify(a.out)
