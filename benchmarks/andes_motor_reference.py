"""External ANDES cold-start adapters; ordinary Motor3 corpus plus explicit Motor5 limits.

Motor3 defaults are not a cold-start equilibrium. Prescribe zero rotor voltage
and unit slip, solve native G=0 at fixed X, and command the native trapezoidal
stepper on an explicit physical clock. No production motor RHS/stamps are used.
"""
from __future__ import annotations
import argparse,hashlib,json,math,os,subprocess,sys,tempfile,time
from pathlib import Path
import numpy as np
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.induction_network import validate_induction
from power_grid_env.ipc import kill_process_group
from power_grid_env.report import write_json,sha256_file

STEPS=(.0005,.00025,.000125)
LIMITS={'speed_pu':1e-5,'e_real_pu':1e-3,'e_imag_pu':1e-3,'current_pu':1e-3,'bus_voltage_pu':1e-4,'te_pu':1e-3,'p_w':1000.,'q_var':1000.}


def scope(spec,motor_model='Motor3'):
    if spec.study!='motor_start' or spec.backend!='scipy' or spec.induction is None:raise ValueError('motor_start/scipy required')
    if motor_model not in {'Motor3','Motor5'}:raise ValueError('unknown native motor reference')
    n,s=spec.network,spec.induction
    if len(s.motors)!=1 or len(n.sources)!=1 or len(n.lines)!=1 or len(n.connectivity_nodes)!=2:raise ValueError('one motor/source/line on two nodes required')
    if s.events or s.operating_point or n.loads or n.transformers or n.generators:raise ValueError('cold event-free unregulated feeder only')
    m=s.motors[0]
    if m.model!=('single_cage' if motor_model=='Motor3' else 'double_cage'):raise ValueError('native reference requires explicitly matching cage passport')
    if motor_model=='Motor5' and m.rotor_mutual_x_pu:raise ValueError('native Motor5 adapter does not represent mutual rotor leakage')
    if not m.connected or m.thermal or m.contactor or m.mechanical_load is not None:raise ValueError('connected cold motor with fan load and no thermal relay required')
    if s.duration_s>3.:raise ValueError('external benchmark horizon exceeds 3 s')
    if not validate_induction(n,s)['ok'] or np.any(n.lines[0].b_siemens):raise ValueError('unsupported motor network/charging')
    terminals={t.id:t.connectivity_node_id for t in n.terminals}
    if terminals[n.sources[0].terminal_id]==m.node_id:raise ValueError('source and motor must occupy different nodes')


def _native(spec,step,motor_model='Motor3'):
    scope(spec,motor_model)
    if not 1e-5<=step<=.01 or math.ceil(spec.induction.duration_s/step)>300000:raise ValueError('native step budget')
    from power_grid_env.andes_rms import ensure_andes_importable
    if not ensure_andes_importable():raise ImportError('ANDES extra required')
    import andes
    from andes.linsolvers.solverbase import Solver
    n=spec.network;m=spec.induction.motors[0];src=n.sources[0];line=n.lines[0]
    factor=m.rated_va/n.s_base_va;zb=m.rated_ll_v**2/n.s_base_va
    z=complex(line.r_ohm[0][0]-line.r_ohm[0][1],line.x_ohm[0][0]-line.x_ohm[0][1])+complex(src.r_ohm,src.x_ohm)
    a=andes.System(no_output=True,default_config=True,pycode_path=str(Path(tempfile.gettempdir())/'power-grid-env-andes-pycode'))
    a.config.mva=n.s_base_va/1e6;a.config.freq=n.frequency_hz
    for i in [1,2]:a.add('Bus',idx=i,Vn=m.rated_ll_v/1000,v0=1.)
    a.add('Slack',idx='grid',bus=1,Sn=n.s_base_va/1e6,Vn=m.rated_ll_v/1000,v0=src.ll_v/m.rated_ll_v,a0=src.angle_a_rad)
    a.add('Line',idx='feed',bus1=1,bus2=2,Sn=n.s_base_va/1e6,Vn1=m.rated_ll_v/1000,Vn2=m.rated_ll_v/1000,fn=n.frequency_hz,r=z.real/zb,x=z.imag/zb,b=0.)
    k=m.fan_torque_pu*factor
    rotor2=dict(rr2=m.rotor2_r_pu,xr2=m.rotor2_x_pu) if motor_model=='Motor5' else {}
    a.add(motor_model,idx='motor',bus=2,Sn=m.rated_va/1e6,Vn=m.rated_ll_v/1000,fn=n.frequency_hz,
          rs=m.stator_r_pu,xs=m.stator_x_pu,rr1=m.rotor_r_pu,xr1=m.rotor_x_pu,xm=m.magnetizing_x_pu,Hm=m.inertia_s,
          c1=-k/2,c2=k,c3=k/2,u=0,**rotor2)
    a.PFlow.solver=Solver('spsolve');a.TDS.solver=Solver('spsolve')
    a.setup();a.PFlow.config.tol=1e-12
    if not a.PFlow.run():raise RuntimeError('native unloaded network seed failed')
    a.set_status(motor_model,'motor',1)
    a.TDS.config.tf=spec.induction.duration_s;a.TDS.config.tstep=step;a.TDS.config.fixt=1;a.TDS.config.shrinkt=0
    a.TDS.config.tol=1e-11;a.TDS.config.criteria=0;a.TDS.config.test_init=0;a.TDS.init()
    native_motor=getattr(a,motor_model)
    a.dae.x[native_motor.slip.a]=1.;a.dae.x[native_motor.e1d.a]=0.;a.dae.x[native_motor.e1q.a]=0.
    if motor_model=='Motor5':a.dae.x[native_motor.e2d.a]=0.;a.dae.x[native_motor.e2q.a]=0.
    for _ in range(30):
        a.vars_to_models();a.TDS.fg_update(a.exist.pflow_tds)
        residual=float(max(abs(a.dae.g)))
        if residual<1e-10:break
        a.j_update(a.exist.pflow_tds);a.dae.y[:]-=a.TDS.solver.solve(a.dae.gy,a.dae.g)
    else:raise RuntimeError('native fixed-X algebraic initialization failed')
    initial_motor_rhs=a.dae.f[native_motor.e1q.a].tolist()
    a.dae.store();previous=0.
    regular=np.arange(math.ceil(spec.induction.duration_s/step))*step
    times=np.r_[regular[(regular>0)&(regular<spec.induction.duration_s)],spec.induction.duration_s]
    for t in times:
        a.TDS.h=float(t-previous);a.dae.t[...]=t
        if not a.TDS.itm_step():raise RuntimeError('native trapezoidal step failed')
        a.b_update(a.exist.pflow_tds);a.dae.store();previous=float(t)
    t=a.dae.ts.t;x=a.dae.ts.x;y=a.dae.ts.y;model=native_motor
    def state(name):return x[:,int(getattr(model,name).a[0])]
    def algebraic(name):return y[:,int(getattr(model,name).a[0])]
    channels={'speed_pu':1-state('slip'),'e_real_pu':state('e1q' if motor_model=='Motor3' else 'e2q'),'e_imag_pu':-state('e1d' if motor_model=='Motor3' else 'e2d'),
        'current_pu':np.hypot(algebraic('Id'),algebraic('Iq'))/factor,'bus_voltage_pu':y[:,int(a.Bus.v.a[1])],
        'te_pu':algebraic('te')/factor,'p_w':algebraic('p')*n.s_base_va,'q_var':algebraic('q')*n.s_base_va}
    if not all(np.isfinite(v).all() for v in channels.values()):raise ValueError('nonfinite native result')
    if ''.join(model.tm.e_str.split())!='ue*(aa+bb*slip+c2*slip*slip)-tm':raise ValueError('unsupported native mechanical polynomial implementation')
    coefficients=np.array([model.aa.v[0]+model.bb.v[0]+model.c2.v[0],-model.bb.v[0]-2*model.c2.v[0],model.c2.v[0]])/factor
    mapping_error=float(max(abs(coefficients-[0.,0.,m.fan_torque_pu])))
    torque_error=float(max(abs(algebraic('tm')/factor-m.fan_torque_pu*channels['speed_pu']**2)))
    if mapping_error>1e-12 or torque_error>1e-7:raise ValueError('native load coefficient mapping or algebraic residual failed')
    source_root=Path(andes.__file__).parent
    hashes={name:sha256_file(source_root/name) for name in ['models/motor/'+motor_model.lower()+'.py','models/motor/motorbase.py','routines/daeint.py','linsolvers/scipy.py']}
    return dict(completed=True,case_sha256=canonical_hash(spec),code_sha256=package_fingerprint(),backend='ANDES '+motor_model,backend_version=andes.__version__,
        step_s=step,time_s=t.tolist(),channels={key:value.tolist() for key,value in channels.items()},
        native_load_mapping_error_pu=mapping_error,native_load_algebraic_residual_pu=torque_error,initial_algebraic_residual_pu=residual,initial_motor_rhs_nonzero_expected=initial_motor_rhs,
        initialization='Prescribed slip=1 and e1d=e1q=0; solve native G=0 at fixed X; not steady dynamic initialization',
        integration='ANDES native implicit trapezoidal itm_step, explicit endpoint clock and initial row; no curve shifting',
        mechanical_mapping=dict(native_c1=-k/2,native_c2=k,native_c3=k/2,motor_to_system_va=factor,
            literal_native_polynomial='aa+bb*slip+c2*slip^2',expanded_own_base_polynomial='fan_torque_pu*omega^2'),
        native_zero_slip_rotor_decay_per_s=[-1./float(model.T10.v[0])]+([-1./float(model.T20.v[0])] if motor_model=='Motor5' else []),native_source_sha256=hashes,industrial_profile_qualified=False,field_validated=False)


def native_reference(spec,step,timeout_s=60.,motor_model='Motor3'):
    scope(spec,motor_model)
    if not math.isfinite(timeout_s) or timeout_s<=0:raise ValueError('positive native timeout required')
    with tempfile.TemporaryDirectory(prefix='pge-andes-motor-') as tmp:
        root=Path(tmp);write_json(root/'study.json',spec.model_dump(mode='json'))
        env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
        with (root/'log').open('wb') as log:
            process=subprocess.Popen([sys.executable,'-m','benchmarks.andes_motor_reference','--native',str(root/'study.json'),'--step',str(step),'--native-model',motor_model,'--out',str(root/'result.json')],
                cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
            start=time.monotonic();reason=None
            try:
                while process.poll() is None:
                    if time.monotonic()-start>=timeout_s:reason='native_timeout'
                    elif (root/'log').stat().st_size>1_000_000 or ((root/'result.json').exists() and (root/'result.json').stat().st_size>32_000_000):reason='native_output_budget'
                    if reason:kill_process_group(process);break
                    time.sleep(.01)
                if reason:return dict(completed=False,status=reason)
                if (root/'log').stat().st_size>1_000_000:return dict(completed=False,status='native_output_budget')
                if process.returncode!=0 or not (root/'result.json').exists():return dict(completed=False,status='native_worker_failed',exit_code=process.returncode,log_tail=(root/'log').read_text(errors='replace')[-2000:])
                data=(root/'result.json').read_bytes()
                if len(data)>32_000_000:return dict(completed=False,status='native_output_budget')
                report=json.loads(data)
                if report.get('case_sha256')!=canonical_hash(spec) or report.get('code_sha256')!=package_fingerprint() or report.get('step_s')!=step:raise ValueError('native identity mismatch')
                return report
            finally:
                if process.poll() is None:kill_process_group(process)


def compare(spec,steps=STEPS,out=None):
    scope(spec)
    if len(steps)<2 or any(not math.isfinite(h) or not 1e-5<=h<=.01 for h in steps) or list(steps)!=sorted(set(steps),reverse=True):raise ValueError('strictly decreasing native steps required')
    actual=run_study(spec,isolate=True,wall_timeout_s=60.)
    if not actual.get('completed'):raise ValueError('production simulation did not complete')
    m=spec.induction.motors[0];rows=[]
    if out is not None:write_json(Path(out)/'production.json',actual)
    for step in steps:
        native=native_reference(spec,step)
        if not native.get('completed'):rows.append(dict(step_s=step,passed=False,failure=native));continue
        errors={key:float(max(abs(np.interp(actual['time_s'],native['time_s'],values)-actual['channels'][m.id+'.'+key]))) for key,values in native['channels'].items()}
        rows.append(dict(step_s=step,errors=errors,initial_algebraic_residual_pu=native['initial_algebraic_residual_pu'],passed=all(errors[k]<=v for k,v in LIMITS.items())))
        if out is not None:write_json(Path(out)/f'andes-{step}.json',native)
    fine=rows[-1];converges=all('errors' in r for r in rows) and fine['errors']['speed_pu']<rows[0]['errors']['speed_pu']
    return dict(passed=bool(fine['passed'] and converges),case_sha256=canonical_hash(spec),steps=rows,finest_limits=LIMITS,
        convergence_checked='fine speed error must decrease relative to coarse; all fine channel errors have fixed limits',
        code_sha256=package_fingerprint(),field_validated=False,industrial_profile_qualified=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--native',type=Path);p.add_argument('--step',type=float);p.add_argument('--native-model',choices=['Motor3','Motor5'],default='Motor3');p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.native:
        if args.step is None:p.error('--step is required for --native')
        spec=StudySpec.model_validate_json(args.native.read_text());write_json(args.out,_native(spec,args.step,args.native_model));return
    rows=[]
    for name in ['nominal','high_inertia','weak_source','frequency_base']:
        path=Path('cases/studies/andes_motor_'+name+'.json');spec=StudySpec.model_validate_json(path.read_text())
        result=compare(spec,out=args.out/name);result['case']=str(path);result['case_file_sha256']=sha256_file(path);rows.append(result)
    result=dict(passed=all(r['passed'] for r in rows),cases=rows,code_sha256=package_fingerprint(),
        reference='external ANDES Motor3 equations/Jacobian/native trapezoidal integrator with explicit cold initial conditions',
        scope='single connected cold fan-load motor, one equivalent feeder, no events/thermal/coasting/reclose/converters',field_validated=False,industrial_profile_qualified=False)
    write_json(args.out/'summary.json',result);print([(Path(r['case']).stem,r['passed'],r['steps'][-1].get('errors')) for r in rows])
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
