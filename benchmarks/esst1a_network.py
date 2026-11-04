"""Native OpenIPSL ESST1A in feedback with an independent SI winding network.

Placeholder SEXS slots in the legacy SI adapter are frozen and disconnected;
only the native FMU drives impressed GENROU field voltage. No production RHS,
network stamps, operating-point state vector or event clocks are consumed.
"""
from pathlib import Path
from types import SimpleNamespace
import copy,ctypes as C,math,sys,time
import numpy as np
from benchmarks.esst1a_native import NativeExciter,verify
from benchmarks.generator_excitation_si import ExcitationCircuit
from power_grid_env.exciter_esst1a import PORTS
from power_grid_env.study_spec import StudySpec
from power_grid_env.model import canonical_hash


def scope(spec,maximum_duration_s=5.,allow_cold_prestart=False):
    if not math.isfinite(maximum_duration_s) or not 0<maximum_duration_s<=60:raise ValueError('bounded native ESST1A horizon required')
    if spec.study!='coupled_rms' or not spec.coupled or not spec.coupled.esst1a_exciters:raise ValueError('coupled ESST1A profile required')
    s=spec.coupled;targets={p.generator_id for p in s.esst1a_exciters}
    if len(targets)!=len(s.esst1a_exciters) or not targets<={g.id for g in spec.network.generators}:raise ValueError('unique known ESST1A targets required')
    if s.oel2c_limiters or s.overexcitation_limiters or (s.operating_point is None and (not allow_cold_prestart or any(m.connected for m in s.motors))) or s.duration_s>maximum_duration_s:raise ValueError('loaded ESST1A reference subset required; OEL adapters not qualified')
    if any(m.contactor or m.thermal for m in s.motors):raise ValueError('motor automatic controls outside native ESST1A scope')
    if any(e.kind not in ('source_voltage','motor_open','motor_close','generator_open','governor_step','voltage_reference_step','esst1a_port_set') for e in s.events):raise ValueError('unsupported ESST1A network event')
    if any(e.kind=='esst1a_port_set' and e.equipment_id not in targets for e in s.events):raise ValueError('unknown ESST1A port target')
    if any(m.model!='GENROU' or m.governor is None or (m.stabilizer is not None and (m.generator_id not in targets or m.stabilizer_connection!='external_ESST1A')) or (m.exciter is not None if m.generator_id in targets else m.exciter is None) for m in s.machines):raise ValueError('GENROU/TGOV1 and exclusive ESST1A or SEXS required')
    data=spec.model_dump(mode='json');data['coupled']['esst1a_exciters']=[];data['coupled']['takeover_oels']=[];data['coupled']['faults']=[]
    data['coupled']['events']=[e for e in data['coupled']['events'] if e['kind']!='esst1a_port_set']
    for m in data['coupled']['machines']:
        if m['generator_id'] in targets:
            m['exciter']=dict(model='SEXS',TATB=.25,TB=.2,K=10.,TE=.05,EMIN=0.,EMAX=100.)
            m['stabilizer']=None;m['stabilizer_connection']='SEXS'
    return StudySpec.model_validate(data)


def uel_event_reasons(policy,before,after,own_root=False,own_time=False,step_request=False):
    """Route events to this FMU, without broadcasting unrelated plant events."""
    if policy not in ('broadcast','causal'):raise ValueError('unknown native UEL event policy')
    reasons=[]
    if policy=='broadcast':reasons.append('legacy_broadcast')
    if own_root:reasons.append('own_state_event')
    if before!=after:reasons.append('input_discontinuity')
    if own_time:reasons.append('own_time_event')
    if step_request:reasons.append('own_step_event')
    return reasons


def run(spec,out,root,tolerance=1e-10,*,native_uels=False,uel_event_policy='causal',uel_root_policy='continuous_atoms',linearize=False,maximum_duration_s=5.,audit_network=False,allow_cold_prestart=False):
    from benchmarks.native_uel_roots import POLICIES
    if uel_root_policy not in POLICIES:raise ValueError('unknown native UEL root policy')
    if uel_event_policy not in ('broadcast','causal'):raise ValueError('unknown native UEL event policy')
    if not math.isfinite(tolerance) or not 0<tolerance<=1e-6:raise ValueError('native tolerance required')
    baseline=scope(spec,maximum_duration_s,allow_cold_prestart)
    if linearize and (spec.coupled.events or spec.coupled.faults or spec.coupled.underexcitation_limiters or spec.coupled.generator_thermal or spec.coupled.generator_field_thermal or any(not m.connected for m in spec.coupled.motors)):raise ValueError('strict connected nonhybrid equilibrium required for native modal reference')
    if native_uels:
        if not spec.coupled.underexcitation_limiters:raise ValueError('native UEL feedback requires an explicit UEL passport')
        if not {p.generator_id for p in spec.coupled.underexcitation_limiters}<={p.generator_id for p in spec.coupled.esst1a_exciters}:raise ValueError('native UEL targets require ESST1A in this joint profile')
        if any(p.initialization!='inactive_equilibrium' or min(p.T_M,p.T_F2,p.K_F2)<1e-6 for p in spec.coupled.underexcitation_limiters):raise ValueError('joint native UEL requires strict inactive equilibrium and dynamic blocks')
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False);identity=verify(root)
    fmpy_path=str(Path(root).resolve()/'fmpy')
    if fmpy_path not in sys.path:sys.path.insert(0,fmpy_path)
    from fmpy.sundials import CVodeSolver
    from fmpy.sundials.libraries import sundials_cvode
    import fmpy
    if fmpy.__version__!=identity['fmpy_version'] or not Path(fmpy.__file__).resolve().is_relative_to(Path(fmpy_path)):raise ValueError('wrong FMI master package')
    plant=ExcitationCircuit(baseline,maximum_duration_s=maximum_duration_s);state=plant.state.copy();nplant=len(state);blocks=[];time_s=0.;output_times=[];sides=[];channels={};node_voltage={};event_log=[]
    base_admittance=plant.Y.copy();active_faults=set();faults={f.id:f for f in spec.coupled.faults}
    uel_iterations=dict(updated=0,skipped_unaffected=0)
    network_balance=[];calls=0;events_count=0;started=time.monotonic();callback_error=[]
    ports={g.g.id:(i,node,g,at) for i,(node,g,at) in enumerate(plant.ports)}
    def reaction(gid):
        i,node,g,at=ports[gid];plant.electrical(state)
        return float(ExcitationCircuit.reaction(g,state[at],plant.generator_currents[i]))
    try:
        nx=nplant;nz=0
        controls={};external={};native_uel_blocks={};native_oel_blocks={};native_pss_blocks={};frozen_uel_addresses={};exciter_ids={p.generator_id for p in spec.coupled.esst1a_exciters}
        for p in spec.coupled.esst1a_exciters:
            i,node,g,at=ports[p.generator_id];voltage=plant.electrical(state)[0]
            field0=g.field_voltage(state[at]);current0=reaction(p.generator_id)
            block=NativeExciter(p,field0,abs(voltage[node])/g.vbase,current0,out/('block-'+str(len(blocks))),root,spec.coupled.duration_s,tolerance)
            block.a=slice(nx,nx+block.nx);block.z=slice(nz,nz+block.nz);nx+=block.nx;nz+=block.nz;blocks.append(block)
            controls[p.generator_id]=dict(reference_increment=0.,ports={key:getattr(p,key,0.) for key in PORTS})
            external[p.generator_id]=field0
            g.field_voltage=lambda x,g=g,gid=p.generator_id:external[gid]*float(g.field_enabled)
        if native_uels:
            from benchmarks.mnlex2_model_exchange import NativeUEL
            for p in spec.coupled.underexcitation_limiters:
                i,node,g,at=ports[p.generator_id];voltage=plant.electrical(state)[0];v=voltage[node];power=3*v*np.conj(plant.generator_currents[i])/g.p.rated_va
                b=NativeUEL(p,power.real,power.imag,abs(v)/g.vbase,out/('uel-'+str(len(native_uel_blocks))),root,spec.coupled.duration_s,tolerance,root_policy=uel_root_policy)
                b.a=slice(nx,nx+b.nx);b.z=slice(nz,nz+b.nz);nx+=b.nx;nz+=b.nz;native_uel_blocks[p.generator_id]=b
                frozen_uel_addresses[p.generator_id]=plant.uels.pop(i)[0]
        from benchmarks.ex02_native_blocks import NativeAux
        for p in spec.coupled.takeover_oels:
            i,node,g,at=ports[p.generator_id];current0=reaction(p.generator_id)
            if current0>=p.current_limit_pu-1e-7 or external[p.generator_id]>=p.maximum_field_pu-1e-7:raise ValueError('native initial takeover OEL limit or boundary')
            b=NativeAux(p,'oel',current0,1.,out/('oel-'+str(len(native_oel_blocks))),root,spec.coupled.duration_s,tolerance)
            b.a=slice(nx,nx+b.nx);b.z=slice(nz,nz+b.nz);nx+=b.nx;nz+=b.nz;native_oel_blocks[p.generator_id]=b
        for m in spec.coupled.machines:
            if m.stabilizer is None:continue
            i,node,g,at=ports[m.generator_id];voltage=plant.electrical(state)[0]
            b=NativeAux(m.stabilizer,'pss',0.,abs(voltage[node])/g.vbase,out/('pss-'+str(len(native_pss_blocks))),root,spec.coupled.duration_s,tolerance,generator_id=m.generator_id)
            b.a=slice(nx,nx+b.nx);b.z=slice(nz,nz+b.nz);nx+=b.nx;nz+=b.nz;native_pss_blocks[m.generator_id]=b
        all_blocks=[*native_pss_blocks.values(),*native_oel_blocks.values(),*blocks,*native_uel_blocks.values()]
        native_nz=nz
        def sync():
            voltage=plant.electrical(state)[0]
            for gid,b in native_pss_blocks.items():
                i,node,g,at=ports[gid];terminal=voltage[node] if plant.generator_closed(i) else g.emf(state[at]);b.set_inputs([state[at.start+1]-1.,abs(terminal)/g.vbase])
            for gid,b in native_oel_blocks.items():b.set_inputs([reaction(gid)])
            for gid,b in native_uel_blocks.items():
                i,node,g,at=ports[gid];terminal=voltage[node] if plant.generator_closed(i) else g.emf(state[at]);power=3*terminal*np.conj(plant.generator_currents[i])/g.p.rated_va
                b.set_inputs([power.real,power.imag,abs(terminal)/g.vbase])
            for b in blocks:
                gid=b.p.generator_id;i,node,g,at=ports[gid];control=controls[gid];q=control['ports']
                terminal=voltage[node] if plant.generator_closed(i) else g.emf(state[at])
                uel=native_uel_blocks[gid].output() if gid in native_uel_blocks else plant.uel_values(state,i)[1] if i in plant.uels else 0.
                b.set_inputs([abs(terminal)/g.vbase,reaction(gid),control['reference_increment'],min(q['oel_ceiling_pu'],native_oel_blocks[gid].output()) if gid in native_oel_blocks else q['oel_ceiling_pu'],q['uel_error_floor_pu'],q['uel_field_floor_pu'],q['uel_sum_pu']+uel,q['supplementary_voltage_pu']+(native_pss_blocks[gid].output() if gid in native_pss_blocks else 0.),q['supplementary_field_pu']])
                external[gid]=b.output()
        def set_time(t):
            nonlocal time_s
            time_s=float(t)
            for b in all_blocks:b.f.setTime(time_s)
        def get_x(ptr,n):
            arr=np.ctypeslib.as_array(ptr,(n,));arr[:nplant]=state
            for b in all_blocks:b.f.getContinuousStates(np.ctypeslib.as_ctypes(arr[b.a]),b.nx)
        def set_x(ptr,n):
            nonlocal state
            arr=np.ctypeslib.as_array(ptr,(n,));state=arr[:nplant].copy()
            for b in all_blocks:b.f.setContinuousStates(np.ctypeslib.as_ctypes(arr[b.a]),b.nx)
        def get_dx(ptr,n):
            nonlocal calls
            calls+=1
            if calls>2_000_000 or time.monotonic()-started>180:raise ValueError('native coupled work budget')
            sync();arr=np.ctypeslib.as_array(ptr,(n,));arr[:nplant]=plant.rhs(time_s,state)
            for gid in exciter_ids:
                at=ports[gid][3];arr[at.start+4:at.start+6]=0.
            for a in frozen_uel_addresses.values():arr[a:a+2]=0.
            for b in all_blocks:b.f.getDerivatives(np.ctypeslib.as_ctypes(arr[b.a]),b.nx)
            if not np.isfinite(arr).all():raise ValueError('nonfinite native coupled RHS')
        def nominals(ptr,n):
            arr=np.ctypeslib.as_array(ptr,(n,));arr[:nplant]=np.maximum(1.,abs(plant.state))
            for b in all_blocks:b.f.getNominalsOfContinuousStates(np.ctypeslib.as_ctypes(arr[b.a]),b.nx)
        guards=[]
        def get_z(ptr,n):
            sync();arr=np.ctypeslib.as_array(ptr,(n,))
            for b in all_blocks:
                if hasattr(b,'event_indicators'):b.event_indicators(arr[b.z])
                else:b.f.getEventIndicators(np.ctypeslib.as_ctypes(arr[b.z]),b.nz)
            for j,(_,guard) in enumerate(guards):arr[native_nz+j]=guard(time_s,state)
            if not np.isfinite(arr).all():raise ValueError('nonfinite native coupled event indicator')
        class Input:
            def apply(self,t):pass
        class Checked(CVodeSolver):
            def f(self,*args):
                try:return super().f(*args)
                except Exception as e:callback_error.append(str(e));return -1
            def g(self,*args):
                try:return super().g(*args)
                except Exception as e:callback_error.append(str(e));return -1
        def solver_at(t):
            nonlocal guards
            sync();guards=[(key,g) for key,g in plant.extra_guards(state) if not (key[0]=='controller' and plant.ports[key[1]][1].g.id in exciter_ids and key[2]==1)]
            solver=Checked(nx,native_nz+len(guards),get_x,set_x,get_dx,get_z,nominals,set_time,Input(),t,maxStep=min(.002,spec.coupled.step_s),relativeTolerance=tolerance,maxNumSteps=20000)
            from benchmarks.native_uel_roots import indicator_directions
            native_directions=[0]*native_nz
            for b in native_uel_blocks.values():native_directions[b.z]=indicator_directions(b.root_policy,b.native_nz)
            direction=(C.c_int*(native_nz+len(guards)))(*(native_directions+[int(g.direction) for _,g in guards]))
            function=sundials_cvode.CVodeSetRootDirection;function.argtypes=[C.c_void_p,C.POINTER(C.c_int)];function.restype=C.c_int
            if function(solver.cvode_mem,direction)!=0:raise ValueError('native root-direction setup failed')
            return solver
        def record(t,side):
            set_time(t);sync();v,st,rot,torque=plant.electrical(state);values={}
            if audit_network:network_balance.append(plant.physical_balance(v,st))
            for i,(m,p) in enumerate(zip(plant.s.motors,plant.p)):
                flux=state[p['state']].reshape(-1,2);flux=flux[:,0]+1j*flux[:,1]
                emf=1j*plant.wb*(p['lm']*np.linalg.solve(p['L'],np.ones(len(p['rr']))))@flux/p['vbase'];power=3*v[p['bus']]*np.conj(st[i])
                row=dict(speed_pu=state[p['speed']],e_real_pu=emf.real,e_imag_pu=emf.imag,current_pu=abs(st[i])/p['ibase'],bus_voltage_pu=abs(v[p['bus']])/p['vbase'],p_w=power.real,q_var=power.imag,te_pu=torque[i],connected=float(plant.cfg['connected'][i]))
                values.update({m.id+'.'+k:float(x) for k,x in row.items()})
            for i,(node,g,at) in enumerate(plant.ports):
                terminal=v[node] if plant.generator_closed(i) else g.emf(state[at]);row=g.signals(state[at],plant.generator_currents[i],terminal)
                values.update(row)
                error=g.vref-abs(terminal)/g.vbase
                if i in plant.limiters:
                    a,p=plant.limiters[i];error-=min(max(state[a+1],0.),p.maximum_voltage_bias_pu)
                if i in plant.uels:error+=plant.uel_values(state,i)[1]
                values[g.g.id+'.SEXS.reference_pu']=float(plant.cfg['generator_vref'][i])
                values[g.g.id+'.SEXS.error_pu']=float(error)
            values.update(plant.extra_signals(state))
            for b in blocks:
                gid=b.p.generator_id
                for key in list(values):
                    if key.startswith(gid+'.SEXS.'):del values[key]
                q=b.signals()|controls[gid]['ports']|dict(reference_increment_pu=controls[gid]['reference_increment'],field_current_pu=reaction(gid))
                values.update({gid+'.ESST1A.'+key:float(value) for key,value in q.items()})
            for gid,b in native_uel_blocks.items():
                i,node,g,at=ports[gid];terminal=v[node] if plant.generator_closed(i) else g.emf(state[at]);power=3*terminal*np.conj(plant.generator_currents[i])/g.p.rated_va
                q=b.signals()|dict(p_pu=power.real,q_pu=power.imag,voltage_pu=abs(terminal)/g.vbase)
                values.update({gid+'.UEL.'+key:float(value) for key,value in q.items()})
            for gid,b in native_oel_blocks.items():values.update({gid+'.TakeoverOEL.'+k:float(x) for k,x in b.signals().items()})
            for gid,b in native_pss_blocks.items():values.update({gid+'.IEEEST.'+k:float(x) for k,x in b.signals().items()})
            for fault in faults.values():
                fault_voltage=complex(plant.electrical(state)[0][plant.nodes[fault.node_id]])
                current=fault_voltage/complex(fault.r_ohm,fault.x_ohm) if fault.id in active_faults else 0j
                power=3*fault_voltage*np.conj(current)
                values.update({fault.id+'.'+key:float(value) for key,value in dict(active=fault.id in active_faults,current_a=abs(current),p_w=power.real,q_var=power.imag).items()})
            if not all(math.isfinite(x) for x in values.values()):raise ValueError('nonfinite native output')
            if channels and set(values)!=set(channels):raise ValueError('native channel inventory drift')
            output_times.append(float(t));sides.append(side)
            for k,value in values.items():channels.setdefault(k,[]).append(float(value))
            for node,index in plant.nodes.items():node_voltage.setdefault(node,[]).append(complex(v[index]))
        if linearize:
            def branches():
                sync();result=[]
                for b in blocks:
                    q=b.signals();raw=q['regulator_raw_pu'];p=b.p
                    result.append((raw<=p.V_AMIN,raw>=p.V_AMAX,q['error_pu']<=p.V_IMIN,q['error_pu']>=p.V_IMAX,q['current_limit_pu']>0,*(q[k]>0 for k in ('oel_active','uel2_active','uel3_active','rectifier_upper_active','rectifier_lower_active'))))
                for b in native_oel_blocks.values():
                    raw=b.signals()['raw_ceiling_pu'];result.append((raw<=b.p.minimum_field_pu,raw>=b.p.maximum_field_pu))
                for b in native_pss_blocks.values():
                    q=b.signals();result.append((q['voltage_gate'],q['raw_pu']<=b.p.LSMIN,q['raw_pu']>=b.p.LSMAX))
                return tuple(result)
            set_time(0.);sync();full=np.empty(nx);get_x(np.ctypeslib.as_ctypes(full),nx)
            frozen={a for gid,(_,_,_,at) in ports.items() if gid in exciter_ids for a in range(at.start+4,at.start+6)}
            indices=np.array([i for i in range(nx) if i not in frozen],dtype=int)
            if len(indices)>128:raise ValueError('native modal state budget')
            original_branch=branches();matrices=[]
            for relative in (1e-6,5e-7):
                matrix=np.empty((len(indices),len(indices)))
                for column,address in enumerate(indices):
                    h=relative*max(1.,abs(full[address]))
                    for attempt in range(32):
                        plus=full.copy();minus=full.copy();plus[address]+=h;minus[address]-=h
                        if plus[address]==full[address] or minus[address]==full[address]:raise ValueError('native modal perturbation not representable')
                        set_x(np.ctypeslib.as_ctypes(plus),nx);bp=branches()
                        set_x(np.ctypeslib.as_ctypes(minus),nx);bm=branches()
                        if bp==bm==original_branch:break
                        h/=2
                    else:raise ValueError('native modal perturbation crosses limiter branch')
                    set_x(np.ctypeslib.as_ctypes(plus),nx);dp=np.empty(nx);get_dx(np.ctypeslib.as_ctypes(dp),nx)
                    set_x(np.ctypeslib.as_ctypes(minus),nx);dm=np.empty(nx);get_dx(np.ctypeslib.as_ctypes(dm),nx)
                    matrix[:,column]=(dp[indices]-dm[indices])/(2*h)
                matrices.append(matrix)
            set_x(np.ctypeslib.as_ctypes(full),nx);sync();residual=np.empty(nx);get_dx(np.ctypeslib.as_ctypes(residual),nx)
            change=float(np.linalg.norm(matrices[0]-matrices[1],ord=np.inf)/max(1.,np.linalg.norm(matrices[1],ord=np.inf)))
            if not np.isfinite(change) or change>1e-5:raise ValueError('native linearization not converged')
            return dict(completed=True,status='completed',state_matrix_per_s=matrices[1].tolist(),eigenvalues_per_s=[[float(z.real),float(z.imag)] for z in np.linalg.eigvals(matrices[1])],
                state_count=len(indices),native_state_indices=indices.tolist(),relative_matrix_change=change,relative_steps=[1e-6,5e-7],branch_preserving_steps=True,independent_SI_network=True,
                production_stamps_used=False,production_AVR_RHS_used=False,primary_state_vector_imported=False,native_ESST1A_in_feedback=True,native_PSS_in_feedback=bool(native_pss_blocks),native_takeover_OEL_in_feedback=bool(native_oel_blocks),
                initialization=plant.initialization,reference=identity,tolerance=tolerance,industrial_profile_qualified=False)
        scheduled={}
        for e in spec.coupled.events:scheduled.setdefault(e.time_s,[]).append(e)
        for fault in faults.values():
            for at,kind in [(fault.apply_s,'fault_apply'),(fault.clear_s,'fault_clear')]:
                scheduled.setdefault(at,[]).append(SimpleNamespace(kind=kind,equipment_id=fault.id))
        grid=np.r_[np.arange(math.ceil(spec.coupled.duration_s/spec.coupled.step_s))*spec.coupled.step_s,spec.coupled.duration_s]
        targets=sorted(set([float(t) for t in grid if t>0]+list(scheduled)));cursor=0.;record(0.,'initial');solver=solver_at(0.)
        for target in targets:
            while cursor<target-1e-13:
                next_time=min([target,*[b.next_time for b in all_blocks]])
                try:event,roots,at=solver.step(cursor,next_time)
                except RuntimeError as exc:raise ValueError('native step failed: '+str(callback_error)) from exc
                if callback_error:raise ValueError('native callback: '+callback_error[0])
                set_time(at);sync();step_event=False;block_step_events={}
                for b in all_blocks:
                    # OMC completedIntegratorStep stores discrete pre-values.
                    # Preserve the native UEL when edge until event iteration.
                    if native_uels and b in native_uel_blocks.values() and event:continue
                    signal,terminate=b.f.completedIntegratorStep()
                    if terminate:raise ValueError('native early termination')
                    step_event |= signal
                    block_step_events[b]=bool(signal)
                at_target=abs(at-target)<=1e-12;timed=at_target and target in scheduled;native_timed=any(abs(at-b.next_time)<1e-12 for b in all_blocks)
                root_keys={key for (key,_),hit in zip(guards,roots[native_nz:]) if hit}
                hybrid=event or step_event or timed or native_timed
                if hybrid:
                    events_count+=1
                    if events_count>5000:raise ValueError('native hybrid event budget')
                    record(at,'pre_event')
                    uel_before_commands={gid:b.guard_snapshot() for gid,b in native_uel_blocks.items()}
                    cfg=copy.deepcopy(plant.cfg)
                    if root_keys:event_log.extend(plant.extra_apply(root_keys,at,state,cfg));plant.set_context(cfg)
                    if timed:
                        for e in scheduled[target]:
                            if e.kind in ('fault_apply','fault_clear'):
                                if e.kind=='fault_apply':active_faults.add(e.equipment_id)
                                else:active_faults.remove(e.equipment_id)
                                plant.Y=base_admittance.copy()
                                for fid in sorted(active_faults):
                                    fault=faults[fid];node=plant.nodes[fault.node_id];plant.Y[node,node]+=1/complex(fault.r_ohm,fault.x_ohm)
                                plant.rebuild()
                            elif e.kind=='source_voltage':cfg['scale']=e.value
                            elif e.kind in ('motor_open','motor_close'):cfg['connected'][next(i for i,m in enumerate(plant.s.motors) if m.id==e.equipment_id)]=e.kind=='motor_close'
                            elif e.equipment_id in exciter_ids and e.kind in ('esst1a_port_set','voltage_reference_step'):
                                if e.kind=='esst1a_port_set':controls[e.equipment_id]['ports'][e.port]=e.value
                                else:controls[e.equipment_id]['reference_increment']+=e.value
                            else:plant.handle_event(e,cfg)
                            event_log.append(dict(time_s=float(at),kind=e.kind,equipment_id=e.equipment_id))
                        plant.set_context(cfg)
                    sync();event_log.extend(plant.after_commands(at,state,cfg));plant.set_context(cfg);sync()
                    for b in all_blocks:
                        gid=b.generator_id if hasattr(b,'generator_id') else b.p.generator_id
                        raw_before=b.signals()['raw_lag_state_pu'] if gid in native_uel_blocks and b is native_uel_blocks[gid] else None
                        guard_before=b.guard_snapshot() if raw_before is not None else None
                        reasons=None
                        if raw_before is not None:
                            reasons=uel_event_reasons(uel_event_policy,uel_before_commands[b.p.generator_id]['native_inputs'],guard_before['native_inputs'],
                                own_root=bool(event and any(roots[b.z])),own_time=abs(at-b.next_time)<1e-12,step_request=block_step_events.get(b,False))
                        update=raw_before is None or bool(reasons)
                        if update:b.update()
                        if b in native_pss_blocks.values():sync()
                        if raw_before is not None:
                            if event:
                                post_signal,terminate=b.f.completedIntegratorStep()
                                if terminate:raise ValueError('native UEL post-event termination')
                                if post_signal:
                                    reasons.append('own_post_step_event');b.update();update=True
                            uel_iterations['updated' if update else 'skipped_unaffected']+=1
                            raw_after=b.signals()['raw_lag_state_pu']
                            if abs(raw_before-raw_after)>1e-10:
                                event_log.append(dict(time_s=float(at),kind='native_mnlex2_lag_reset',equipment_id=b.p.generator_id,
                                    raw_state_before_pu=float(raw_before),raw_state_after_pu=float(raw_after),bound='lower' if raw_after==0. else 'upper',
                                    guard_before_commands=uel_before_commands[b.p.generator_id],guard_before_update=guard_before,guard_after_update=b.guard_snapshot(),
                                    event_iteration_reasons=reasons,trigger=dict(native_root=bool(event),integrator_step=bool(step_event),scheduled_command=bool(timed),native_time=bool(native_timed))))
                                if b.root_policy=='continuous_atoms':
                                    indices=[i for i,hit in enumerate(roots[b.z]) if hit] if event else []
                                    event_log[-1].update(root_indicator_indices=indices,root_indicator_names=[b.root_indicator_names[i] for i in indices])
                    record(at,'post_event')
                    del solver;solver=solver_at(at)
                elif at_target:record(at,'regular')
                if at<=cursor and not hybrid:raise ValueError('native coupled time did not advance')
                cursor=at
        del solver
        if abs(cursor-spec.coupled.duration_s)>1e-10:raise ValueError('incomplete native time axis')
        return dict(completed=True,status='completed',time_s=output_times,sample_side=sides,channels=channels,network_balance=network_balance,
            node_voltage={k:dict(real_v=[z.real for z in v],imag_v=[z.imag for z in v]) for k,v in node_voltage.items()},event_log=event_log,
            native_MNLEX2_in_feedback=bool(native_uel_blocks),native_UEL_event_policy=uel_event_policy,native_UEL_root_policy=uel_root_policy,native_UEL_event_iterations=uel_iterations,native_UEL_initialization={gid:dict(native_states=b.native_state_names,event_detection=b.p.event_detection,event_rate_floor_per_s=1e-8 if b.p.event_detection=='conditioned_1e-8' else 64*np.finfo(float).eps,root_policy=b.root_policy,root_indicator_names=b.root_indicator_names,native_FMI_compound_indicators=b.native_nz,initial_residual_per_s=b.initial_residual,native_algebraic_initial_raw_pu=b.initial_raw,independent_SI_inputs=b.initial_inputs,reference=b.identity) for gid,b in native_uel_blocks.items()},
            native_ESST1A_in_feedback=True,native_takeover_OEL_in_feedback=bool(native_oel_blocks),native_PSS_in_feedback=bool(native_pss_blocks),independent_SI_network=True,production_stamps_used=False,production_AVR_RHS_used=False,primary_event_clocks_used=False,
            tolerance=tolerance,reference=identity,study_sha256=canonical_hash(spec),rhs_evaluations=calls,hybrid_batches=events_count,elapsed_s=time.monotonic()-started,
            initialization=plant.initialization,exciter_initialization={b.p.generator_id:dict(native_states=b.native_state_names,initial_residual_per_s=b.initial_residual) for b in blocks},whole_stock_OpenIPSL_network_qualified=False)
    finally:
        for b in [*blocks,*locals().get('native_uel_blocks',{}).values(),*locals().get('native_oel_blocks',{}).values(),*locals().get('native_pss_blocks',{}).values()]:b.close()
