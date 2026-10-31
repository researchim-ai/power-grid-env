"""Fresh-process motor-group scaling and analytic/FD equivalence measurements.

Synthetic fixed-total-VA branching feeders, not a target-plant qualification.
Each sample fixes BLAS threads and includes process-lifetime peak RSS explicitly.
"""
from __future__ import annotations
import argparse,hashlib,json,os,resource,statistics,subprocess,sys,tempfile,time
from pathlib import Path
import numpy as np
from power_grid_env.build import NetBuilder
from power_grid_env.induction import InductionMotor,InductionSettings,InductionEvent,MotorOperatingPoint
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.induction_network import InductionNetwork
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint
from power_grid_env.ipc import kill_process_group
from power_grid_env.report import write_json

CASES=[(64,4,False,'cold'),(512,16,False,'cold'),(2000,32,False,'cold'),(2000,32,True,'cold'),(512,16,False,'restart'),(2000,32,True,'restart')]
TRACE_KEYS=['e_real_pu','e_imag_pu','speed_pu','current_pu','bus_voltage_pu']


def make_study(buses,motors,mesh=False,duration=2.,scenario='cold'):
    if not 2<=buses<=2000 or not 1<=motors<=min(32,buses-1):raise ValueError('scale case outside bounded motor/network profile')
    b=NetBuilder(f'motor-scale-{buses}-{motors}-{mesh}','Synthetic fixed-total-VA motor feeder',10.,s_base_va=10e6)
    for i in range(buses):b.node(f'n{i}')
    b.source('n0',.05,.2)
    for i in range(1,buses):
        b.line_z1(f'n{(i-1)//2}',f'n{i}',.02+.06j,line_id=f'l{i}')
        b.load(f'n{i}',100000/(buses-1),20000/(buses-1),model='constant_z',load_id=f'load{i}')
    if mesh:
        for i in range(max(2,buses//8),buses,max(2,buses//8)):
            b.line_z1(f'n{i}',f'n{max(0,i-buses//16)}',.03+.08j,line_id=f'tie{i}')
    machines=[]
    locations=np.linspace(max(1,buses//2),buses-1,motors,dtype=int)
    for i,node in enumerate(locations):
        machines.append(InductionMotor(id=f'm{i}',node_id=f'n{node}',rated_va=1e6/motors,rated_ll_v=10000.,
            stator_r_pu=.02,stator_x_pu=.1,rotor_r_pu=.015,rotor_x_pu=.1,magnetizing_x_pu=3.,inertia_s=.3,fan_torque_pu=.8))
    settings=InductionSettings(duration_s=duration,step_s=.02,motors=machines)
    if scenario=='restart':
        if duration<=1.:raise ValueError('restart horizon must exceed recovery event')
        settings.operating_point=MotorOperatingPoint()
        settings.events=[InductionEvent(time_s=.5,kind='source_voltage',equipment_id=b.sources[0].id,value=.6),InductionEvent(time_s=1.,kind='source_voltage',equipment_id=b.sources[0].id,value=1.)]
    elif scenario!='cold':raise ValueError('unknown motor-scale scenario')
    return StudySpec(id=f'motor-scale-{buses}-{motors}-{mesh}-{scenario}',study='motor_start',backend='scipy',network=b.build(),induction=settings)


def measure(spec,mode):
    if mode not in {'analytic','finite_difference'}:raise ValueError('unknown Jacobian policy')
    counts=dict(rhs_calls=0,electrical_calls=0);rhs=InductionNetwork.rhs;electrical=InductionNetwork.electrical;policy=InductionNetwork.integration_jacobian
    def counted_rhs(self,*args):counts['rhs_calls']+=1;return rhs(self,*args)
    def counted_electrical(self,*args):counts['electrical_calls']+=1;return electrical(self,*args)
    InductionNetwork.rhs=counted_rhs;InductionNetwork.electrical=counted_electrical
    if mode=='finite_difference':InductionNetwork.integration_jacobian=lambda self:None
    cpu=resource.getrusage(resource.RUSAGE_SELF);start=time.perf_counter()
    try:report=run_study(spec)
    finally:
        elapsed=time.perf_counter()-start;end=resource.getrusage(resource.RUSAGE_SELF)
        InductionNetwork.rhs=rhs;InductionNetwork.electrical=electrical;InductionNetwork.integration_jacobian=policy
    result=dict(completed=bool(report.get('completed')),status=report.get('status'),jacobian_policy=mode,solve_seconds=elapsed,
        solve_cpu_seconds=end.ru_utime+end.ru_stime-cpu.ru_utime-cpu.ru_stime,**counts,
        case_sha256=canonical_hash(spec),code_sha256=package_fingerprint(),integration=report.get('integration'),
        maximum_kcl_residual_pu=report.get('maximum_kcl_residual_pu'),maximum_power_balance_residual_pu=report.get('maximum_power_balance_residual_pu'))
    if report.get('completed'):
        result['trace']=dict(time_s=report['time_s'],sample_side=report['sample_side'],channels={m.id+'.'+key:report['channels'][m.id+'.'+key] for m in spec.induction.motors for key in TRACE_KEYS})
        result['motor_summary']=report['motor_summary']
        result['full_result_sha256']=hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    result['process_lifetime_peak_rss_kib_linux']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return result


def fresh_sample(buses,motors,mesh,mode,out,timeout_s=90.,scenario='cold'):
    with tempfile.TemporaryDirectory(prefix='pge-motor-scale-') as tmp:
        root=Path(tmp);env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
        for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','BLIS_NUM_THREADS']:env[key]='1'
        command=[sys.executable,'-m','benchmarks.motor_scale','--child','--buses',str(buses),'--motors',str(motors),'--mode',mode,'--scenario',scenario,'--out',str(root/'result.json')]
        if mesh:command.append('--mesh')
        with (root/'log').open('wb') as log:
            process=subprocess.Popen(command,cwd=root,env=env,stdout=log,stderr=log,stdin=subprocess.DEVNULL,start_new_session=True)
            start=time.monotonic();reason=None
            try:
                while process.poll() is None:
                    if time.monotonic()-start>=timeout_s:reason='timeout'
                    elif (root/'log').stat().st_size>1_000_000 or ((root/'result.json').exists() and (root/'result.json').stat().st_size>32_000_000):reason='output_budget'
                    if reason:kill_process_group(process);break
                    time.sleep(.01)
                if reason:result=dict(completed=False,status=reason)
                elif process.returncode or not (root/'result.json').exists():result=dict(completed=False,status='worker_failed',exit_code=process.returncode)
                elif (root/'result.json').stat().st_size>32_000_000:result=dict(completed=False,status='output_budget')
                else:result=json.loads((root/'result.json').read_text())
                result['fresh_process_wall_s']=time.monotonic()-start
                write_json(out,result);return result
            finally:
                if process.poll() is None:kill_process_group(process)


def compare_samples(a,b):
    if not(a.get('completed') and b.get('completed')):return dict(passed=False,reason='incomplete_sample')
    if a['code_sha256']!=b['code_sha256'] or a['code_sha256']!=package_fingerprint():return dict(passed=False,reason='code_mismatch')
    if a['case_sha256']!=b['case_sha256'] or a['trace']['time_s']!=b['trace']['time_s'] or a['trace']['sample_side']!=b['trace']['sample_side']:return dict(passed=False,reason='case_or_axis_mismatch')
    errors={key:float(max(abs(np.asarray(values)-b['trace']['channels'][key]))) for key,values in a['trace']['channels'].items()}
    return dict(passed=all(np.isfinite(v) and v<=2e-6 for v in errors.values()),maximum_motor_channel_error_pu=max(errors.values()),limit_pu=2e-6)


def run(out,repeats=3,cases=CASES):
    if repeats<1 or repeats>10:raise ValueError('one to ten scale repeats required')
    rows=[]
    for buses,motors,mesh,scenario in cases:
        folder=Path(out)/f'{buses}-{motors}-{mesh}-{scenario}';samples={mode:[] for mode in ['analytic','finite_difference']};checks=[]
        for repeat in range(repeats):
            # Alternate ordering to avoid systematically assigning one policy first.
            for mode in (['analytic','finite_difference'] if repeat%2==0 else ['finite_difference','analytic']):
                result=fresh_sample(buses,motors,mesh,mode,folder/f'{mode}-{repeat}.json',scenario=scenario);samples[mode].append(result)
            checks.append(compare_samples(samples['analytic'][-1],samples['finite_difference'][-1]))
        passed=all(check['passed'] for check in checks)
        summary=dict(buses=buses,motors=motors,mesh=mesh,scenario=scenario,repeats=repeats,passed=passed,comparisons=checks)
        if passed:
            summary['median_solve_seconds']={mode:statistics.median(s['solve_seconds'] for s in values) for mode,values in samples.items()}
            summary['observed_median_ratio_fd_over_analytic']=summary['median_solve_seconds']['finite_difference']/summary['median_solve_seconds']['analytic']
            summary['peak_rss_kib_linux']={mode:max(s['process_lifetime_peak_rss_kib_linux'] for s in values) for mode,values in samples.items()}
            summary['rhs_calls']={mode:[s['rhs_calls'] for s in values] for mode,values in samples.items()}
        rows.append(summary)
    report=dict(passed=all(row['passed'] for row in rows),cases=rows,code_sha256=package_fingerprint(),blas_threads=1,
        horizon_s=2.,output_step_s=.02,total_motor_va=1e6,total_background_w=100000.,
        correctness='Same model analytic-vs-FD numerical equivalence, not an independent large-network physical oracle',
        memory='Each sample is a fresh process; RSS includes import/build/solve/serialization peak, Linux KiB',
        timing='solve_seconds excludes process import, case build and result serialization; wall includes all',
        target_scale_qualified=False,arbitrary_scale_claim=False,field_validated=False,industrial_profile_qualified=False,cross_simulator_speed_claim=False)
    write_json(Path(out)/'summary.json',report);return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--scenario',choices=['cold','restart'],default='cold');p.add_argument('--child',action='store_true');p.add_argument('--buses',type=int);p.add_argument('--motors',type=int);p.add_argument('--mesh',action='store_true');p.add_argument('--mode',choices=['analytic','finite_difference'])
    args=p.parse_args()
    if args.child:
        start=time.perf_counter();spec=make_study(args.buses,args.motors,args.mesh,scenario=args.scenario);build=time.perf_counter()-start
        result=measure(spec,args.mode);result['case_build_seconds']=build;write_json(args.out,result)
    else:
        result=run(args.out,args.repeats);print([(c['buses'],c['motors'],c['mesh'],c['scenario'],c['passed'],c.get('observed_median_ratio_fd_over_analytic')) for c in result['cases']])
        if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
