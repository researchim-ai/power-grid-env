"""Preregistered DAE/Radau, extended SI and memory validation in fresh workers."""
from pathlib import Path
import argparse,json,os,subprocess,sys,time,tracemalloc
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from benchmarks.native_online_validation import source_identity
from benchmarks.dae_cases import NAMES,SCALES,case,scale_case
from benchmarks.coupled_jacobian_validation import compare as trajectory_compare
from benchmarks.jacobian_scaling_validation import current_exec_peak_rss_kib


def worker(study,mode,out):
    from unittest.mock import patch
    import power_grid_env.network_sensitivity as ns
    s=StudySpec.model_validate_json(Path(study).read_text());p=s.coupled or s.induction
    p.jacobian_format='csc';p.jacobian_network='direct_solve';p.integration_method='network_dae' if mode=='dae' else 'radau';p.event_policy='coincident_roots_v1';s=StudySpec.model_validate(s.model_dump(mode='json'))
    def forbidden(*a,**kw):raise ValueError('port-square reduction used by explicit direct profile')
    started=time.perf_counter()
    with patch.object(ns,'port_response',forbidden):r=run_study(s)
    out=Path(out);out.mkdir(parents=True,exist_ok=False);(out/'trace.json').write_text(json.dumps(r,allow_nan=False)+'\n')
    (out/'metrics.json').write_text(json.dumps(dict(completed=r.get('completed',False),mode=mode,wall_s=time.perf_counter()-started,current_exec_peak_rss_kib_linux=current_exec_peak_rss_kib(),code_sha256=package_fingerprint(),study_sha256=canonical_hash(s),integration=r.get('integration'),runtime_budget=r.get('runtime_budget')),indent=2)+'\n')
    if not r.get('completed'):raise ValueError('DAE corpus worker failed: '+str(r.get('reason',r.get('solver_message',r.get('status')))))
    expected='analytic_Schur_products' if mode=='dae' else 'column_blocks'
    if r['integration']['jacobian_assembly']!=expected:raise ValueError('wrong declared integration path')


def compare(left,right,out):
    a=json.loads(Path(left).read_text());b=json.loads(Path(right).read_text())
    if a.get('code_sha256')!=package_fingerprint() or b.get('code_sha256')!=package_fingerprint():raise ValueError('comparison producer mismatch')
    if a.get('network_sha256')!=b.get('network_sha256') or a.get('study_id')!=b.get('study_id'):raise ValueError('different physical peer study')
    sa=dict(a['settings']);sb=dict(b['settings']);sa.pop('integration_method',None);sb.pop('integration_method',None)
    if sa!=sb:raise ValueError('physical or numerical policy changed beyond the declared integrator')
    result=trajectory_compare(a,b)
    for k,v in result['channel_errors'].items():
        if k.endswith(('.connected','.thermal_latched','.stator_thermal_latched','.field_thermal_latched','.field_voltage_enabled','.voltage_gate','_active','_expired')) and v!=0:result['passed']=False
    Path(out).write_text(json.dumps(result,indent=2)+'\n')
    if not result['passed']:raise ValueError('DAE/Radau comparison failed')


def compare_si(study,primary,reference,out):
    from power_grid_env.topology import compile_topology
    s=StudySpec.model_validate_json(Path(study).read_text());a=json.loads(Path(primary).read_text());b=json.loads(Path(reference).read_text())
    if not a.get('completed') or not b.get('completed') or b.get('code_sha256')!=package_fingerprint() or a.get('code_sha256')!=package_fingerprint() or b.get('source_sha256')!=source_identity() or b['study_sha256']!=canonical_hash(s):raise ValueError('SI/primary identity or completion mismatch')
    if a.get('network_sha256')!=canonical_hash(s.network) or a.get('study_id')!=s.id:raise ValueError('SI comparison primary input mismatch')
    expected=s.coupled.model_copy(deep=True);expected.integration_method='network_dae' if a['integration']['method']=='RadauIIA_index1_network_DAE' else 'radau'
    if a['settings']!=expected.model_dump(mode='json'):raise ValueError('SI comparison primary settings mismatch')
    at=np.asarray(a['time_s']);ids=[]
    for moment,side in zip(b['time_s'],b['sample_side']):
        matches=[int(i) for i in np.flatnonzero(abs(at-moment)<=1e-9) if a['sample_side'][i]==side]
        if not matches:matches=[int(i) for i in np.flatnonzero(abs(at-moment)<=1e-9)]
        if not matches:raise ValueError('missing authored SI sample')
        ids.append(matches[0] if side=='pre_event' else matches[-1])
    required=set()
    for m in s.coupled.motors:
        keys=['speed_pu','e_real_pu','e_imag_pu','current_pu','bus_voltage_pu','p_w','q_var','te_pu','connected']
        if m.model=='double_cage':keys+=['cage1_e_real_pu','cage1_e_imag_pu','cage2_e_real_pu','cage2_e_imag_pu','rotor1_current_pu','rotor2_current_pu','rotor1_copper_loss_w','rotor2_copper_loss_w']
        required.update(m.id+'.'+k for k in keys)
    for g in s.network.generators:required.update(g.id+'.'+k for k in ['delta','omega','p_w','q_var','current_pu','e1q','e1d','e2d','e2q','psi2d','psi2q','Se','field_pu','TGOV1.valve_pu','TGOV1.turbine_state_pu','TGOV1.output_pu','SEXS.field_pu','SEXS.filter_pu','SEXS.reference_pu','SEXS.error_pu','terminal_voltage_pu','connected'])
    if not required<=b['channels'].keys() or not required<=a['channels'].keys():raise ValueError('missing required extended SI channel')
    errors={};passed=True
    for k in sorted(required):
        x=np.asarray(a['channels'][k])[ids];y=np.asarray(b['channels'][k]);error=float(np.max(abs(x-y)))
        if x.shape!=y.shape or not np.isfinite([*x,*y]).all():raise ValueError('invalid SI channel')
        limit=0. if k.endswith('.connected') else 1. if k.endswith(('_w','_var')) else 2e-5
        errors[k]=error;passed=passed and error<=limit
    buses=compile_topology(s.network).node_to_bus;node_error=0.
    if set(b['node_voltage'])!={n.id for n in s.network.connectivity_nodes}:raise ValueError('SI node inventory differs')
    for node,voltage in b['node_voltage'].items():
        expected=np.asarray(a['channels'][buses[node]+'.v'])[ids]*np.exp(1j*np.asarray(a['channels'][buses[node]+'.a'])[ids]);measured=np.asarray(voltage['real_pu'])+1j*np.asarray(voltage['imag_pu'])
        if measured.shape!=expected.shape or not np.isfinite(measured).all():raise ValueError('invalid all-node SI voltage')
        node_error=max(node_error,float(np.max(abs(expected-measured))))
    balances=b.get('network_balance',[])
    if len(balances)!=len(b['time_s']) or not balances:raise ValueError('missing SI network balances')
    values=[v for row in balances for v in row.values()]
    if not np.isfinite(values).all() or min(values)<0:raise ValueError('invalid SI physical balance')
    maximum_balance=max(values);physical={'motor_open','motor_close','source_voltage'}
    left=[(e['time_s'],e['kind'],e['equipment_id']) for e in a['event_log'] if e['kind'] in physical];right=[(e['time_s'],e['kind'],e['equipment_id']) for e in b['event_log']]
    if left!=right:raise ValueError('authored SI command sequence differs')
    passed=passed and node_error<=2e-5 and maximum_balance<=1e-8 and a['maximum_kcl_residual_pu']<=1e-8 and a['maximum_power_balance_residual_pu']<=1e-8
    result=dict(passed=bool(passed),channel_errors=errors,maximum_node_complex_voltage_error_pu=node_error,maximum_SI_balance_pu=maximum_balance,compared_channels=len(required),compared_nodes=len(buses),sampling='authored grid and external event sides; continuous regulator roots qualified separately')
    Path(out).write_text(json.dumps(result,indent=2)+'\n')
    if not passed:raise ValueError('independent extended SI comparison failed')


def memory_worker(study,mode,out):
    from power_grid_env.coupled_rms import CoupledNetwork
    from power_grid_env.coupled_initialization import initialize_coupled
    from power_grid_env.dae_linearization import DAEBlocks
    from power_grid_env.sparse_jacobian import assemble
    s=StudySpec.model_validate_json(Path(study).read_text());m=CoupledNetwork(s.network,s.coupled);x,init=initialize_coupled(m)
    if x is None:raise ValueError('memory snapshot initialization failed')
    m.connected[:]=True;m.factorize()
    for i in range(len(m.settings.motors)):x[3*i:3*i+3]=[.3,.1,.8]
    for i,a in m.cages.addresses.items():x[a]=[.05,.02]
    cb=m.integration_jacobian();v=np.random.default_rng(52).normal(size=len(x));tracemalloc.start();started=time.perf_counter()
    if mode=='dae':
        blocks=DAEBlocks(m,cb,0.,x,32,lambda:None,lambda:None);result=blocks.action(v);stored=blocks.stored_local_scalar_slots
    else:matrix=assemble(cb,0.,x);result=matrix@v;stored=int(2*matrix.nnz+len(matrix.indptr))
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop();out=Path(out);out.mkdir(parents=True,exist_ok=False);np.save(out/'product.npy',result)
    (out/'metrics.json').write_text(json.dumps(dict(mode=mode,states=len(x),stored_scalar_slots=stored,numpy_tracemalloc_peak_bytes=peak,wall_s=time.perf_counter()-started,current_exec_peak_rss_kib_linux=current_exec_peak_rss_kib(),code_sha256=package_fingerprint(),scope='transient derivative snapshot, not a full trajectory'),indent=2)+'\n')


def qualify(out):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();sources=source_identity();rows=[];scales=[];memories=[]
    specs={n:case(n) for n in NAMES}
    from benchmarks.ex03_cases import LONG,long_case
    specs.update({n:long_case(n) for n in LONG})
    big={f'scale-{n}-{m}-{g}':scale_case(n,m,g,voltage_profile='nominal_1p0' if g==32 else 'original_0995') for n,m,g in SCALES}
    (out/'protocol.json').write_text(json.dumps(dict(code_sha256=code,source_sha256=sources,cases={n:s.model_dump(mode='json') for n,s in specs.items()},extended_cases={n:s.model_dump(mode='json') for n,s in big.items()},modes=['radau','dae'],paired_workers_concurrent=True,timing_is_not_controlled_competitive_benchmark=True,repeats=2,worker_timeout_s=1200,physical_tolerances=dict(state=2e-5,power_w_var=100.,events_s=1e-6,balance_pu=1e-8),SI_tolerances=dict(state=2e-5,power_w_var=1.,voltage_pu=2e-5),arbitrary_scale_qualified=False),indent=2)+'\n')
    (out/'summary.json').write_text(json.dumps(dict(passed=False,status='running'))+'\n');env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    def execute(module,args,log):
        p=subprocess.run([sys.executable,'-m',module,*args],env=env,capture_output=True,text=True,timeout=1200);log.write_text(p.stdout+'\n'+p.stderr)
        if p.returncode:raise ValueError('worker failed: '+str(log))
    try:
        for name,s in {**specs,**big}.items():
            folder=out/name;folder.mkdir();study=folder/'study.json';study.write_text(s.model_dump_json())
            for repeat in range(2):
                part=folder/str(repeat);part.mkdir()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    pending=[pool.submit(execute,'benchmarks.dae_validation',['--worker','--study',str(study),'--mode',mode,'--out',str(part/mode)],part/(mode+'.log')) for mode in ['radau','dae']]
                    for future in pending:future.result()
                target=part/'comparison.json';execute('benchmarks.dae_validation',['--compare','--left',str(part/'radau/trace.json'),'--right',str(part/'dae/trace.json'),'--out',str(target)],part/'comparison.log');check=json.loads(target.read_text());rows.append(dict(case=name,repeat=repeat,**check));print(name,repeat,check['passed'],flush=True)
            if name in big:
                execute('benchmarks.dae_scale_reference',['--study',str(study),'--out',str(folder/'independent-SI')],folder/'SI.log')
                for mode in ['radau','dae']:
                    target=folder/(mode+'-SI-comparison.json');execute('benchmarks.dae_validation',['--compare-si','--study',str(study),'--left',str(folder/'0'/mode/'trace.json'),'--right',str(folder/'independent-SI/reference.json'),'--out',str(target)],folder/(mode+'-SI.log'));scales.append(dict(case=name,mode=mode,**json.loads(target.read_text())))
                for mode in ['radau','dae']:execute('benchmarks.dae_validation',['--memory','--study',str(study),'--mode',mode,'--out',str(folder/('memory-'+mode))],folder/('memory-'+mode+'.log'))
                left=np.load(folder/'memory-radau/product.npy');right=np.load(folder/'memory-dae/product.npy');a=json.loads((folder/'memory-radau/metrics.json').read_text());b=json.loads((folder/'memory-dae/metrics.json').read_text());error=float(np.max(abs(left-right)));passed=error<=2e-9 and b['numpy_tracemalloc_peak_bytes']<a['numpy_tracemalloc_peak_bytes'];memories.append(dict(case=name,passed=passed,error=error,radau=a,dae=b))
                if not passed:raise ValueError('DAE memory/product evidence failed')
        if code!=package_fingerprint() or sources!=source_identity():raise ValueError('producer changed during DAE qualification')
        result=dict(passed=True,status='complete',code_sha256=code,source_sha256=sources,trajectory_cases=len(specs)+len(big),comparisons=rows,independent_SI_comparisons=scales,memory=memories,industrial_release_qualified=False,independent_host_verified=False)
        (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n');return result
    except BaseException as exc:
        (out/'summary.json').write_text(json.dumps(dict(passed=False,status='failed',comparisons=rows,independent_SI_comparisons=scales,memory=memories,error=type(exc).__name__+': '+str(exc)),indent=2)+'\n');raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--study',type=Path);p.add_argument('--mode',choices=['radau','dae']);p.add_argument('--worker',action='store_true');p.add_argument('--compare',action='store_true');p.add_argument('--compare-si',action='store_true');p.add_argument('--memory',action='store_true');p.add_argument('--left',type=Path);p.add_argument('--right',type=Path);a=p.parse_args()
    if a.worker:worker(a.study,a.mode,a.out)
    elif a.compare:compare(a.left,a.right,a.out)
    elif a.compare_si:compare_si(a.study,a.left,a.right,a.out)
    elif a.memory:memory_worker(a.study,a.mode,a.out)
    else:qualify(a.out)
