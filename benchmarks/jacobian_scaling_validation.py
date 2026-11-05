"""Fresh-process exact dense/CSC trajectory and blocked-port memory evidence."""
from pathlib import Path
import argparse,json,os,resource,subprocess,sys,tracemalloc
import numpy as np
from power_grid_env.study_spec import StudySpec
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint
from benchmarks.native_online_validation import source_identity
from benchmarks.coupled_jacobian_validation import compare,worker as analytic_worker

def current_exec_peak_rss_kib():
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith('VmHWM:'):return int(line.split()[1])
    raise ValueError('Linux current-exec VmHWM unavailable')


NAMES=('classical_trip','round_limits','esst_trip','uel_trip','oel_regulating','batch_four','scale_classical_512','scale_round_512','scale_esst_2000')


def case(name):
    if name not in NAMES:raise ValueError('registered scaling case required')
    if name=='classical_trip':
        from benchmarks.coupled_jacobian_validation import case as make;s=make('generator_thermal_trip')
    elif name in ('round_limits','scale_round_512'):
        from benchmarks.genrou_jacobian_validation import case as make;s=make('genrou_limits_recovery' if name=='round_limits' else 'scale_512_32_4')
    elif name=='esst_trip':
        from benchmarks.generator_esst1a_cases import example;s=example('field_trip')
    elif name=='uel_trip':
        from benchmarks.generator_underexcitation_cases import example;s=example('field_trip')
    elif name=='oel_regulating':
        from benchmarks.generator_oel2c_regulating_cases import example;s=example('voltage_up')
    elif name=='batch_four':
        from benchmarks.oel2c_batch_validation import case as make;s=make('cold__four_machines')
    elif name=='scale_classical_512':
        from benchmarks.coupled_jacobian_validation import case as make;s=make('scale_512_32_4')
    else:
        from benchmarks.esst1a_jacobian_validation import case as make;s=make('scale_2000_8_4')
    s.id='storage-scaling-'+name
    return StudySpec.model_validate(s.model_dump(mode='json'))


def trajectory_worker(study,out):
    from power_grid_env.coupled_rms import CoupledNetwork
    s=StudySpec.model_validate_json(Path(study).read_text());m=CoupledNetwork(s.network,s.coupled);callback=m.integration_jacobian()
    if callback is None:raise ValueError('expected analytic profile')
    analytic_worker(study,'analytic',out,analytic_kind=callback.jacobian_kind)
    p=Path(out);trace=json.loads((p/'trace.json').read_text());metrics=json.loads((p/'metrics.json').read_text())
    if trace['integration']['jacobian_format']!=s.coupled.jacobian_format:raise ValueError('storage route mismatch')
    metrics.update(jacobian_format=s.coupled.jacobian_format,current_exec_peak_rss_kib_linux=current_exec_peak_rss_kib(),process_lifetime_peak_rss_kib_linux=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    (p/'metrics.json').write_text(json.dumps(metrics,indent=2)+'\n')


def memory_worker(size,mode,out):
    from scipy.sparse import diags
    from scipy.sparse.linalg import splu
    from types import SimpleNamespace
    from power_grid_env.network_sensitivity import port_response
    n=size;ports=40;buses=np.linspace(0,n-1,ports,dtype=int);y=np.full(ports,.2+.1j);lu=splu(diags([np.full(n-1,-.1j),np.full(n,2.+1j),np.full(n-1,-.1j)],[-1,0,1],format='csc'))
    widths=[]
    def solve(rhs):widths.append(rhs.shape[1]);return lu.solve(rhs)
    model=SimpleNamespace(bus_names=range(n),lu=SimpleNamespace(solve=solve));tracemalloc.start()
    if mode=='legacy':
        rhs=np.zeros((n,ports),complex);rhs[buses,np.arange(ports)]=y;response=solve(rhs)[buses,:]
    else:response=port_response(model,buses,y,buses)
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop();out=Path(out);out.mkdir(parents=True)
    np.save(out/'response.npy',response)
    (out/'metrics.json').write_text(json.dumps(dict(size=n,ports=ports,mode=mode,max_rhs_columns=max(widths),numpy_tracemalloc_peak_bytes=peak,current_exec_peak_rss_kib_linux=current_exec_peak_rss_kib(),process_lifetime_peak_rss_kib_linux=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,code_sha256=package_fingerprint()),indent=2)+'\n')


def qualify(out):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();source=source_identity();specs={n:case(n) for n in NAMES};env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    protocol=dict(code_sha256=code,source_sha256=source,cases={n:s.model_dump(mode='json') for n,s in specs.items()},storage=['dense','csc'],repeats=2,memory_sizes=[2000,20000],memory_ports=40,worker_timeout_s=180,limits=dict(state=2e-5,power_w_var=100.,event_s=1e-6,balance_pu=1e-8),scope='same physical RHS; exact CSC of dense assembly, not a sparse DAE or independent physics oracle; synthetic linear memory probe is not a 20000-bus trajectory')
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n');rows=[];memory=[];(out/'summary.json').write_text(json.dumps(dict(passed=False,status='running'))+'\n')
    def execute(args,log):
        p=subprocess.run([sys.executable,'-m','benchmarks.jacobian_scaling_validation',*args],env=env,capture_output=True,text=True,timeout=180);log.parent.mkdir(parents=True,exist_ok=True);log.write_text(p.stdout+'\n'+p.stderr)
        if p.returncode:raise ValueError('worker failed: '+str(log))
    try:
        for name,s in specs.items():
            folder=out/name;folder.mkdir()
            for repeat in range(2):
                traces={};metrics={}
                for storage in ['dense','csc']:
                    spec=s.model_copy(deep=True);spec.coupled.jacobian_format=storage;study=folder/(storage+'.json');study.write_text(spec.model_dump_json());target=folder/(storage+'-'+str(repeat))
                    execute(['--child','--study',str(study),'--out',str(target)],folder/(target.name+'.log'))
                    traces[storage]=json.loads((target/'trace.json').read_text());metrics[storage]=json.loads((target/'metrics.json').read_text())
                    if metrics[storage]['code_sha256']!=code or metrics[storage]['case_sha256']!=canonical_hash(spec):raise ValueError('trajectory producer/input mismatch')
                check=compare(traces['dense'],traces['csc']);rows.append(dict(case=name,repeat=repeat,comparison=check,metrics=metrics));print(name,repeat,check['passed'],flush=True)
                if not check['passed']:raise ValueError('dense/CSC trajectory differs: '+name)
        for size in [2000,20000]:
            for repeat in range(2):
                data={};arrays={}
                for mode in ['legacy','blocked']:
                    target=out/'memory'/f'{size}-{repeat}-{mode}';execute(['--memory',str(size),'--memory-mode',mode,'--out',str(target)],target.with_suffix('.log'));data[mode]=json.loads((target/'metrics.json').read_text());arrays[mode]=np.load(target/'response.npy')
                if not np.array_equal(arrays['legacy'],arrays['blocked']) or data['blocked']['max_rhs_columns']>8 or data['blocked']['numpy_tracemalloc_peak_bytes']>=data['legacy']['numpy_tracemalloc_peak_bytes']:raise ValueError('memory or exact response criterion failed')
                if any(d['code_sha256']!=code for d in data.values()):raise ValueError('memory producer mismatch')
                memory.append(dict(size=size,repeat=repeat,passed=True,metrics=data))
        if code!=package_fingerprint() or source!=source_identity():raise ValueError('producer changed during qualification')
        result=dict(passed=True,status='complete',cases=rows,memory=memory,code_sha256=code,source_sha256=source,independent_host_verified=False,industrial_release_qualified=False)
        (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n');return result
    except BaseException as exc:
        (out/'summary.json').write_text(json.dumps(dict(passed=False,status='failed',cases=rows,memory=memory,error=type(exc).__name__+': '+str(exc)),indent=2)+'\n');raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--child',action='store_true');p.add_argument('--study',type=Path);p.add_argument('--memory',type=int);p.add_argument('--memory-mode',choices=['legacy','blocked']);a=p.parse_args()
    if a.child:trajectory_worker(a.study,a.out)
    elif a.memory:memory_worker(a.memory,a.memory_mode,a.out)
    else:qualify(a.out)
