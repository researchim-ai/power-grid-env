"""Native ANDES mixed Motor3/Motor5 groups, optional active GENROU source.

Independent circuit/PV initialization. Only explicit stator terminal constraints
are adapted; all native rotor, mechanical and regulator differential laws remain
unchanged. Controller commands are inputs, not native closed-loop decisions.
"""
from pathlib import Path
import argparse,json,math,os,shutil,subprocess,sys,tempfile,time
import numpy as np
from scipy.optimize import brentq,root
from power_grid_env.study_spec import StudySpec
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json,sha256_file
from power_grid_env.ipc import kill_process_group
from benchmarks.double_cage_andes_limits import native5_coordinates
from benchmarks.andes_motor_terminal import initial_motor,equations


def scope(spec):
    if spec.study not in {'coupled_rms','motor_start'} or spec.backend!='scipy':raise ValueError('motor_start or coupled_rms/scipy required')
    n,s=spec.network,spec.coupled if spec.coupled else spec.induction
    coupled=spec.study=='coupled_rms'
    if len(n.sources)!=1 or len(n.lines)!=1 or len(n.generators)!=(1 if coupled else 0) or (coupled and len(s.machines)!=1) or len(n.connectivity_nodes)!=2 or not 1<=len(s.motors)<=32:raise ValueError('one source/line, optional generator and 1..32 common-bus motors required')
    if any([n.loads,n.transformers,n.shunts,n.inverters,n.switches,n.storages,n.neutral_grounds,n.study_faults,n.harmonics,getattr(s,'faults',[])]):raise ValueError('unaugmented balanced group circuit required')
    if s.duration_s>3:raise ValueError('native group duration budget')
    if not math.isfinite(s.step_s) or s.step_s<=0:raise ValueError('native group output sample budget')
    output_intervals=s.duration_s/s.step_s
    channels=8*len(s.motors)+10*len(n.generators)
    if not math.isfinite(output_intervals) or (2*output_intervals+2*len(s.events)+5)*channels*32>30_000_000:raise ValueError('native group output sample budget')
    machine=s.machines[0] if coupled else None;g=n.generators[0] if coupled else None;src=n.sources[0];line=n.lines[0];motors=s.motors;v=motors[0].rated_ll_v
    if coupled and (machine.model!='GENROU' or not machine.governor or not machine.exciter or machine.stabilizer or machine.generator_id!=g.id or g.mode!='pv'):raise ValueError('one PV GENROU/TGOV1/SEXS without PSS required')
    if any(m.thermal or m.contactor or m.mechanical_load or not m.connected for m in motors):raise ValueError('initially connected fan motors without native controllers required')
    terminals={t.id:t.connectivity_node_id for t in n.terminals};plant=motors[0].node_id;grid=terminals[src.terminal_id]
    if grid==plant or (g is not None and terminals[g.terminal_id]!=plant) or any(m.node_id!=plant for m in motors) or {terminals[line.terminal_from],terminals[line.terminal_to]}!={grid,plant}:raise ValueError('common motor/generator bus and direct feeder required')
    if any(sorted(t.phases)!=list('ABC') for t in [*n.terminals,*n.connectivity_nodes]) or (g is not None and sorted(g.phases)!=list('ABC')) or (src.phase_pu and any(x!=1. for x in src.phase_pu.values())):raise ValueError('balanced ABC source and terminals required')
    if (g is not None and g.ll_v!=v) or any(m.rated_ll_v!=v for m in motors) or any(x.nominal_ll_v!=v for x in n.voltage_levels):raise ValueError('common nominal voltage base required')
    if np.any(line.b_siemens):raise ValueError('no feeder charging')
    for values in [line.r_ohm,line.x_ohm]:
        values=np.array(values);balanced=np.full((3,3),values[0,1]);np.fill_diagonal(balanced,values[0,0])
        if not np.allclose(values,balanced,rtol=0,atol=1e-12):raise ValueError('balanced feeder required')
    for e in s.events:
        if not 0<e.time_s<s.duration_s or e.kind not in {'source_voltage','governor_step','voltage_reference_step','motor_open','motor_close'}:raise ValueError('unsupported native group event')
        targets=[src.id] if e.kind=='source_voltage' else [m.id for m in motors] if e.kind in {'motor_open','motor_close'} else [g.id] if g else []
        if e.equipment_id not in targets:raise ValueError('native group event target mismatch')


def coordinates(spec):
    settings=spec.coupled if spec.coupled else spec.induction
    d=spec.model_dump(mode='json');d['study']='motor_start';d['coupled']=None
    d['induction']=settings.model_dump(mode='json',exclude={'machines','faults','events'});d['induction']['events']=[]
    rows=[]
    for m in settings.motors:
        one=dict(d);one['induction']=dict(d['induction'],motors=[m.model_dump(mode='json')]);single=StudySpec.model_validate(one)
        if m.model=='double_cage':mapped,mapping=native5_coordinates(single);motor=mapped.induction.motors[0];model='Motor5'
        else:motor=m;mapping=dict(method='native Motor3 single-cage circuit');model='Motor3'
        rows.append((motor,model,mapping))
    return rows


def single_point(m,frequency,operating_point,voltage):
    x0=m.stator_x_pu+m.magnetizing_x_pu;x1=m.stator_x_pu+m.rotor_x_pu*m.magnetizing_x_pu/(m.rotor_x_pu+m.magnetizing_x_pu)
    if operating_point is None:return dict(speed_pu=0.,e1=0j,admittance=1/complex(m.stator_r_pu,x1))
    wb=2*math.pi*frequency;rate=wb*m.rotor_r_pu/(m.rotor_x_pu+m.magnetizing_x_pu)
    def response(w):
        response=1j*(x0-x1)*rate/(rate+1j*wb*(1-w));current=1/(complex(m.stator_r_pu,x1)+response)
        return response*current,current
    def residual(w):
        emf,current=response(w);return (emf*np.conj(current)).real*voltage**2-m.fan_torque_pu*w*w
    grid=np.linspace(operating_point.minimum_speed_pu,1.,501);roots=[]
    for lo,hi in zip(grid,grid[1:]):
        if residual(lo)*residual(hi)<=0:roots.append(brentq(residual,lo,hi,xtol=1e-13))
    if not roots:raise ValueError('native group single-cage high-speed equilibrium missing')
    w=max(roots);emf,current=response(w);return dict(speed_pu=w,e1=emf,admittance=current)


def terminal_gates(a,models):
    from andes.core import NumParam
    audit={}
    for name in sorted(models):
        model=getattr(a,name);before=equations(model);services={k:v.v_str for k,v in model.services.items()};algebra={k:v.e_str for k,v in model.algebs.items()}
        model.closed=NumParam(default=1.,vrange=(0,1),info='Benchmark stator switch; rotor/mechanics remain active')
        for key in ['Id','Iq']:
            var=getattr(model,key);var.e_str=f'closed * ({var.e_str}) + (1-closed) * {key}'
        model.cache.refresh();audit[name]=dict(differential_equations=before,services=services,algebra=algebra)
    a.prepare(quick=True,models=sorted(models),nomp=True)
    for name,original in audit.items():
        model=getattr(a,name)
        if equations(model)!=original['differential_equations'] or {k:v.v_str for k,v in model.services.items()}!=original['services']:raise ValueError('native differential laws/services changed')
        if any(v.e_str!=original['algebra'][k] for k,v in model.algebs.items() if k not in {'Id','Iq'}):raise ValueError('native nonterminal algebra changed')
    return dict(differential_equations_unchanged=True,services_unchanged=True,models=audit)


def native_run(spec,step,cache,*,driver=None):
    scope(spec)
    if not math.isfinite(step) or not 1e-5<=step<=.01:raise ValueError('native group step budget')
    from power_grid_env.andes_rms import ensure_andes_importable
    if not ensure_andes_importable():raise ImportError('ANDES extra required')
    import andes
    from andes.linsolvers.solverbase import Solver
    if andes.__version__!='2.0.0':raise ValueError('ANDES 2.0.0 required')
    n,s=spec.network,spec.coupled if spec.coupled else spec.induction;g=n.generators[0] if n.generators else None;p=s.machines[0] if g else None;src=n.sources[0];line=n.lines[0];rows=coordinates(spec);rated_v=rows[0][0].rated_ll_v
    a=andes.System(no_output=True,default_config=True,pycode_path=str(cache));adaptation=terminal_gates(a,{name for _,name,_ in rows})
    a.config.mva=n.s_base_va/1e6;a.config.freq=n.frequency_hz;kv=rated_v/1000;zb=rated_v**2/n.s_base_va;gf=p.rated_va/n.s_base_va if p else 0.
    z=complex(src.r_ohm+line.r_ohm[0][0]-line.r_ohm[0][1],src.x_ohm+line.x_ohm[0][0]-line.x_ohm[0][1])
    for i in [1,2]:a.add('Bus',idx=i,Vn=kv,v0=1.,a0=src.angle_a_rad)
    a.add('Slack',idx='grid',bus=1,Sn=n.s_base_va/1e6,Vn=kv,v0=src.ll_v/rated_v,a0=src.angle_a_rad)
    a.add('Line',idx='feed',bus1=1,bus2=2,Sn=n.s_base_va/1e6,Vn1=kv,Vn2=kv,fn=n.frequency_hz,r=z.real/zb,x=z.imag/zb,b=0.)
    if g:
        a.add('PV',idx='pv',bus=2,Sn=n.s_base_va/1e6,Vn=kv,p0=g.p_w/n.s_base_va,v0=g.v_set_pu)
        a.add('GENROU',idx='g',bus=2,gen='pv',Sn=p.rated_va/1e6,Vn=kv,fn=n.frequency_hz,M=2*p.inertia_s,D=p.damping_pu,ra=p.armature_r_pu,xl=p.leakage_pu,xd=p.x_d_pu,xq=p.x_q_pu,xd1=p.x_d_prime_pu,xd2=p.x_d_subtransient_pu,xq1=p.x_q_prime_pu,xq2=p.x_q_subtransient_pu,Td10=p.td_prime_s,Td20=p.td_subtransient_s,Tq10=p.tq_prime_s,Tq20=p.tq_subtransient_s,S10=0. if p.saturation=='disabled' else p.saturation_s10,S12=1. if p.saturation=='disabled' else p.saturation_s12)
        a.add('TGOV1',idx='gov',syn='g',Tn=p.rated_va/1e6,wref0=1.,**p.governor.model_dump(exclude={'model'}));a.add('SEXS',idx='avr',syn='g',**p.exciter.model_dump(exclude={'model'}))
    def motor_point(m,name,magnitude):
        point=initial_motor(m,n.frequency_hz,s.operating_point) if name=='Motor5' else single_point(m,n.frequency_hz,s.operating_point,magnitude)
        return point(magnitude) if callable(point) else point
    magnitude=g.v_set_pu if g else src.ll_v/rated_v
    if not g and s.operating_point:
        es=src.ll_v/rated_v*np.exp(1j*src.angle_a_rad)
        def equilibrium(value):
            v=complex(*value);admittance=sum(m.rated_va/n.s_base_va*motor_point(m,name,abs(v))['admittance'] for m,name,_ in rows)
            error=v-es/(1+z/zb*admittance)
            return [error.real,error.imag]
        solved=root(equilibrium,[es.real,es.imag],tol=1e-11)
        if max(abs(np.array(equilibrium(solved.x))))>1e-9:raise ValueError('native grid group equilibrium failed')
        magnitude=abs(complex(*solved.x))
    points=[];seed=0j;layout=[]
    for m,name,_ in rows:
        point=motor_point(m,name,magnitude)
        points.append(point);factor=m.rated_va/n.s_base_va;seed+=factor*point['admittance'];k=m.fan_torque_pu*factor
        rotor2=dict(rr2=m.rotor2_r_pu,xr2=m.rotor2_x_pu) if name=='Motor5' else {}
        a.add(name,idx=m.id,bus=2,Sn=m.rated_va/1e6,Vn=kv,fn=n.frequency_hz,rs=m.stator_r_pu,xs=m.stator_x_pu,xm=m.magnetizing_x_pu,rr1=m.rotor_r_pu,xr1=m.rotor_x_pu,Hm=m.inertia_s,c1=-k/2,c2=k,c3=k/2,u=0,**rotor2)
        layout.append((m,getattr(a,name),name))
    a.add('Shunt',idx='motor_seed',bus=2,Sn=n.s_base_va/1e6,Vn=kv,g=seed.real,b=seed.imag)
    a.PFlow.solver=Solver('spsolve');a.TDS.solver=Solver('spsolve');a.setup();a.PFlow.config.tol=1e-12
    if not a.PFlow.run():raise ValueError('native group seeded power flow failed')
    initial_q=float(a.PV.q.v[0])*n.s_base_va if g else None
    if g and ((g.q_min_var is not None and initial_q<g.q_min_var) or (g.q_max_var is not None and initial_q>g.q_max_var)):raise ValueError('native group generator initial Q limit')
    a.TDS.config.tf=s.duration_s;a.TDS.config.tstep=step;a.TDS.config.fixt=1;a.TDS.config.shrinkt=0;a.TDS.config.tol=1e-11;a.TDS.config.criteria=0;a.TDS.config.test_init=0;a.TDS.config.honest=1;a.TDS.init()
    seeded_v=a.Bus.v.v.copy();seeded_x=a.dae.x.copy();a.set_status('Shunt','motor_seed',0)
    addresses={};motor_indices=[]
    for (m,model,name),point in zip(layout,points):
        a.set_status(name,m.id,1);i=model.idx2uid(m.id);addresses[m.id]=i
        a.dae.x[model.slip.a[i]]=1-point['speed_pu'];voltage=a.Bus.v.v[1]*np.exp(1j*a.Bus.a.v[1])
        for prefix in ['e1']+(['e2'] if name=='Motor5' else []):
            emf=point[prefix]*voltage;a.dae.x[getattr(model,prefix+'q').a[i]]=emf.real;a.dae.x[getattr(model,prefix+'d').a[i]]=-emf.imag
        motor_indices.extend(int(getattr(model,k).a[i]) for k in ['slip','e1d','e1q']+(['e2d','e2q'] if name=='Motor5' else []))
    projection=Solver('spsolve')
    def project():
        for _ in range(40):
            a.vars_to_models();a.TDS.fg_update(a.exist.pflow_tds);res=float(max(abs(a.dae.g)))
            if res<1e-10:return res
            a.j_update(a.exist.pflow_tds);projection.worker.factorize=True;a.dae.y[:]-=projection.solve(a.dae.gy,a.dae.g)
        raise ValueError('native group algebraic projection failed')
    residual=project();nonmotor=np.setdiff1d(np.arange(len(a.dae.x)),motor_indices)
    if max(abs(a.Bus.v.v-seeded_v))>1e-8 or not np.array_equal(a.dae.x[nonmotor],seeded_x[nonmotor]) or (len(nonmotor)>0 and max(abs(a.dae.f[nonmotor]))>1e-8):raise ValueError('native group replacement disturbed generator/controller seed')
    if s.operating_point and max(abs(a.dae.f[motor_indices]))>1e-8:raise ValueError('native group loaded motor RHS not zero')
    driver_result=None
    if driver is not None:
        driver_result=driver(dict(system=a,project=project,layout=layout,addresses=addresses,network=n,settings=s,step_s=step,initial_residual=residual))
        records=driver_result['records'];maximum_g=driver_result['maximum_accepted_algebraic_residual_pu'];times=range(driver_result['accepted_steps'])
    else:
        records=[]
        def store(t,side):records.append((float(t),side,a.dae.x.copy(),a.dae.y.copy()))
        store(0.,'regular');events={};previous=0.;maximum_g=residual
        for e in s.events:events.setdefault(e.time_s,[]).append(e)
        regular=np.arange(math.ceil(s.duration_s/step))*step
        times=sorted(set([*[float(t) for t in regular if 0<t<s.duration_s and all(abs(t-e)>1e-12 for e in events)],s.duration_s,*events]))
        # Keep the native brackets of the requested regular output clock and all
        # event sides, while integrating and checking G at EVERY native step.
        wanted=np.r_[np.arange(math.ceil(s.duration_s/s.step_s))*s.step_s,s.duration_s]
        keep={s.duration_s,*events}
        for at in wanted:
            right=int(np.searchsorted(times,at))
            for index in [right-1,right]:
                if 0<=index<len(times):keep.add(times[index])
        for t in times:
            a.TDS.h=float(t-previous);a.dae.t[...]=t
            if not a.TDS.itm_step():raise ValueError('native group trapezoidal step failed: '+str(t))
            maximum_g=max(maximum_g,float(max(abs(a.dae.g))))
            if not math.isfinite(maximum_g) or maximum_g>1e-8:raise ValueError('native group accepted algebraic residual exceeds 1e-8')
            a.TDS.custom_event=False;a.b_update(a.exist.pflow_tds)
            if t in keep:store(t,'pre_event' if t in events else 'regular')
            if t in events:
                for e in events[t]:
                    if e.kind=='source_voltage':a.Slack.v0.v[0]=src.ll_v/rated_v*e.value
                    elif e.kind=='governor_step':a.TGOV1.pref0.v[0]+=e.value*gf
                    elif e.kind=='voltage_reference_step':a.SEXS.vref0.v[0]+=e.value
                    else:
                        m,model,name=next(row for row in layout if row[0].id==e.equipment_id);model.closed.v[addresses[m.id]]=0. if e.kind=='motor_open' else 1.
                before=a.dae.x.copy();maximum_g=max(maximum_g,project())
                if not np.array_equal(before,a.dae.x):raise ValueError('native event projection changed differential state')
                a.TDS.custom_event=True;store(t,'post_event')
            previous=float(t)
    x=np.array([r[2] for r in records]);y=np.array([r[3] for r in records]);channels={}
    def values(model,key,i=0):
        variable=getattr(model,key);return (x if variable.v_code=='x' else y)[:,int(variable.a[i])]
    mapping={}
    for m,model,name in layout:
        i=addresses[m.id];factor=m.rated_va/n.s_base_va;last='e2' if name=='Motor5' else 'e1'
        row=dict(speed_pu=1-values(model,'slip',i),e_real_pu=values(model,last+'q',i),e_imag_pu=-values(model,last+'d',i),current_pu=np.hypot(values(model,'Id',i),values(model,'Iq',i))/factor,bus_voltage_pu=y[:,int(a.Bus.v.a[1])],te_pu=values(model,'te',i)/factor,p_w=values(model,'p',i)*n.s_base_va,q_var=values(model,'q',i)*n.s_base_va)
        if ''.join(model.tm.e_str.split())!='ue*(aa+bb*slip+c2*slip*slip)-tm' or max(abs(values(model,'tm',i)/factor-m.fan_torque_pu*row['speed_pu']**2))>1e-7:raise ValueError('native group load mapping/residual failed')
        channels.update({m.id+'.'+k:v for k,v in row.items()});mapping[m.id]=next(r[2] for r in rows if r[0].id==m.id)
    if g:
        channels.update({g.id+'.'+k:values(a.GENROU,k) for k in ['delta','omega','e1q','e1d','e2d','e2q']})
        channels.update({g.id+'.TGOV1.valve_pu':values(a.TGOV1,'LAG_y')/gf,g.id+'.SEXS.field_pu':values(a.SEXS,'vout'),g.id+'.p_w':(values(a.GENROU,'vd')*values(a.GENROU,'Id')+values(a.GENROU,'vq')*values(a.GENROU,'Iq'))*n.s_base_va,g.id+'.q_var':(values(a.GENROU,'vq')*values(a.GENROU,'Id')-values(a.GENROU,'vd')*values(a.GENROU,'Iq'))*n.s_base_va})
    if not all(np.isfinite(v).all() for v in channels.values()):raise ValueError('native group nonfinite channels')
    source_root=Path(andes.__file__).parent
    return dict(completed=True,adapter_source_sha256=sha256_file(Path(__file__)),case_sha256=canonical_hash(spec),code_sha256=package_fingerprint(),step_s=step,backend_version=andes.__version__,time_s=[r[0] for r in records],sample_side=[r[1] for r in records],channels={k:v.tolist() for k,v in channels.items()},motor_count=len(rows),native_driver_metadata=(driver_result['metadata'] if driver_result is not None else None),native_steps_executed=len(times),retained_samples=len(records),output_retention='Native brackets of study output clock and every event side; every integration step still executed and G checked',motor_models={m.id:name for m,_,name in layout},mapping=mapping,terminal_adaptation=adaptation,initial_algebraic_residual_pu=residual,initial_q_var=initial_q,native_honest_jacobian=True,maximum_accepted_algebraic_residual_pu=maximum_g,native_source_sha256={str(p.relative_to(source_root)):sha256_file(p) for p in sorted(source_root.rglob('*.py'))},generated_source_sha256={p.name:sha256_file(p) for p in sorted(cache.glob('*.py'))},native_closed_loop_controller_qualified=False,field_validated=False,industrial_profile_qualified=False)


def worker(spec,step,*,driver=None):
    scope(spec);stock=Path(tempfile.gettempdir())/'power-grid-env-andes-pycode'
    if not (stock/'Motor5.py').is_file() or not (stock/'Motor3.py').is_file():raise ValueError('qualify stock native code cache first')
    hashes={p.name:sha256_file(p) for p in stock.glob('*.py')}
    with tempfile.TemporaryDirectory(prefix='pge-group-pycode-') as tmp:
        cache=Path(tmp)
        for p in stock.glob('*.py'):shutil.copyfile(p,cache/p.name)
        result=native_run(spec,step,cache,driver=driver)
    if hashes!={p.name:sha256_file(p) for p in stock.glob('*.py')}:raise ValueError('stock code cache changed')
    result['stock_generated_cache_unchanged']=True;return result


def native_reference(spec,step,timeout_s=120.):
    scope(spec)
    if not math.isfinite(step) or not 1e-5<=step<=.01 or not math.isfinite(timeout_s) or timeout_s<=0:raise ValueError('positive timeout and bounded step required')
    with tempfile.TemporaryDirectory(prefix='pge-native-group-') as tmp:
        root=Path(tmp);write_json(root/'study.json',spec.model_dump(mode='json'));env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
        with (root/'log').open('wb') as log:
            proc=subprocess.Popen([sys.executable,'-m','benchmarks.andes_group_motor_reference','--native',str(root/'study.json'),'--step',str(step),'--out',str(root/'result.json')],cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True);start=time.monotonic();reason=None
            try:
                while proc.poll() is None:
                    if time.monotonic()-start>=timeout_s:reason='native_timeout'
                    elif (root/'log').stat().st_size>1_000_000 or ((root/'result.json').exists() and (root/'result.json').stat().st_size>32_000_000):reason='native_output_budget'
                    if reason:kill_process_group(proc);break
                    time.sleep(.01)
                if reason:return dict(completed=False,status=reason)
                if (root/'log').stat().st_size>1_000_000:return dict(completed=False,status='native_output_budget')
                if proc.returncode!=0 or not (root/'result.json').exists():return dict(completed=False,status='native_worker_failed',exit_code=proc.returncode,log_tail=(root/'log').read_text(errors='replace')[-1800:])
                raw=(root/'result.json').read_bytes()
                if len(raw)>32_000_000:return dict(completed=False,status='native_output_budget')
                r=json.loads(raw)
                if r.get('case_sha256')!=canonical_hash(spec) or r.get('code_sha256')!=package_fingerprint() or r.get('step_s')!=step or r.get('backend_version')!='2.0.0' or r.get('adapter_source_sha256')!=sha256_file(Path(__file__)):raise ValueError('native group identity mismatch')
                return r
            finally:
                if proc.poll() is None:kill_process_group(proc)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--native',type=Path,required=True);p.add_argument('--step',type=float,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    write_json(a.out,worker(StudySpec.model_validate_json(a.native.read_text()),a.step))
