"""Streamed CSC vs NA-08-style dense conversion, plus full-state memory probes."""
from pathlib import Path
import argparse,json,os,subprocess,sys,tracemalloc,resource,time
from unittest.mock import patch
import numpy as np
from scipy.sparse import csc_matrix
from power_grid_env.study_spec import StudySpec
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from benchmarks.native_online_validation import source_identity
from benchmarks.coupled_jacobian_validation import compare,worker as analytic_worker
from benchmarks.jacobian_scaling_validation import NAMES as PREVIOUS,case as previous,current_exec_peak_rss_kib

NAMES=PREVIOUS+('max_genrou_8_32','max_esst_8_32','max_genrou_long_8_32')
MEMORY_NAMES=('scale_esst_2000','max_genrou_8_32','max_esst_8_32')


def case(name):
    if name in PREVIOUS:s=previous(name)
    elif name in NAMES:
        from benchmarks.ex03_cases import scale_case
        from power_grid_env.build import NetBuilder
        from power_grid_env.rms_controls import IEEESTPassport
        from power_grid_env.motor_thermal import MotorThermalProtection
        from power_grid_env.generator_thermal import GeneratorThermalProtection
        from power_grid_env.generator_field_thermal import GeneratorFieldThermalProtection
        from power_grid_env.generator_underexcitation import UnderexcitationLimiter
        from power_grid_env.generator_oel2c import OEL2CPassport
        from power_grid_env.exciter_esst1a import ESST1APassport
        from power_grid_env.excitation_takeover import TakeoverOELPassport
        s=scale_case(512,32,4);template=s.coupled.machines[0]
        b=NetBuilder('eight-machine-extension','Authored eight-machine scale family',10.)
        for i in range(4,8):
            b.node(f'n{i+1}');b.generator(f'n{i+1}',1e5/8,mode='pv',v_set_pu=.995,q_min_var=-1e7,q_max_var=1e7,generator_id=f'g{i}')
            s.coupled.machines.append(template.model_copy(deep=True,update={'generator_id':f'g{i}'}))
        extra=b.build();s.network.generators+=extra.generators;s.network.terminals+=extra.terminals
        for g in s.network.generators:g.p_w=1e5/8
        for m in s.coupled.machines:
            m.rated_va=10e6/8
            m.stabilizer=IEEESTPassport(A1=.2,A2=.02,A3=.3,A4=.03,A5=.05,A6=.001,T1=.1,T2=.05,T3=.1,T4=.05,T5=1.,T6=1.,KS=2.,LSMIN=-.1,LSMAX=.1,VCL=.8,VCU=1.2)
        for m in s.coupled.motors:
            m.model='double_cage';m.rotor2_r_pu=.07;m.rotor2_x_pu=.12
            m.thermal=MotorThermalProtection(time_constant_s=3.,trip_level=100.)
        ids=[m.generator_id for m in s.coupled.machines]
        s.coupled.generator_thermal=[GeneratorThermalProtection(generator_id=g,time_constant_s=5.,trip_level=100.) for g in ids]
        s.coupled.generator_field_thermal=[GeneratorFieldThermalProtection(generator_id=g,rated_field_reaction_pu=1.5,time_constant_s=5.,trip_level=100.) for g in ids]
        s.coupled.underexcitation_limiters=[UnderexcitationLimiter(generator_id=g,Q_0=2.5,Radius=4.) for g in ids]
        if name=='max_esst_8_32':
            for m in s.coupled.machines:m.exciter=None;m.stabilizer_connection='external_ESST1A'
            s.coupled.esst1a_exciters=[ESST1APassport(generator_id=g,K_C=0.,V_IMAX=.5) for g in ids]
            s.coupled.takeover_oels=[TakeoverOELPassport(generator_id=g,current_limit_pu=2.) for g in ids]
        else:
            # Deliberately distinct authored thresholds; no benchmark tuning.
            s.coupled.oel2c_limiters=[OEL2CPassport(generator_id=g,rated_field_reaction_pu=1.5,initialization='inactive_equilibrium',IThOffPu=.05+.003*i,coincidence_policy='independent_below_clock_batch_v1') for i,g in enumerate(ids)]
        s.coupled.duration_s=6. if name=='max_genrou_long_8_32' else .8
        s.coupled.runtime_limits.maximum_integration_wall_s=180.
    else:raise ValueError('registered sparse assembly case required')
    s.id='streamed-assembly-'+name;s.coupled.jacobian_format='csc'
    return StudySpec.model_validate(s.model_dump(mode='json'))


def worker(study,mode,out):
    from power_grid_env.coupled_rms import CoupledNetwork
    import power_grid_env.jacobian_storage as storage
    s=StudySpec.model_validate_json(Path(study).read_text());m=CoupledNetwork(s.network,s.coupled);cb=m.integration_jacobian()
    if cb is None:raise ValueError('analytic profile required')
    original=storage.configure
    def legacy(callback,kind):
        if callback is None or kind!='csc':return original(callback,kind)
        def dense_only(t,x):return callback(t,x)
        dense_only.jacobian_kind=getattr(callback,'jacobian_kind','analytic_motor_network_reduction')
        return original(dense_only,kind)
    if mode=='legacy':
        with patch.object(storage,'configure',legacy):analytic_worker(study,'analytic',out,analytic_kind=cb.jacobian_kind)
    else:analytic_worker(study,'analytic',out,analytic_kind=cb.jacobian_kind)
    p=Path(out);trace=json.loads((p/'trace.json').read_text());metrics=json.loads((p/'metrics.json').read_text())
    expected='dense_conversion' if mode=='legacy' else 'column_blocks'
    if trace['integration']['jacobian_assembly']!=expected:raise ValueError('assembly path mismatch')
    metrics.update(assembly=expected,current_exec_peak_rss_kib_linux=current_exec_peak_rss_kib(),process_lifetime_peak_rss_kib_linux=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    (p/'metrics.json').write_text(json.dumps(metrics,indent=2)+'\n')


def memory_worker(name,mode,out):
    from power_grid_env.coupled_rms import CoupledNetwork
    from power_grid_env.coupled_initialization import initialize_coupled
    from power_grid_env.sparse_jacobian import assemble
    s=case(name);m=CoupledNetwork(s.network,s.coupled);x,init=initialize_coupled(m)
    if x is None:raise ValueError('memory snapshot initialization failed: '+str(init))
    # Authored connected transient snapshot; not an equilibrium/trajectory claim.
    m.connected[:]=True;m.factorize()
    for i in range(len(m.settings.motors)):x[3*i:3*i+3]=[.3,.1,.8]
    for i,a in m.cages.addresses.items():x[a]=[.05,.02]
    for _,(a,p,k) in m.oel2c.entries.items():x[a+4]=-.07;x[a+6]=.01;x[a+7]=.01
    for _,(a,p) in m.underexcitation_limiters.entries.items():x[a]=p.MEL_MAX*.3
    for _,(a,p) in m.takeover_oel.entries.items():x[a+1]=.5-p.proportional_gain*(p.current_limit_pu-x[a])
    cb=m.integration_jacobian();ctx=cb.jacobian_context(0.,x);cb.jacobian_columns(0.,x,np.arange(min(16,len(x))),ctx)
    tracemalloc.start();started=time.perf_counter()
    if mode=='legacy':matrix=csc_matrix(cb(0.,x))
    else:matrix=assemble(cb,0.,x)
    elapsed=time.perf_counter()-started;_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    out=Path(out);out.mkdir(parents=True);from scipy.sparse import save_npz
    save_npz(out/'matrix.npz',matrix)
    (out/'metrics.json').write_text(json.dumps(dict(case=name,mode=mode,states=len(x),nnz=matrix.nnz,density=matrix.nnz/len(x)**2,numpy_tracemalloc_peak_bytes=peak,assembly_wall_s=elapsed,current_exec_peak_rss_kib_linux=current_exec_peak_rss_kib(),code_sha256=package_fingerprint()),indent=2)+'\n')


def qualify(out):
    from scipy.sparse import load_npz
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();source=source_identity();specs={};env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    for name in NAMES:
        spec=case(name);folder=out/name;folder.mkdir();(folder/'study.json').write_text(spec.model_dump_json())
        specs[name]=dict(case_sha256=canonical_hash(spec),study_path=name+'/study.json')
    del spec
    (out/'protocol.json').write_text(json.dumps(dict(code_sha256=code,source_sha256=source,cases=specs,repeats=2,assembly=['legacy_dense_conversion','column_blocks'],block_columns=32,memory_cases=MEMORY_NAMES,worker_timeout_s=240,memory_matrix_limit=2e-10,trajectory_limits=dict(state=2e-5,power_w_var=100.,event_s=1e-6,balance_pu=1e-8),scope='same-RHS assembly regression, not independent physics; both modes use CSC/SuperLU; connected memory snapshots distinct from trajectories'),indent=2)+'\n')
    rows=[];memory=[];(out/'summary.json').write_text(json.dumps(dict(passed=False,status='running'))+'\n')
    def execute(args,log):
        proc=subprocess.run([sys.executable,'-m','benchmarks.sparse_jacobian_validation',*args],env=env,capture_output=True,text=True,timeout=240);log.parent.mkdir(parents=True,exist_ok=True);log.write_text(proc.stdout+'\n'+proc.stderr)
        if proc.returncode:raise ValueError('worker failed: '+str(log))
    try:
        for name,identity in specs.items():
            folder=out/name;study=folder/'study.json'
            for repeat in range(2):
                metrics={}
                for mode in ['legacy','streamed']:
                    target=folder/(mode+'-'+str(repeat));execute(['--child','--study',str(study),'--mode',mode,'--out',str(target)],target.with_suffix('.log'));metrics[mode]=json.loads((target/'metrics.json').read_text())
                    if metrics[mode]['code_sha256']!=code or metrics[mode]['case_sha256']!=identity['case_sha256']:raise ValueError('trajectory producer/input mismatch')
                comparison_file=folder/f'comparison-{repeat}.json'
                execute(['--compare',str(folder),'--repeat',str(repeat),'--out',str(comparison_file)],folder/f'comparison-{repeat}.log')
                check=json.loads(comparison_file.read_text());rows.append(dict(case=name,repeat=repeat,comparison=check,metrics=metrics));print(name,repeat,check['passed'],flush=True)
                if not check['passed']:raise ValueError('assembly trajectory mismatch: '+name)
        for name in MEMORY_NAMES:
            for repeat in range(2):
                data={};matrices={}
                for mode in ['legacy','streamed']:
                    target=out/'memory'/f'{name}-{repeat}-{mode}';execute(['--memory',name,'--mode',mode,'--out',str(target)],target.with_suffix('.log'));data[mode]=json.loads((target/'metrics.json').read_text());matrices[mode]=load_npz(target/'matrix.npz')
                difference=matrices['legacy']-matrices['streamed'];error=max(abs(difference.data),default=0.)
                passed=error<2e-10 and data['streamed']['numpy_tracemalloc_peak_bytes']<data['legacy']['numpy_tracemalloc_peak_bytes']
                memory.append(dict(case=name,repeat=repeat,passed=bool(passed),maximum_matrix_error=float(error),metrics=data))
                if not passed or any(d['code_sha256']!=code for d in data.values()):raise ValueError('matrix memory/equivalence criterion failed')
        if code!=package_fingerprint() or source!=source_identity():raise ValueError('producer changed during qualification')
        result=dict(passed=True,status='complete',cases=rows,memory=memory,code_sha256=code,source_sha256=source,independent_host_verified=False,industrial_release_qualified=False)
        (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n');return result
    except BaseException as exc:
        (out/'summary.json').write_text(json.dumps(dict(passed=False,status='failed',cases=rows,memory=memory,error=type(exc).__name__+': '+str(exc)),indent=2)+'\n');raise


def comparison_worker(folder,repeat,out):
    folder=Path(folder)
    a=json.loads((folder/f'legacy-{repeat}'/'trace.json').read_text())
    b=json.loads((folder/f'streamed-{repeat}'/'trace.json').read_text())
    check=compare(a,b)
    Path(out).write_text(json.dumps(check,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--child',action='store_true');p.add_argument('--study',type=Path);p.add_argument('--compare',type=Path);p.add_argument('--repeat',type=int,default=0);p.add_argument('--memory',choices=MEMORY_NAMES);p.add_argument('--mode',choices=['legacy','streamed']);a=p.parse_args()
    if a.child:worker(a.study,a.mode,a.out)
    elif a.compare:comparison_worker(a.compare,a.repeat,a.out)
    elif a.memory:memory_worker(a.memory,a.mode,a.out)
    else:qualify(a.out)
