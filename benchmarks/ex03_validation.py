"""Fresh-process long-run/scale qualification with independent SI MNA.

Resource figures describe each complete process, not arbitrary scale support.
"""
from pathlib import Path
import argparse,json,math,os,resource,statistics,subprocess,sys,time
import numpy as np
from benchmarks.ex03_cases import LONG,SCALES,long_case,scale_case,native_long
from benchmarks.ex03_reference import reference
from benchmarks.native_online_validation import source_identity,match_events
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json
from power_grid_env.ipc import kill_process_group

NODE_LIMIT=2e-5
EVENT_LIMIT=1e-6
BALANCE_LIMIT=1e-8


def case(name):
    if name in LONG:return long_case(name)
    if name=='native_avr':return native_long()
    if name.startswith('scale_'):
        return scale_case(*map(int,name[6:].split('_')))
    raise ValueError('registered EX03 case required')

NAMES=(*LONG,*['scale_'+'_'.join(map(str,p)) for p in SCALES])

def executable_peak_rss_kib(status_file=Path('/proc/self/status')):
    """Peak of the current Linux mm, reset by exec; ru_maxrss is not."""
    for line in Path(status_file).read_text().splitlines():
        if line.startswith('VmHWM:'):
            fields=line.split()
            if len(fields)!=3 or fields[2]!='kB' or not fields[1].isdigit() or int(fields[1])<=0:raise ValueError('invalid current-executable peak RSS')
            return int(fields[1])
    raise ValueError('Linux VmHWM measurement unavailable')

class Work:
    def __init__(self):self.counts=dict(factorizations=0,network_solves=0,maximum_matrix_nnz=0,maximum_LU_nnz=0);self.saved=[]
    def __enter__(self):
        import power_grid_env.induction_network as a,power_grid_env.coupled_rms as b,power_grid_env.coupled_initialization as c,benchmarks.multibus_si_motor as d
        for module in (a,b,c,d):
            original=module.splu;self.saved.append((module,original));counts=self.counts
            def wrapped(matrix,*args,original=original,counts=counts,**kwargs):
                lu=original(matrix,*args,**kwargs);counts['factorizations']+=1
                counts['maximum_matrix_nnz']=max(counts['maximum_matrix_nnz'],matrix.nnz);counts['maximum_LU_nnz']=max(counts['maximum_LU_nnz'],lu.L.nnz+lu.U.nnz)
                class Proxy:
                    def solve(self,*a,**k):counts['network_solves']+=1;return lu.solve(*a,**k)
                    def __getattr__(self,key):return getattr(lu,key)
                return Proxy()
            module.splu=wrapped
        return self
    def __exit__(self,*args):
        for module,original in self.saved:module.splu=original


def worker(name,role,out,primary=None,step=.01,root=None,tolerance=1e-13,study=None):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);start=time.perf_counter();spec=StudySpec.model_validate_json(Path(study).read_text()) if study else case(name);build=time.perf_counter()-start
    before=resource.getrusage(resource.RUSAGE_SELF);start=time.perf_counter()
    with Work() as work:
        if role=='primary':trace=run_study(spec)
        elif role=='reference':
            a=json.loads(Path(primary).read_text());trace=reference(spec,maximum_step_s=step).run(a);trace.update(completed=True,status='completed')
        elif role=='native':
            from benchmarks.esst1a_network import run
            trace=run(spec,out/'native',Path(root),tolerance,native_uels=bool(spec.coupled.underexcitation_limiters),maximum_duration_s=30.,audit_network=True,allow_cold_prestart=True)
        else:raise ValueError('known worker role required')
        elapsed=time.perf_counter()-start;after=resource.getrusage(resource.RUSAGE_SELF)
    write_json(out/'trace.json',trace)
    metrics=dict(schema='power-grid-env.ex03-resource/v2',completed=trace.get('completed') is True,status=trace.get('status'),role=role,case=name,case_sha256=canonical_hash(spec),code_sha256=package_fingerprint(),build_s=build,solve_and_initialization_s=elapsed,solve_cpu_s=after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,network_work=work.counts,network_work_scope='SuperLU calls including initialization; native dense MNA excluded',integration=trace.get('integration'),runtime_budget=trace.get('runtime_budget'),process_peak_rss_kib_linux=executable_peak_rss_kib(),process_lifetime_peak_rss_kib_linux=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,child_process_peak_rss_kib_linux=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,reference_maximum_step_s=step if role=='reference' else None,native_tolerance=tolerance if role=='native' else None,native_build_and_source_verification_included=role=='native',peak_memory_includes_import_build_solve_and_trace_serialization=True,memory_measurement='current exec VmHWM; getrusage lifetime/pre-exec and child-lifetime peaks separate; not process-family concurrent RSS')
    write_json(out/'metrics.json',metrics)


def fresh(name,role,out,*,primary=None,step=.01,root=None,tolerance=1e-13,timeout_s=360.,spec=None):
    if name not in (*NAMES,'native_avr') or role not in ('primary','reference','native') or not math.isfinite(timeout_s) or not 0<timeout_s<=900:raise ValueError('bounded registered worker required')
    out=Path(out).resolve();out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists():raise ValueError('fresh worker directory required')
    spec=spec if spec is not None else case(name)
    study=out.parent/(out.name+'-study.json');write_json(study,spec.model_dump(mode='json'))
    env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
    for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','BLIS_NUM_THREADS'):env[key]='1'
    args=[sys.executable,'-m','benchmarks.ex03_validation','--child','--name',name,'--role',role,'--out',str(out),'--step',str(step),'--tolerance',str(tolerance),'--study',str(study)]
    if primary:args+=['--primary',str(Path(primary).resolve())]
    if root:args+=['--root',str(Path(root).resolve())]
    started=time.monotonic();log=out.with_suffix('.log')
    with log.open('wb') as stream:
        p=subprocess.Popen(args,cwd='/tmp',env=env,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,start_new_session=True)
        reason=None
        try:
            while p.poll() is None:
                if time.monotonic()-started>=timeout_s:reason='worker_timeout'
                elif log.stat().st_size>2_000_000 or any(f.stat().st_size>192_000_000 for f in out.glob('*.json')):reason='worker_output_budget'
                if reason:kill_process_group(p);break
                time.sleep(.02)
            if reason:result=dict(completed=False,status=reason)
            elif p.returncode or not (out/'metrics.json').exists():result=dict(completed=False,status='worker_failed',exit_code=p.returncode,log_tail=log.read_text(errors='replace')[-3000:])
            else:
                result=json.loads((out/'metrics.json').read_text())
                if result.get('case_sha256')!=canonical_hash(spec) or result.get('code_sha256')!=package_fingerprint():raise ValueError('fresh worker input or producer mismatch')
        finally:
            if p.poll() is None:kill_process_group(p)
    result['fresh_process_wall_s']=time.monotonic()-started;write_json(out.parent/(out.name+'-receipt.json'),result);return result


def compare(spec,a,b):
    s=spec.coupled or spec.induction
    if not a.get('completed') or not b.get('completed') or not b.get('independent_SI_network') or b.get('production_stamps_used') is not False:raise ValueError('complete independent traces required')
    if a['time_s']!=b['time_s'] or a['sample_side']!=b['sample_side'] or abs(a['time_s'][-1]-s.duration_s)>1e-10:raise ValueError('full matching horizon and sides required')
    if not b['balance_audit'] or len(b['balance_audit'])!=len(a['time_s']):raise ValueError('all-sample independent physical balance required')
    errors={};limits={}
    for key,value in b['channels'].items():
        if key not in a['channels']:raise ValueError('missing primary state: '+key)
        x=np.asarray(value);y=np.asarray(a['channels'][key])
        if x.shape!=y.shape or not np.isfinite(x).all() or not np.isfinite(y).all():raise ValueError('invalid comparison channel')
        discrete=key.endswith(('.connected','.thermal_latched','voltage_available','pending_restart','restart_count','timer_active'))
        limit=0. if discrete else 100. if key.endswith(('.p_w','.q_var','copper_loss_w')) else 1e-6 if key.endswith('timer_remaining_s') else 2e-5
        errors[key]=float(max(abs(x-y)));limits[key]=limit
    wanted={m.id+'.'+k for m in s.motors for k in ('speed_pu','e_real_pu','e_imag_pu','te_pu','current_pu','connected')}
    for m in s.motors:
        if m.thermal:wanted.update(m.id+'.'+k for k in ('thermal_level','thermal_latched'))
        if m.contactor:wanted.update(m.id+'.contactor_'+k for k in ('sensed_voltage_pu','voltage_available','pending_restart','restart_count','timer_active','timer_remaining_s'))
        if m.model=='double_cage':wanted.update(m.id+'.'+k for k in ('cage1_e_real_pu','cage1_e_imag_pu','cage2_e_real_pu','cage2_e_imag_pu'))
    for g in spec.network.generators:wanted.update(g.id+'.'+k for k in ('delta','omega','e1q','e1d','e2d','e2q','TGOV1.valve_state_pu','TGOV1.turbine_state_pu','SEXS.filter_pu','SEXS.field_state_pu'))
    if not wanted<=errors.keys():raise ValueError('missing independent internal states')
    from power_grid_env.topology import compile_topology
    topology=compile_topology(spec.network);bases={v.id:v.nominal_ll_v/3**.5 for v in spec.network.voltage_levels}
    if set(b['node_voltage'])!={n.id for n in spec.network.connectivity_nodes}:raise ValueError('independent all-node inventory required')
    for node in spec.network.connectivity_nodes:
        bus=topology.node_to_bus[node.id];voltage=np.asarray(a['channels'][bus+'.v'])*np.exp(1j*np.asarray(a['channels'][bus+'.a']))
        for part,target in [('real_v',voltage.real),('imag_v',voltage.imag)]:
            value=np.asarray(b['node_voltage'][node.id][part])/bases[node.voltage_level_id]
            if value.shape!=target.shape or not np.isfinite(value).all():raise ValueError('invalid independent node trace')
            key='node.'+node.id+'.'+part;errors[key]=float(max(abs(value-target)));limits[key]=NODE_LIMIT
    dt=match_events(a['event_log'],b['event_log'])
    residuals=dict(primary_kcl=a['maximum_kcl_residual_pu'],primary_power=a['maximum_power_balance_residual_pu'],reference_kcl=max(r['kcl_pu'] for r in b['balance_audit']),reference_power=max(r['power_balance_pu'] for r in b['balance_audit']))
    passed=all(math.isfinite(v) and v<=limits[k] for k,v in errors.items()) and dt<=EVENT_LIMIT and all(math.isfinite(v) and v<=BALANCE_LIMIT for v in residuals.values())
    return dict(passed=bool(passed),errors=errors,limits=limits,event_error_s=dt,event_limit_s=EVENT_LIMIT,residuals=residuals,balance_limit_pu=BALANCE_LIMIT,all_node_count=len(b['node_voltage']),required_state_channels=len(wanted))


def contract(name,spec,r):
    s=spec.coupled or spec.induction;time_axis=np.asarray(r['time_s']);checks={}
    if abs(time_axis[-1]-s.duration_s)>1e-10:return dict(passed=False,reason='incomplete_horizon')
    for i,m in enumerate(s.motors):
        speed=np.asarray(r['channels'][m.id+'.speed_pu'])
        if name=='gen_thermal_cooling' and i==0:
            events=[e for e in r['event_log'] if e['kind']=='thermal_trip' and e['equipment_id']==m.id]
            checks[m.id+'.trip']=len(events)==1
            if events:
                trip=events[0]['time_s'];mask=time_axis>trip+1e-8;theta=np.asarray(r['channels'][m.id+'.thermal_level']);k=np.where(time_axis>=trip-1e-10)[0][0]
                exact=theta[k]*np.exp(-(time_axis[mask]-time_axis[k])/m.thermal.time_constant_s)
                checks[m.id+'.full_cooling']=s.duration_s-trip>=8*m.thermal.time_constant_s and bool(np.max(abs(theta[mask]-exact))<2e-6) and bool(np.all(np.asarray(r['channels'][m.id+'.connected'])[mask]==0))
                checks[m.id+'.blocked_reclose']=any(e['kind']=='motor_close' and e['equipment_id']==m.id and e.get('outcome')=='blocked_thermal_latch' for e in r['event_log'])
        else:
            checks[m.id+'.completed_runup']=float(speed[-1])>=.95
            if name in (*LONG,'native_avr'):checks[m.id+'.pre_dip_runup']=float(speed[np.argmin(abs(time_axis-10.))])>=.95
            if 'restart' in name or name in ('gen_thermal_cooling','native_avr'):
                opens=[e for e in r['event_log'] if e['kind']=='motor_open' and e['equipment_id']==m.id and e['time_s']>=12.]
                def closes_breaker(e):
                    near=abs(time_axis-e['time_s'])<=1e-6;connected=np.asarray(r['channels'][m.id+'.connected']);sides=np.asarray(r['sample_side'])
                    return bool(np.any(near&(sides=='pre_event')&(connected==0)) and np.any(near&(sides=='post_event')&(connected==1)))
                closes=[e for e in r['event_log'] if e['kind']=='motor_close' and e['equipment_id']==m.id and e['time_s']>=14. and closes_breaker(e)]
                checks[m.id+'.coast_and_restart']=bool(opens and closes) and min(speed[(time_axis>=12.)&(time_axis<=14.5)])<.9
    if name=='native_avr':
        for p in s.esst1a_exciters:
            field=np.asarray(r['channels'][p.generator_id+'.ESST1A.field_pu']);checks[p.generator_id+'.field_responds_and_settles']=float(max(field)-min(field))>1e-4 and float(np.ptp(field[time_axis>=s.duration_s-2]))<.02
    return dict(passed=all(checks.values()),checks={k:bool(v) for k,v in checks.items()})


def qualify(out,names=NAMES,repeats=2,root=None):
    if not names or len(set(names))!=len(names) or not set(names)<=set(NAMES) or type(repeats) is not int or not 2<=repeats<=5:raise ValueError('registered cases and two to five repeats required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();sources=source_identity();rows=[]
    snapshots={n:case(n) for n in names};native_spec=native_long() if root else None
    write_json(out/'protocol.json',dict(code_sha256=code,source_sha256=sources,cases={n:s.model_dump(mode='json') for n,s in snapshots.items()},native_case=native_spec.model_dump(mode='json') if root else None,repeats=repeats,node_limit_pu=NODE_LIMIT,event_limit_s=EVENT_LIMIT,balance_limit_pu=BALANCE_LIMIT,reference_method='independent SI winding sparse MNA with DOP853; own PV initialization; own protection decisions',reference_maximum_step_s=.01,refinement_maximum_step_s=.005,refined_cases=['gen_thermal_cooling','scale_512_32_4'],native_tolerances=[1e-12,1e-13] if root else [],BLAS_threads=1,timeout_s=360.,performance_comparison_to_other_simulators=False,memory_measurement='resource/v2: current executable Linux VmHWM; getrusage includes pre-exec peaks and is retained separately'))
    write_json(out/'summary.json',dict(passed=False,status='running'))
    try:
        for name in names:
            print(name,flush=True);s=snapshots[name];samples={'primary':[],'reference':[]};checks=[]
            for i in range(repeats):
                a=fresh(name,'primary',out/name/f'primary-{i}',spec=s);samples['primary'].append(a)
                if not a['completed']:raise ValueError('primary failed: '+str(a))
                b=fresh(name,'reference',out/name/f'reference-{i}',primary=out/name/f'primary-{i}/trace.json',spec=s);samples['reference'].append(b)
                if not b['completed']:raise ValueError('reference failed: '+str(b))
                x=json.loads((out/name/f'primary-{i}/trace.json').read_text());y=json.loads((out/name/f'reference-{i}/trace.json').read_text())
                c=compare(s,x,y);c['physical_contract']=contract(name,s,x);c['independent_physical_contract']=contract(name,s,y);c['passed']=c['passed'] and c['physical_contract']['passed'] and c['independent_physical_contract']['passed'];write_json(out/name/f'comparison-{i}.json',c);checks.append(c)
            refined=None
            if name in ('gen_thermal_cooling','scale_512_32_4'):
                r=fresh(name,'reference',out/name/'refined',primary=out/name/'primary-0/trace.json',step=.005,spec=s)
                if not r['completed']:raise ValueError('refined SI failed: '+str(r))
                refined=compare(s,json.loads((out/name/'primary-0/trace.json').read_text()),json.loads((out/name/'refined/trace.json').read_text()));write_json(out/name/'refinement.json',refined)
            row=dict(case=name,passed=all(c['passed'] for c in checks) and (refined is None or refined['passed']),checks=checks,refinement=refined,samples=samples,median_solve_and_initialization_s={role:statistics.median(r['solve_and_initialization_s'] for r in values) for role,values in samples.items()})
            rows.append(row);write_json(out/'progress.json',dict(cases=rows));print(' ',row['passed'],flush=True)
        native_checks=[]
        if root:
            from benchmarks.generator_esst1a_validation import compare as compare_native
            for i,tolerance in enumerate((1e-12,1e-13)):
                a=fresh('native_avr','primary',out/f'native-avr/primary-{i}',spec=native_spec)
                if not a['completed']:raise ValueError('long AVR primary failed: '+str(a))
                b=fresh('native_avr','native',out/f'native-avr/reference-{i}',primary=out/f'native-avr/primary-{i}/trace.json',root=root,tolerance=tolerance,spec=native_spec)
                if not b['completed']:raise ValueError('long AVR native failed: '+str(b))
                x=json.loads((out/f'native-avr/primary-{i}/trace.json').read_text());y=json.loads((out/f'native-avr/reference-{i}/trace.json').read_text())
                c=compare_native(native_spec,x,y);c['physical_contract']=contract('native_avr',native_spec,x);c['independent_physical_contract']=contract('native_avr',native_spec,y)
                balance=y.get('network_balance',[]);c['native_balance_complete']=len(balance)==len(y['time_s'])
                c['maximum_native_balance_pu']=max((max(r.values()) for r in balance),default=math.inf)
                c['passed']=c['passed'] and c['physical_contract']['passed'] and c['independent_physical_contract']['passed'] and c['native_balance_complete'] and c['maximum_native_balance_pu']<=BALANCE_LIMIT
                write_json(out/f'native-avr/comparison-{i}.json',c);native_checks.append(c)
        failures=[]
        base=scale_case(*SCALES[0])
        for label,key,value in [('rhs','maximum_rhs_evaluations',1),('output','maximum_retained_scalars',10),('nodes','maximum_network_buses',1)]:
            s=base.model_copy(deep=True);setattr(s.coupled.runtime_limits,key,value)
            r=run_study(s);write_json(out/f'negative-{label}.json',r)
            specific=(r.get('status')=='rejected' and any('implementation budget' in e for e in r.get('scope',{}).get('errors',[]))) if label=='nodes' else r.get('reason')==('rhs_evaluations' if label=='rhs' else 'retained_scalars')
            failures.append(dict(resource=label,passed=not r.get('completed') and specific and 'channels' not in r))
        if code!=package_fingerprint() or sources!=source_identity():raise ValueError('EX03 producer changed')
        result=dict(passed=all(r['passed'] for r in rows) and all(r['passed'] for r in native_checks) and all(r['passed'] for r in failures),status='complete',registered_corpus_complete=set(names)==set(NAMES),native_long_qualified=bool(root) and all(r['passed'] for r in native_checks),cases=rows,native=native_checks,negative_budget_checks=failures,code_sha256=code,source_sha256=sources,arbitrary_scale_qualified=False,industrial_profile_qualified=False,competitive_superiority_proven=False)
        write_json(out/'summary.json',result);return result
    except Exception as exc:
        write_json(out/'summary.json',dict(passed=False,status='failed',cases=rows,error=str(exc),code_sha256=code,source_sha256=sources));raise

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--child',action='store_true');p.add_argument('--name');p.add_argument('--role');p.add_argument('--primary',type=Path);p.add_argument('--study',type=Path);p.add_argument('--root',type=Path);p.add_argument('--step',type=float,default=.01);p.add_argument('--tolerance',type=float,default=1e-13);p.add_argument('--cases',nargs='+',choices=NAMES,default=NAMES);a=p.parse_args()
    if a.child:worker(a.name,a.role,a.out,a.primary,a.step,a.root,a.tolerance,a.study)
    else:
        r=qualify(a.out,a.cases,root=a.root);print(json.dumps(dict(passed=r['passed'])));raise SystemExit(0 if r['passed'] else 1)
