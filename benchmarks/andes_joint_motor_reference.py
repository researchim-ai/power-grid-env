"""External active GENROU/TGOV1/SEXS and mapped Motor5 cold-start reference.

Native equations and initialization only; temporary exact locked-rotor shunt
seeds generator PF initialization independently of production trajectories.
"""
from pathlib import Path
import argparse,json,math,os,shutil,subprocess,sys,tempfile,time
import numpy as np
from power_grid_env.study_spec import StudySpec
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.ipc import kill_process_group
from benchmarks.double_cage_andes_limits import native5_coordinates


def scope(spec,terminal_adapter=False):
    if spec.study!='coupled_rms' or spec.backend!='scipy' or spec.coupled is None:raise ValueError('coupled_rms/scipy required')
    n,s=spec.network,spec.coupled
    if any(len(x)!=1 for x in [n.sources,n.lines,n.generators,s.motors,s.machines]) or len(n.connectivity_nodes)!=2:raise ValueError('one source, line, generator and motor on two nodes required')
    m=s.motors[0];machine=s.machines[0]
    if n.loads or n.transformers or n.shunts or n.inverters or n.switches or n.storages or n.neutral_grounds or n.study_faults or n.harmonics or (s.operating_point and not terminal_adapter) or s.faults:raise ValueError('cold unaugmented feeder required')
    if machine.model!='GENROU' or machine.stabilizer or machine.governor is None or machine.exciter is None:raise ValueError('GENROU/TGOV1/SEXS without PSS required')
    if m.model!='double_cage' or not m.connected or m.thermal or m.contactor or m.mechanical_load:raise ValueError('connected cold double cage fan-load motor required')
    if any(e.time_s>=s.duration_s for e in s.events):raise ValueError('events strictly inside native horizon required')
    if s.duration_s>3:raise ValueError('external joint duration budget')
    terminals={t.id:t.connectivity_node_id for t in n.terminals};src=n.sources[0];line=n.lines[0];g=n.generators[0]
    if terminals[g.terminal_id]!=m.node_id or terminals[src.terminal_id]==m.node_id or {terminals[line.terminal_from],terminals[line.terminal_to]}!={m.node_id,terminals[src.terminal_id]}:raise ValueError('common plant bus and direct feeder required')
    if machine.generator_id!=g.id:raise ValueError('generator passport target mismatch')
    if g.ll_v!=m.rated_ll_v:raise ValueError('common motor/generator voltage base required')
    if np.any(line.b_siemens):raise ValueError('no feeder charging')
    for matrix in [line.r_ohm,line.x_ohm]:
        matrix=np.array(matrix);equivalent=np.full((3,3),matrix[0,1]);np.fill_diagonal(equivalent,matrix[0,0])
        if not np.allclose(matrix,equivalent,rtol=0,atol=1e-12):raise ValueError('transposed balanced feeder required')
    for e in s.events:
        allowed={'source_voltage','governor_step','voltage_reference_step'}|({'motor_open','motor_close'} if terminal_adapter else set())
        if e.kind not in allowed:raise ValueError('unsupported joint native event')
        target=src.id if e.kind=='source_voltage' else m.id if e.kind in {'motor_open','motor_close'} else g.id
        if e.equipment_id!=target:raise ValueError('native event target mismatch')


def _native(spec,step,terminal_adapter=False):
    scope(spec,terminal_adapter)
    base=Path(tempfile.gettempdir())/'power-grid-env-andes-pycode'
    if not terminal_adapter:return _native_run(spec,step,False,base)
    if not (base/'Motor5.py').is_file():raise ValueError('prepare/qualify stock native adapter first')
    stock={p.name:sha256_file(p) for p in base.glob('*.py')}
    with tempfile.TemporaryDirectory(prefix='pge-terminal-pycode-') as private:
        private=Path(private)
        for p in base.glob('*.py'):shutil.copyfile(p,private/p.name)
        result=_native_run(spec,step,True,private)
        if stock!={p.name:sha256_file(p) for p in base.glob('*.py')}:raise ValueError('stock native code cache changed')
        result['stock_generated_cache_unchanged']=True
        return result


def _native_run(spec,step,terminal_adapter,cache):
    if not math.isfinite(step) or not 1e-5<=step<=.01:raise ValueError('native step budget')
    # Reuse the analytic circuit mapping, never a simulated trajectory/state.
    data=spec.model_dump(mode='json');data['study']='motor_start';data['coupled']=None
    data['induction']=spec.coupled.model_dump(mode='json',exclude={'machines','faults','events'});data['induction']['events']=[]
    mapped,mapping=native5_coordinates(StudySpec.model_validate(data))
    n,s=spec.network,spec.coupled;m=mapped.induction.motors[0];machine=s.machines[0];g=n.generators[0];src=n.sources[0];line=n.lines[0]
    from power_grid_env.andes_rms import ensure_andes_importable
    if not ensure_andes_importable():raise ImportError('ANDES extra required')
    import andes
    if andes.__version__!='2.0.0':raise ValueError('joint reference pinned to ANDES 2.0.0')
    from andes.linsolvers.solverbase import Solver
    a=andes.System(no_output=True,default_config=True,pycode_path=str(cache))
    from benchmarks.andes_motor_terminal import install_terminal_gate,initial_motor
    adaptation=install_terminal_gate(a) if terminal_adapter else None
    a.config.mva=n.s_base_va/1e6;a.config.freq=n.frequency_hz
    kv=m.rated_ll_v/1000;zb=m.rated_ll_v**2/n.s_base_va;mf=m.rated_va/n.s_base_va;gf=machine.rated_va/n.s_base_va
    z=complex(src.r_ohm+line.r_ohm[0][0]-line.r_ohm[0][1],src.x_ohm+line.x_ohm[0][0]-line.x_ohm[0][1])
    for i in [1,2]:a.add('Bus',idx=i,Vn=kv,v0=1.,a0=src.angle_a_rad)
    a.add('Slack',idx='grid',bus=1,Sn=n.s_base_va/1e6,Vn=kv,v0=src.ll_v/m.rated_ll_v,a0=src.angle_a_rad)
    a.add('Line',idx='feed',bus1=1,bus2=2,Sn=n.s_base_va/1e6,Vn1=kv,Vn2=kv,fn=n.frequency_hz,r=z.real/zb,x=z.imag/zb,b=0.)
    a.add('PV',idx='pv',bus=2,Sn=n.s_base_va/1e6,Vn=kv,p0=g.p_w/n.s_base_va,v0=g.v_set_pu)
    a.add('GENROU',idx='g',bus=2,gen='pv',Sn=machine.rated_va/1e6,Vn=kv,fn=n.frequency_hz,M=2*machine.inertia_s,D=machine.damping_pu,
        ra=machine.armature_r_pu,xl=machine.leakage_pu,xd=machine.x_d_pu,xq=machine.x_q_pu,xd1=machine.x_d_prime_pu,xd2=machine.x_d_subtransient_pu,
        xq1=machine.x_q_prime_pu,xq2=machine.x_q_subtransient_pu,Td10=machine.td_prime_s,Td20=machine.td_subtransient_s,Tq10=machine.tq_prime_s,Tq20=machine.tq_subtransient_s,
        S10=0. if machine.saturation=='disabled' else machine.saturation_s10,S12=1. if machine.saturation=='disabled' else machine.saturation_s12)
    a.add('TGOV1',idx='gov',syn='g',Tn=machine.rated_va/1e6,wref0=1.,**machine.governor.model_dump(exclude={'model'}))
    a.add('SEXS',idx='avr',syn='g',**machine.exciter.model_dump(exclude={'model'}))
    start=initial_motor(m,n.frequency_hz,s.operating_point)
    if callable(start):start=start(g.v_set_pu)
    seed=mf*start['admittance']
    a.add('Shunt',idx='cold_seed',bus=2,Sn=n.s_base_va/1e6,Vn=kv,g=seed.real,b=seed.imag)
    k=m.fan_torque_pu*mf
    a.add('Motor5',idx='motor',bus=2,Sn=m.rated_va/1e6,Vn=kv,fn=n.frequency_hz,rs=m.stator_r_pu,xs=m.stator_x_pu,xm=m.magnetizing_x_pu,
        rr1=m.rotor_r_pu,xr1=m.rotor_x_pu,rr2=m.rotor2_r_pu,xr2=m.rotor2_x_pu,Hm=m.inertia_s,c1=-k/2,c2=k,c3=k/2,u=0)
    a.PFlow.solver=Solver('spsolve');a.TDS.solver=Solver('spsolve');a.setup();a.PFlow.config.tol=1e-12
    if not a.PFlow.run():raise RuntimeError('cold-load PF seed failed')
    a.TDS.config.tf=s.duration_s;a.TDS.config.tstep=step;a.TDS.config.fixt=1;a.TDS.config.shrinkt=0;a.TDS.config.tol=1e-11;a.TDS.config.criteria=0;a.TDS.config.test_init=0;a.TDS.config.honest=1;a.TDS.init()
    seed_voltage=a.Bus.v.v.copy();seed_states=a.dae.x.copy()
    a.set_status('Shunt','cold_seed',0);a.set_status('Motor5','motor',1)
    a.dae.x[a.Motor5.slip.a]=1.-start['speed_pu']
    voltage=g.v_set_pu*np.exp(1j*a.Bus.a.v[1])
    for prefix,key in [('e1','e1'),('e2','e2')]:
        emf=start[key]*voltage
        a.dae.x[getattr(a.Motor5,prefix+'q').a]=emf.real
        a.dae.x[getattr(a.Motor5,prefix+'d').a]=-emf.imag
    projection_solver=Solver('spsolve')
    def project():
        for _ in range(40):
            a.vars_to_models();a.TDS.fg_update(a.exist.pflow_tds);res=float(max(abs(a.dae.g)))
            if res<1e-10:return res
            a.j_update(a.exist.pflow_tds);projection_solver.worker.factorize=True
            a.dae.y[:]-=projection_solver.solve(a.dae.gy,a.dae.g)
        raise RuntimeError('native joint G=0 projection failed')
    residual=project()
    seed_voltage_error=float(max(abs(a.Bus.v.v-seed_voltage)))
    machine_indices=np.r_[*[getattr(a.GENROU,k).a for k in ['delta','omega','e1q','e1d','e2d','e2q']]]
    machine_rhs=float(max(abs(a.dae.f[machine_indices])))
    if seed_voltage_error>1e-8 or machine_rhs>1e-8 or not np.array_equal(a.dae.x[machine_indices],seed_states[machine_indices]):raise ValueError('cold seed replacement changed generator initial condition')
    motor_indices=np.r_[*[getattr(a.Motor5,k).a for k in ['slip','e1d','e1q','e2d','e2q']]]
    nonmotor_indices=np.setdiff1d(np.arange(len(a.dae.x)),motor_indices)
    control_rhs=float(max(abs(a.dae.f[nonmotor_indices])))
    if control_rhs>1e-8:raise ValueError('generator/controller cold initialization residual')
    initial_motor_rhs=float(max(abs(a.dae.f[motor_indices])))
    if s.operating_point and initial_motor_rhs>1e-8:raise ValueError('native loaded motor initialization residual')
    # Explicit rows support distinct pre/post algebraic values at equal event time.
    records=[]
    def store(t,side):records.append((float(t),side,a.dae.x.copy(),a.dae.y.copy(),float(a.Motor5.closed.v[0]) if terminal_adapter else 1.))
    store(0.,'regular');previous=0.;maximum_g=residual
    events={}
    for e in s.events:events.setdefault(e.time_s,[]).append(e)
    grid=np.arange(math.ceil(s.duration_s/step))*step
    regular=[float(t) for t in grid if 0<t<s.duration_s and all(abs(t-e)>1e-12 for e in events)]
    times=sorted(set([*regular,s.duration_s,*events]))
    for t in times:
        a.TDS.h=float(t-previous);a.dae.t[...]=t
        if not a.TDS.itm_step():raise RuntimeError(f'native joint trapezoidal step failed at {t} s, h={a.TDS.h}: {a.TDS.err_msg}')
        accepted_g=float(np.max(np.abs(a.dae.g)))
        maximum_g=max(maximum_g,accepted_g)
        if not math.isfinite(accepted_g) or maximum_g>1e-8:raise ValueError('native accepted algebraic residual exceeds 1e-8')
        a.TDS.custom_event=False
        a.b_update(a.exist.pflow_tds);store(t,'pre_event' if t in events else 'regular')
        if t in events:
            for e in events[t]:
                if e.kind=='source_voltage':a.Slack.v0.v[0]=src.ll_v/m.rated_ll_v*e.value
                elif e.kind=='governor_step':a.TGOV1.pref0.v[0]+=e.value*gf
                elif e.kind=='voltage_reference_step':a.SEXS.vref0.v[0]+=e.value
                else:a.Motor5.closed.v[0]=0. if e.kind=='motor_open' else 1.
            before=a.dae.x.copy();project()
            if not np.array_equal(before,a.dae.x):raise ValueError('event projection changed differential state')
            a.TDS.custom_event=True
            store(t,'post_event')
        previous=float(t)
    x=np.array([r[2] for r in records]);y=np.array([r[3] for r in records])
    def v(model,key):
        variable=getattr(model,key);return (x if variable.v_code=='x' else y)[:,int(variable.a[0])]
    motor=a.Motor5;gen=a.GENROU
    channels={m.id+'.speed_pu':1-v(motor,'slip'),m.id+'.e_real_pu':v(motor,'e2q'),m.id+'.e_imag_pu':-v(motor,'e2d'),m.id+'.current_pu':np.hypot(v(motor,'Id'),v(motor,'Iq'))/mf,
        m.id+'.bus_voltage_pu':y[:,int(a.Bus.v.a[1])],m.id+'.te_pu':v(motor,'te')/mf,m.id+'.p_w':v(motor,'p')*n.s_base_va,m.id+'.q_var':v(motor,'q')*n.s_base_va}
    channels.update({g.id+'.'+key:v(gen,key) for key in ['delta','omega','e1q','e1d','e2d','e2q']})
    channels.update({g.id+'.TGOV1.valve_pu':v(a.TGOV1,'LAG_y')/gf,g.id+'.SEXS.field_pu':v(a.SEXS,'vout'),g.id+'.p_w':(v(gen,'vd')*v(gen,'Id')+v(gen,'vq')*v(gen,'Iq'))*n.s_base_va,g.id+'.q_var':(v(gen,'vq')*v(gen,'Id')-v(gen,'vd')*v(gen,'Iq'))*n.s_base_va})
    if not all(np.isfinite(c).all() for c in channels.values()):raise ValueError('nonfinite joint result')
    source_root=Path(andes.__file__).parent
    hashes={str(path.relative_to(source_root)):sha256_file(path) for path in sorted(source_root.rglob('*.py'))}
    return dict(completed=True,case_sha256=canonical_hash(spec),code_sha256=package_fingerprint(),step_s=step,backend_version=andes.__version__,
        time_s=[r[0] for r in records],sample_side=[r[1] for r in records],channels={k:c.tolist() for k,c in channels.items()},
        initial_algebraic_residual_pu=residual,seed_voltage_replacement_error_pu=seed_voltage_error,initial_generator_rhs=machine_rhs,initial_nonmotor_rhs=control_rhs,
        terminal_adapter=terminal_adapter,terminal_adaptation=adaptation,initial_motor_rhs=initial_motor_rhs,
        native_motor_states={key:v(motor,key).tolist() for key in ['slip','e1d','e1q','e2d','e2q']},terminal_connected=[bool(r[4]) for r in records],
        event_differential_state_continuity_verified=True,native_honest_jacobian=True,maximum_accepted_algebraic_residual_pu=maximum_g,
        native_initial_motor_speed_pu=start['speed_pu'],mapping=mapping,native_source_sha256=hashes,field_validated=False,industrial_profile_qualified=False)


def native_reference(spec,step,timeout_s=120.,*,terminal_adapter=False):
    """Bounded independent worker; no incomplete output may become evidence."""
    scope(spec,terminal_adapter)
    if not math.isfinite(step) or not 1e-5<=step<=.01:raise ValueError('native step budget')
    if not math.isfinite(timeout_s) or timeout_s<=0:raise ValueError('positive timeout required')
    with tempfile.TemporaryDirectory(prefix='pge-andes-joint-') as tmp:
        root=Path(tmp);write_json(root/'study.json',spec.model_dump(mode='json'))
        env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
        with (root/'log').open('wb') as log:
            process=subprocess.Popen([sys.executable,'-m','benchmarks.andes_joint_motor_reference','--native',str(root/'study.json'),'--step',str(step),'--out',str(root/'result.json'),*(['--terminal-adapter'] if terminal_adapter else [])],cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
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
                raw=(root/'result.json').read_bytes()
                if len(raw)>32_000_000:return dict(completed=False,status='native_output_budget')
                result=json.loads(raw)
                if result.get('case_sha256')!=canonical_hash(spec) or result.get('code_sha256')!=package_fingerprint() or result.get('step_s')!=step or result.get('backend_version')!='2.0.0' or result.get('terminal_adapter') is not terminal_adapter:raise ValueError('native joint identity mismatch')
                return result
            finally:
                if process.poll() is None:kill_process_group(process)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--terminal-adapter',action='store_true');p.add_argument('--native',type=Path,required=True);p.add_argument('--step',type=float,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    write_json(a.out,_native(StudySpec.model_validate_json(a.native.read_text()),a.step,a.terminal_adapter))


if __name__=='__main__':main()
