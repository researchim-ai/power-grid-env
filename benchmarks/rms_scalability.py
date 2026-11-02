"""Fresh-process sparse RMS measurements with independent closed-form port oracle.

Binary-tree radial networks, one motor at the last node and optional GENCLS at
root. Measures short dynamic windows, not arbitrary topology/long-run capacity.
"""
from pathlib import Path
import argparse,json,math,resource,subprocess,sys,time
import numpy as np
from power_grid_env.build import NetBuilder
from power_grid_env.induction import InductionMotor,InductionSettings
from power_grid_env.coupled_rms import CoupledSettings
from power_grid_env.rms_study import ClassicalMachine
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.runtime_limits import RuntimeLimits
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.report import write_json,sha256_file
from power_grid_env.topology import compile_topology

ZS=.01+.05j
ZL=.1+.2j
LIMIT_PU=1e-7


def example(size,coupled=False):
    if not 2<=size<=20000:raise ValueError('scale family requires 2..20000 buses')
    b=NetBuilder(f'rms-scale-{size}-{coupled}','Radial binary-tree RMS scale',10.,s_base_va=10e6)
    for i in range(size):b.node('n'+str(i))
    b.source('n0',ZS.real,ZS.imag)
    for i in range(1,size):b.line_z1('n'+str((i-1)//2),'n'+str(i),ZL,line_id='l'+str(i))
    motor=InductionMotor(id='m',node_id='n'+str(size-1),rated_va=1e6,rated_ll_v=10000.,stator_r_pu=.02,stator_x_pu=.1,rotor_r_pu=.015,rotor_x_pu=.1,magnetizing_x_pu=3.,inertia_s=.5,fan_torque_pu=.5)
    runtime=RuntimeLimits(maximum_network_buses=max(2000,size))
    data=dict(duration_s=.02,step_s=.005,rtol=1e-8,atol=1e-10,motors=[motor],runtime_limits=runtime)
    if coupled:
        b.generator('n0',100000.,mode='pv',v_set_pu=1.,q_min_var=-10e6,q_max_var=10e6,generator_id='g')
        settings=CoupledSettings(**data,machines=[ClassicalMachine(generator_id='g',rated_va=2e6,inertia_s=2.,damping_pu=1.,x_d_prime_pu=.25)])
        return StudySpec(id=b.network_id,study='coupled_rms',backend='scipy',network=b.build(),coupled=settings)
    return StudySpec(id=b.network_id,study='motor_start',backend='scipy',network=b.build(),induction=InductionSettings(**data))


def check(spec,report):
    if not report.get('completed'):return dict(passed=False,status=report.get('status'),reason=report.get('reason'))
    n=len(spec.network.connectivity_nodes);settings=spec.coupled or spec.induction;m=settings.motors[0]
    depth=(n).bit_length()-1;path={};at=n-1
    while True:
        path[at]=(at+1).bit_length()-1
        if at==0:break
        at=(at-1)//2
    common=[]
    for node in range(n):
        p=node
        while p not in path:p=(p-1)//2
        common.append(path[p])
    common=np.asarray(common);vb=10000./math.sqrt(3);ibase=m.rated_va/(3*vb)
    zm=complex(m.stator_r_pu,m.stator_x_pu+m.magnetizing_x_pu*m.rotor_x_pu/(m.magnetizing_x_pu+m.rotor_x_pu))*10000.**2/m.rated_va
    mapping=compile_topology(spec.network).node_to_bus # port labels only, no admittance stamps
    ch=report['channels'];error=0.;current_error=0.;generator_error=0.
    for j,t in enumerate(report['time_s']):
        em=complex(ch['m.e_real_pu'][j],ch['m.e_imag_pu'][j])*vb
        if spec.coupled:
            p=settings.machines[0];zg=1j*p.x_d_prime_pu*10000.**2/p.rated_va
            eg=report['operating_point']['generator_initial']['g']['internal_voltage_pu']*vb*np.exp(1j*ch['g.delta'][j])
            yl=1/(depth*ZL);ys=1/ZS;yg=1/zg;ym=1/zm
            root,leaf=np.linalg.solve(np.array([[ys+yg+yl,-yl],[-yl,yl+ym]],complex),[ys*vb+yg*eg,ym*em])
            im=(leaf-em)/zm;ig=(eg-root)/zg
            generator_error=max(generator_error,abs(abs(ig)/(p.rated_va/(3*vb))-ch['g.current_pu'][j]))
        else:
            im=(vb-em)/(ZS+depth*ZL+zm);root=vb-ZS*im
        expected=root-common*ZL*im
        actual=np.array([ch[mapping['n'+str(i)]+'.v'][j]*np.exp(1j*ch[mapping['n'+str(i)]+'.a'][j])*vb for i in range(n)])
        error=max(error,float(max(abs(actual-expected))/vb))
        current_error=max(current_error,abs(abs(im)/ibase-ch['m.current_pu'][j]))
    return dict(passed=bool(max(error,current_error,generator_error)<=LIMIT_PU),maximum_node_voltage_error_pu=error,maximum_motor_current_error_pu=float(current_error),maximum_generator_current_error_pu=float(generator_error),ports_checked=n*len(report['time_s']),oracle='independent physical series reduction and 2x2 root/leaf solve; conditioned on exported internal EMFs')


def worker(size,coupled,out):
    started=time.monotonic();s=example(size,coupled);built=time.monotonic();r=run_study(s);solved=time.monotonic()
    c=check(s,r);checked=time.monotonic()
    write_json(out/'study.json',s.model_dump(mode='json'));write_json(out/'trace.json',r)
    row=dict(passed=c['passed'],nodes=size,coupled=coupled,case_sha256=canonical_hash(s),code_sha256=package_fingerprint(),build_s=built-started,solve_s=solved-built,oracle_s=checked-solved,process_peak_rss_kib_linux=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,checks=c)
    write_json(out/'result.json',row);return row


def run(out,sizes):
    if out.exists() and any(out.iterdir()):raise ValueError('new scale directory required')
    code=package_fingerprint();source=sha256_file(Path(__file__))
    write_json(out/'protocol.json',dict(sizes=sizes,coupled=[False,True],duration_s=.02,step_s=.005,limits_pu=LIMIT_PU,code_sha256=code,producer_sha256=source,topology='radial_binary_tree',fresh_process_per_case=True))
    write_json(out/'summary.json',dict(passed=False,status='running'));rows=[]
    for size in sizes:
        for coupled in (False,True):
            folder=out/(str(size)+'-'+str(coupled));args=[sys.executable,'-m','benchmarks.rms_scalability','--worker','--size',str(size),'--out',str(folder)]
            if coupled:args.append('--coupled')
            print(size,coupled,flush=True)
            result=subprocess.run(args,capture_output=True,text=True,timeout=180.)
            if result.returncode:raise RuntimeError(result.stderr[-2000:])
            row=json.loads((folder/'result.json').read_text());rows.append(row)
            if not row['passed']:raise ValueError('scale oracle failed')
    if code!=package_fingerprint() or source!=sha256_file(Path(__file__)):raise ValueError('scale producer changed')
    summary=dict(passed=True,cases=rows,code_sha256=code,arbitrary_scale_claim=False,large_meshed_network_qualified=False,long_duration_runup_qualified=False,limits_pu=LIMIT_PU)
    write_json(out/'summary.json',summary);return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);p.add_argument('--sizes',type=int,nargs='+',default=[16,128,1000,2000,10000]);p.add_argument('--worker',action='store_true');p.add_argument('--size',type=int);p.add_argument('--coupled',action='store_true');a=p.parse_args()
    if a.worker:worker(a.size,a.coupled,a.out)
    else:print(run(a.out,a.sizes)['passed'])
