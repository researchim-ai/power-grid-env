"""Explicit native ANDES buses, source impedance and multivoltage motor groups."""
from pathlib import Path
import argparse,json,math,os,shutil,subprocess,sys,tempfile,time
import numpy as np
from benchmarks.andes_group_motor_reference import terminal_gates,single_point
from benchmarks.andes_motor_terminal import initial_motor
from benchmarks.double_cage_andes_limits import native5_coordinates
from benchmarks.native_closed_driver import NativeLoop
from benchmarks.multibus_si_motor import Circuit,CLOCKS
from benchmarks.multibus_operating_point import motor_case,validate_scope
from power_grid_env.study_spec import StudySpec
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json,sha256_file
from power_grid_env.ipc import kill_process_group

ROOT=Path(__file__).resolve().parents[1]
FILES=('andes_multibus_motor.py','multibus_si_motor.py','multibus_operating_point.py','multibus_multi_generator.py','independent_group_contactor.py','group_si_generator.py','native_closed_driver.py','native_checkpoint.py','andes_group_motor_reference.py','andes_motor_terminal.py','double_cage_andes_limits.py')


def identity():return {f:sha256_file(ROOT/'benchmarks'/f) for f in FILES}


def native_branch_input(z):
    """ANDES 2.0.0 yhk adds 1e-8 to each PU impedance component.

    Translate physical impedance into its exact native coordinates, rather
    than adding a base-dependent physical resistance/reactance to the study.
    This is a fixed analytic conversion, never a trajectory correction.
    """
    if not np.isfinite(z) or z.real<0 or z.imag<=0:raise ValueError('passive finite physical branch required')
    if z.imag==1e-8:raise ValueError('native zero-reactance input correction would change the requested branch')
    return dict(r=float(z.real-1e-8),x=float(z.imag-1e-8))


def scope(spec,step):
    if spec.coupled and any(e.kind=='generator_open' for e in spec.coupled.events):raise ValueError('native generator electrical-only opening is outside this adapter scope')
    if spec.coupled and spec.coupled.esst1a_exciters:raise ValueError('ESST1A is outside this ANDES adapter')
    if spec.coupled and spec.coupled.oel2c_limiters:raise ValueError('OEL2C feedback requires independent validation; stock ANDES OEL2C is outside this adapter')
    if spec.coupled and spec.coupled.underexcitation_limiters:raise ValueError('MNLEX2 feedback requires independent SI; stock native closed-loop UEL is not qualified')
    if spec.coupled and spec.coupled.generator_field_thermal:raise ValueError('field thermal trip and zero field voltage require the independent SI reference')
    if spec.coupled and spec.coupled.overexcitation_limiters:raise ValueError('OEL feedback requires the independent excitation SI reference; native OEL is not qualified')
    if spec.coupled and spec.coupled.generator_thermal:raise ValueError('generator thermal electrical-trip semantics require the protected SI reference; native comparison is pre-trip only')
    validate_scope(spec)
    if not math.isfinite(step) or not 1e-5<=step<=.01:raise ValueError('bounded native multibus step required')
    s=spec.coupled if spec.coupled else spec.induction
    if any(e.kind=='load_torque_scale' for e in s.events):raise ValueError('native load torque events outside scope')
    rows=4*s.duration_s/s.step_s+2*(len(s.events)+s.maximum_contactor_transitions+len(s.motors))+4
    columns=18*len(s.motors)+2*len(spec.network.connectivity_nodes)+10*len(spec.network.generators)
    if not math.isfinite(rows) or rows*columns*32>30_000_000:raise ValueError('native multibus output budget')


def execute(spec,step,cache):
    from power_grid_env.andes_rms import ensure_andes_importable
    if not ensure_andes_importable():raise ImportError('ANDES extra required')
    import andes
    from andes.linsolvers.solverbase import Solver
    if andes.__version__!='2.0.0':raise ValueError('ANDES 2.0.0 required')
    n,s=spec.network,spec.coupled if spec.coupled else spec.induction;before=identity();code=package_fingerprint()
    bare=motor_case(spec);passports={p.generator_id:p for p in s.machines} if n.generators else {};gen_layout=[]
    levels={v.id:v.nominal_ll_v for v in n.voltage_levels};nodes={p.id:i+1 for i,p in enumerate(n.connectivity_nodes)}
    volts={p.id:levels[p.voltage_level_id] for p in n.connectivity_nodes};term={t.id:t.connectivity_node_id for t in n.terminals}
    src=n.sources[0];source_node=term[src.terminal_id];base=volts[source_node]
    # Seed phase/tap-consistent no-load phasors, not a zero-angle guess on
    # every voltage level. A 150-degree transformer otherwise admits a wrong
    # power-flow branch near zero receiving voltage.
    guesses={source_node:src.ll_v/base*np.exp(1j*src.angle_a_rad)}
    links=[(term[l.terminal_from],term[l.terminal_to],1.+0j) for l in n.lines]
    for t in n.transformers:
        h,l=t.windings;left,right=term[h.terminal_id],term[l.terminal_id]
        ratio=l.nominal_ll_v*l.tap_pu/(h.nominal_ll_v*h.tap_pu)*volts[left]/volts[right]*np.exp(-1j*CLOCKS[t.vector_group]*math.pi/6)
        links.append((left,right,ratio))
    for _ in nodes:
        for left,right,ratio in links:
            if left in guesses and right not in guesses:guesses[right]=guesses[left]*ratio
            elif right in guesses and left not in guesses:guesses[left]=guesses[right]/ratio
    if set(guesses)!=set(nodes):raise ValueError('cannot seed disconnected native multibus circuit')
    models=[];points=[];mapping={}
    for original in s.motors:
        motor=original.model_copy(deep=True);model='Motor5' if motor.model=='double_cage' else 'Motor3'
        if model=='Motor5':
            one=bare.model_copy(deep=True);one.induction.motors=[motor]
            mapped,proof=native5_coordinates(one);motor=mapped.induction.motors[0];mapping[motor.id]=proof
        magnitude=abs(guesses[motor.node_id])
        point=initial_motor(motor,n.frequency_hz,s.operating_point) if model=='Motor5' else single_point(motor,n.frequency_hz,s.operating_point,magnitude)
        if callable(point):point=point(magnitude)
        if not motor.connected:point=dict(point,admittance=0j)
        models.append((motor,model));points.append(point)
    a=andes.System(no_output=True,default_config=True,pycode_path=str(cache));adaptation=terminal_gates(a,{name for _,name in models})
    if ''.join(a.Line.yhk.v_str.split())!='u/((r+1e-8)+1j*(x+1e-8))':raise ValueError('unrecognized native branch regularization')
    a.config.mva=n.s_base_va/1e6;a.config.freq=n.frequency_hz
    physical_branches={}
    def add_branch(name,left,right,nominal_left,nominal_right,z,**extra):
        physical_branches[name]=z
        a.add('Line',idx=name,bus1=left,bus2=right,Sn=n.s_base_va/1e6,Vn1=nominal_left/1000,Vn2=nominal_right/1000,fn=n.frequency_hz,b=0.,**native_branch_input(z),**extra)
    a.add('Bus',idx=0,Vn=base/1000,v0=src.ll_v/base,a0=src.angle_a_rad)
    for node,i in nodes.items():a.add('Bus',idx=i,Vn=volts[node]/1000,v0=abs(guesses[node]),a0=float(np.angle(guesses[node])))
    a.add('Slack',idx='grid',bus=0,Sn=n.s_base_va/1e6,Vn=base/1000,v0=src.ll_v/base,a0=src.angle_a_rad)
    zb=base**2/n.s_base_va
    add_branch('source_impedance',0,nodes[source_node],base,base,complex(src.r_ohm,src.x_ohm)/zb)
    for line in n.lines:
        left,right=term[line.terminal_from],term[line.terminal_to];v=volts[left];zb=v*v/n.s_base_va
        add_branch(line.id,nodes[left],nodes[right],v,v,complex(line.r_ohm[0][0]-line.r_ohm[0][1],line.x_ohm[0][0]-line.x_ohm[0][1])/zb)
    for t in n.transformers:
        h,l=t.windings;left,right=term[h.terminal_id],term[l.terminal_id];vh,vl=volts[left],volts[right]
        factor=n.s_base_va/h.rated_va*(h.nominal_ll_v/vh)**2*l.tap_pu**2/100
        resistance=t.load_loss_pct if h.r_pct is None else h.r_pct+l.r_pct
        add_branch(t.id,nodes[left],nodes[right],vh,vl,complex(resistance,t.x_hl_pct)*factor,
              trans=1,tap=(h.nominal_ll_v/l.nominal_ll_v)/(vh/vl)*h.tap_pu/l.tap_pu,phi=(CLOCKS[t.vector_group]*math.pi/6+math.pi)%(2*math.pi)-math.pi)
    for load in n.loads:
        node=term[load.terminal_id]
        a.add('Shunt',idx=load.id,bus=nodes[node],Sn=n.s_base_va/1e6,Vn=volts[node]/1000,g=sum(load.p_w.values())/n.s_base_va,b=-sum(load.q_var.values())/n.s_base_va)
    for j,g in enumerate(n.generators):
        p=passports[g.id];idx='g' if j==0 else f'g_{j}';pv='pv' if j==0 else f'pv_{j}'
        node=term[g.terminal_id];kv=g.ll_v/1000
        a.add('PV',idx=pv,bus=nodes[node],Sn=n.s_base_va/1e6,Vn=kv,p0=g.p_w/n.s_base_va,v0=g.v_set_pu)
        a.add('GENROU',idx=idx,bus=nodes[node],gen=pv,Sn=p.rated_va/1e6,Vn=kv,fn=n.frequency_hz,M=2*p.inertia_s,D=p.damping_pu,ra=p.armature_r_pu,xl=p.leakage_pu,
              xd=p.x_d_pu,xq=p.x_q_pu,xd1=p.x_d_prime_pu,xd2=p.x_d_subtransient_pu,xq1=p.x_q_prime_pu,xq2=p.x_q_subtransient_pu,Td10=p.td_prime_s,Td20=p.td_subtransient_s,Tq10=p.tq_prime_s,Tq20=p.tq_subtransient_s,
              S10=0. if p.saturation=='disabled' else p.saturation_s10,S12=1. if p.saturation=='disabled' else p.saturation_s12)
        a.add('TGOV1',idx='gov_'+idx,syn=idx,Tn=p.rated_va/1e6,wref0=1.,**p.governor.model_dump(exclude={'model'}))
        a.add('SEXS',idx='avr_'+idx,syn=idx,**p.exciter.model_dump(exclude={'model'}))
        gen_layout.append((g,p,idx,'gov_'+idx,'avr_'+idx,pv))
    layout=[]
    for (m,name),point in zip(models,points):
        factor=m.rated_va/n.s_base_va;k=m.fan_torque_pu*factor;kv=m.rated_ll_v/1000
        a.add(name,idx=m.id,bus=nodes[m.node_id],Sn=m.rated_va/1e6,Vn=kv,fn=n.frequency_hz,rs=m.stator_r_pu,xs=m.stator_x_pu,xm=m.magnetizing_x_pu,rr1=m.rotor_r_pu,xr1=m.rotor_x_pu,Hm=m.inertia_s,c1=-k/2,c2=k,c3=k/2,u=0,**(dict(rr2=m.rotor2_r_pu,xr2=m.rotor2_x_pu) if name=='Motor5' else {}))
        seed=factor*point['admittance'];a.add('Shunt',idx='seed_'+m.id,bus=nodes[m.node_id],Sn=n.s_base_va/1e6,Vn=kv,g=seed.real,b=seed.imag)
        layout.append((m,getattr(a,name),name))
    a.PFlow.solver=Solver('spsolve');a.TDS.solver=Solver('spsolve');a.setup();a.PFlow.config.tol=1e-12;a.PFlow.config.method='NR'
    if not a.PFlow.run():raise ValueError('native multibus seeded power flow failed')
    warm_iterations=0
    if s.operating_point:
        for iteration in range(40):
            updated=[]
            for m,model,name in layout:
                magnitude=float(a.Bus.v.v[a.Bus.idx2uid(nodes[m.node_id])])
                point=initial_motor(m,n.frequency_hz,s.operating_point) if name=='Motor5' else single_point(m,n.frequency_hz,s.operating_point,magnitude)
                updated.append(point(magnitude) if callable(point) else point)
            difference=max(abs(new['admittance']-old['admittance']) for new,old in zip(updated,points));points=updated;warm_iterations=iteration+1
            for (m,_,_),point in zip(layout,points):
                index=a.Shunt.idx2uid('seed_'+m.id);value=m.rated_va/n.s_base_va*point['admittance'];a.Shunt.g.v[index]=value.real;a.Shunt.b.v[index]=value.imag
            if not a.PFlow.run():raise ValueError('native multibus loaded power flow failed')
            if difference<1e-12:break
        else:raise ValueError('native multibus motor/PV equilibrium did not converge')
    initial_q_by_id={}
    for g,p,idx,gov,avr,pv in gen_layout:
        q=float(a.PV.q.v[a.PV.idx2uid(pv)])*n.s_base_va;initial_q_by_id[g.id]=q
        if (g.q_min_var is not None and q<g.q_min_var) or (g.q_max_var is not None and q>g.q_max_var):raise ValueError('native multibus generator initial Q limit: '+g.id)
    initial_q=next(iter(initial_q_by_id.values())) if len(initial_q_by_id)==1 else None
    branch_error=max(abs(a.Line.yhk.v[a.Line.idx2uid(name)]-1/z)/abs(1/z) for name,z in physical_branches.items())
    if branch_error>1e-12:raise ValueError('native branch admittance does not preserve physical impedance')
    a.TDS.config.tf=s.duration_s;a.TDS.config.tstep=step;a.TDS.config.fixt=1;a.TDS.config.shrinkt=0;a.TDS.config.tol=1e-13;a.TDS.config.g_scale=0;a.TDS.config.reset_tiny=0;a.TDS.config.criteria=0;a.TDS.config.test_init=0;a.TDS.config.honest=1;a.TDS.init()
    seeded=a.Bus.v.v.copy();seeded_x=a.dae.x.copy();addresses={};motor_states=[]
    for (m,model,name),point in zip(layout,points):
        a.set_status('Shunt','seed_'+m.id,0);a.set_status(name,m.id,1);i=model.idx2uid(m.id);model.closed.v[i]=float(m.connected);addresses[m.id]=i
        a.dae.x[model.slip.a[i]]=1-point['speed_pu']
        voltage=a.Bus.v.v[a.Bus.idx2uid(nodes[m.node_id])]*np.exp(1j*a.Bus.a.v[a.Bus.idx2uid(nodes[m.node_id])])
        for prefix in ['e1']+(['e2'] if name=='Motor5' else []):
            emf=point[prefix]*voltage;a.dae.x[getattr(model,prefix+'q').a[i]]=emf.real;a.dae.x[getattr(model,prefix+'d').a[i]]=-emf.imag
        motor_states.extend(int(getattr(model,key).a[i]) for key in ['slip','e1d','e1q']+(['e2d','e2q'] if name=='Motor5' else []))
    solver=Solver('spsolve')
    def project():
        for _ in range(40):
            a.vars_to_models();a.TDS.fg_update(a.exist.pflow_tds);res=float(max(abs(a.dae.g)))
            if res<1e-10:return res
            a.j_update(a.exist.pflow_tds);solver.worker.factorize=True;a.dae.y[:]-=solver.solve(a.dae.gy,a.dae.g)
        raise ValueError('native multibus algebraic projection failed')
    residual=project()
    if max(abs(a.Bus.v.v-seeded))>1e-8:raise ValueError('motor replacement changed seeded voltage')
    others=np.setdiff1d(np.arange(len(a.dae.x)),motor_states)
    if not np.array_equal(a.dae.x[others],seeded_x[others]):raise ValueError('motor replacement changed generator/controller states')
    generator_rhs=float(max(abs(a.dae.f[others]))) if len(others) else 0.
    motor_rhs=float(max(abs(a.dae.f[motor_states])))
    if generator_rhs>1e-8 or (s.operating_point and motor_rhs>1e-8):raise ValueError('native loaded initial RHS not zero')
    indices={node:a.Bus.idx2uid(i) for node,i in nodes.items()}
    driver=NativeLoop(spec)(dict(system=a,project=project,layout=layout,addresses=addresses,network=n,settings=s,step_s=step,
        generator_controls={g.id:dict(governor_index=a.TGOV1.idx2uid(gov),exciter_index=a.SEXS.idx2uid(avr),factor=p.rated_va/n.s_base_va) for g,p,idx,gov,avr,pv in gen_layout},
        initial_residual=residual,node_voltage_reader=lambda:{node:float(a.Bus.v.v[i]) for node,i in indices.items()},source_voltage_base_v=base))
    field_reaction_reference={}
    records=driver['records'];x=np.array([r[2] for r in records]);y=np.array([r[3] for r in records]);channels={}
    def values(model,key,i):
        var=getattr(model,key);return (x if var.v_code=='x' else y)[:,int(var.a[i])]
    for m,model,name in layout:
        i=addresses[m.id];factor=m.rated_va/n.s_base_va;last='e2' if name=='Motor5' else 'e1'
        row=dict(speed_pu=1-values(model,'slip',i),e_real_pu=values(model,last+'q',i),e_imag_pu=-values(model,last+'d',i),
            current_pu=np.hypot(values(model,'Id',i),values(model,'Iq',i))/factor,bus_voltage_pu=y[:,int(a.Bus.v.a[indices[m.node_id]])],
            te_pu=values(model,'te',i)/factor,p_w=values(model,'p',i)*n.s_base_va,q_var=values(model,'q',i)*n.s_base_va)
        if max(abs(values(model,'tm',i)/factor-m.fan_torque_pu*row['speed_pu']**2))>1e-7:raise ValueError('native multibus fan torque mismatch')
        channels.update({m.id+'.'+key:value.tolist() for key,value in row.items()})
    for g,p,idx,gov,avr,pv in gen_layout:
        gi=a.GENROU.idx2uid(idx);vi=a.TGOV1.idx2uid(gov);ei=a.SEXS.idx2uid(avr)
        factor=p.rated_va/n.s_base_va
        field_reaction_reference[g.id]=values(a.GENROU,'XadIfd',gi).tolist()
        for key in ('delta','omega','e1q','e1d','e2d','e2q'):channels[g.id+'.'+key]=values(a.GENROU,key,gi).tolist()
        channels.update({g.id+'.TGOV1.valve_pu':(values(a.TGOV1,'LAG_y',vi)/factor).tolist(),g.id+'.SEXS.field_pu':values(a.SEXS,'vout',ei).tolist(),
            g.id+'.p_w':((values(a.GENROU,'vd',gi)*values(a.GENROU,'Id',gi)+values(a.GENROU,'vq',gi)*values(a.GENROU,'Iq',gi))*n.s_base_va).tolist(),
            g.id+'.q_var':((values(a.GENROU,'vq',gi)*values(a.GENROU,'Id',gi)-values(a.GENROU,'vd',gi)*values(a.GENROU,'Iq',gi))*n.s_base_va).tolist()})
    channels.update(driver['metadata'].pop('auxiliary_channels'));node_voltage={}
    for node,i in indices.items():
        phasor=y[:,int(a.Bus.v.a[i])]*np.exp(1j*y[:,int(a.Bus.a.a[i])])*volts[node]/math.sqrt(3)
        node_voltage[node]=dict(real_v=phasor.real.tolist(),imag_v=phasor.imag.tolist())
    if before!=identity() or code!=package_fingerprint():raise ValueError('native multibus producer changed during run')
    source_root=Path(andes.__file__).parent
    return dict(completed=True,backend_version=andes.__version__,case_sha256=canonical_hash(spec),code_sha256=code,source_sha256=before,
                step_s=step,time_s=[r[0] for r in records],sample_side=[r[1] for r in records],channels=channels,node_voltage=node_voltage,field_reaction_reference=field_reaction_reference,
                native_driver_metadata=driver['metadata'],native_steps_executed=driver['accepted_steps'],maximum_accepted_algebraic_residual_pu=driver['maximum_accepted_algebraic_residual_pu'],
                mapping=mapping,terminal_adaptation=adaptation,controller_commands_precomputed=False,explicit_source_terminal=True,
                native_source_sha256={str(p.relative_to(source_root)):sha256_file(p) for p in sorted(source_root.rglob('*.py'))},
                generated_source_sha256={p.name:sha256_file(p) for p in cache.glob('*.py')},
                native_solver_config=dict(tol=1e-13,g_scale=0,reset_tiny=0,honest=1),
                native_branch_coordinates=dict(analytic_input_offset_pu=1e-8,maximum_relative_admittance_error=float(branch_error),physical_impedance_preserved=True,native_services_unchanged=True,power_flow_method='NR'),
                initialization=dict(initial_motor_connected={m.id:m.connected for m in s.motors},generator_states_preserved_during_motor_replacement=True,motors_loaded=s.operating_point is not None,loaded_seed_iterations=warm_iterations,maximum_motor_initial_rhs=motor_rhs,maximum_generator_initial_rhs=generator_rhs,generator_initial_q_var=initial_q,generator_initial_q_var_by_id=initial_q_by_id),
                field_validated=False,industrial_profile_qualified=False)


def worker(spec,step):
    scope(spec,step);stock=Path(tempfile.gettempdir())/'power-grid-env-andes-pycode'
    if not all((stock/(name+'.py')).is_file() for name in ('Motor3','Motor5')):raise ValueError('prepare stock native cache first')
    hashes={p.name:sha256_file(p) for p in stock.glob('*.py')}
    try:
        with tempfile.TemporaryDirectory(prefix='pge-multibus-code-') as tmp:
            for p in stock.glob('*.py'):shutil.copyfile(p,Path(tmp)/p.name)
            result=execute(spec,step,Path(tmp))
    finally:
        if hashes!={p.name:sha256_file(p) for p in stock.glob('*.py')}:raise ValueError('stock native cache changed')
    result['stock_generated_cache_unchanged']=True;return result


def native_reference(spec,step,timeout_s=120.):
    scope(spec,step)
    if not math.isfinite(timeout_s) or timeout_s<=0:raise ValueError('positive native timeout required')
    expected=identity();code=package_fingerprint()
    with tempfile.TemporaryDirectory(prefix='pge-native-multibus-') as tmp:
        root=Path(tmp);write_json(root/'study.json',spec.model_dump(mode='json'));env=dict(os.environ,PYTHONPATH=str(ROOT))
        with (root/'log').open('wb') as log:
            process=subprocess.Popen([sys.executable,'-m','benchmarks.andes_multibus_motor','--native',str(root/'study.json'),'--step',str(step),'--out',str(root/'result.json')],cwd=root,env=env,stdout=log,stderr=log,stdin=subprocess.DEVNULL,start_new_session=True)
            start=time.monotonic();reason=None
            try:
                while process.poll() is None:
                    if time.monotonic()-start>=timeout_s:reason='native_timeout'
                    elif (root/'log').stat().st_size>1_000_000 or ((root/'result.json').exists() and (root/'result.json').stat().st_size>32_000_000):reason='native_output_budget'
                    if reason:kill_process_group(process);break
                    time.sleep(.01)
                if reason:return dict(completed=False,status=reason)
                if (root/'log').stat().st_size>1_000_000:return dict(completed=False,status='native_output_budget')
                if process.returncode!=0 or not (root/'result.json').exists():return dict(completed=False,status='native_worker_failed',log_tail=(root/'log').read_text(errors='replace')[-2500:])
                if (root/'result.json').stat().st_size>32_000_000:return dict(completed=False,status='native_output_budget')
                result=json.loads((root/'result.json').read_text())
                if result['case_sha256']!=canonical_hash(spec) or result['step_s']!=step or result['backend_version']!='2.0.0' or result['source_sha256']!=expected or identity()!=expected or result['code_sha256']!=code or package_fingerprint()!=code:raise ValueError('native multibus identity mismatch')
                return result
            finally:
                if process.poll() is None:kill_process_group(process)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--native',required=True,type=Path);p.add_argument('--step',required=True,type=float);p.add_argument('--out',required=True,type=Path);args=p.parse_args()
    write_json(args.out,worker(StudySpec.model_validate_json(args.native.read_text()),args.step))
