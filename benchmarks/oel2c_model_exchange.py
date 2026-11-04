"""Joint native OEL2C Model Exchange + independently initialized SI winding plant.

SUNDIALS integrates the combined state vector and native event indicators.
Production OEL2C equations, network stamps, states and event clocks are not used.
This is a hybrid reference driver, not a whole stock Dynawo network solver.
"""
from pathlib import Path
import copy,ctypes as C,json,math,sys,time,zipfile,xml.etree.ElementTree as ET
import numpy as np
from benchmarks.dynawo_reference_setup import verify
from benchmarks.oel2c_native_reference import build_model,REAL_VARIABLES,OEL2CPassport
from benchmarks.generator_excitation_si import ExcitationCircuit
from power_grid_env.study_spec import StudySpec
from power_grid_env.model import canonical_hash


class NativeBlock:
    def __init__(self,p,input0,out,root,stop,tolerance,FMU2Model,regulating_output_pu=None):
        measured=p.KScale*input0;reference=p.KAct*measured
        timer=(p.K2*((measured/p.ITfPu)**p.C2-1)+(p.FixedRu if measured<=p.ITfPu else p.FixedRd))/p.KFb
        if p.initialization=='dynawo_startup' and (not p.ILimPu<=reference<=p.IInstPu or not p.tMin<=timer<=p.tMax):raise ValueError('native OEL2C initial conditions outside profile')
        self.p=p;out=Path(out).resolve();archive=build_model(p,input0,out,root,mode='me',tolerance=tolerance,regulating_output_pu=regulating_output_pu);folder=out/'unpacked';folder.mkdir()
        with zipfile.ZipFile(archive) as z:
            if any(Path(n).is_absolute() or '..' in Path(n).parts for n in z.namelist()):raise ValueError('unsafe generated FMU')
            z.extractall(folder)
        xml=ET.parse(folder/'modelDescription.xml').getroot();me=xml.find('ModelExchange')
        if me is None or xml.get('fmiVersion')!='2.0':raise ValueError('FMI 2 Model Exchange required')
        self.real={v.get('name'):int(v.get('valueReference')) for v in xml.find('ModelVariables') if v.find('Real') is not None}
        self.boolean={v.get('name'):int(v.get('valueReference')) for v in xml.find('ModelVariables') if v.find('Boolean') is not None}
        self.nx=len(xml.find('ModelStructure/Derivatives'));self.nz=int(xml.get('numberOfEventIndicators'));self.next_time=math.inf
        self.f=FMU2Model(guid=xml.get('guid'),modelIdentifier=me.get('modelIdentifier'),unzipDirectory=str(folder),instanceName=p.generator_id)
        stationary=None;origin=0.
        if p.initialization!='dynawo_startup':
            from benchmarks.oel2c_equilibrium_reference import initial_conditions
            stationary,origin=initial_conditions(p,input0,regulating_output_pu=regulating_output_pu)
        self.time_offset=-origin
        self.f.instantiate();self.f.setupExperiment(startTime=0.,stopTime=stop+self.time_offset,tolerance=tolerance);self.f.enterInitializationMode()
        self.f.setReal([self.real['fieldCurrent']],[input0]);self.f.exitInitializationMode();self.update(initial=True)
        self.stationary_initial_residual=None;self.boundary_audit=None
        if p.equilibrium_boundary_policy=='reject_sensitive_neighborhood':
            from benchmarks.oel2c_boundary_reference import check_neighborhood
            self.boundary_audit=check_neighborhood(p,input0)
        if stationary is not None:
            variables=list(xml.find('ModelVariables'));names=[]
            for entry in xml.find('ModelStructure/Derivatives'):
                derivative=variables[int(entry.get('index'))-1].find('Real')
                names.append(variables[int(derivative.get('derivative'))-1].get('name'))
            self.native_state_names=names
            if len(names)!=len(set(names)) or set(names)!=set(stationary):raise ValueError('native equilibrium state inventory mismatch')
            # MSL 3.2.3 LimIntegrator holds only strictly outside its cap.
            # One ULP selects the outward held branch without changing a limit.
            reference_key='limiter.oelReferenceCurrent.limIntegrator1.y'
            if stationary[reference_key] in (p.ILimPu,p.IInstPu):
                direction=math.inf if stationary[reference_key]==p.IInstPu else -math.inf
                stationary[reference_key]=np.nextafter(stationary[reference_key],direction)
            key='limiter.oelReferenceCurrent.limIntegrator.y'
            if stationary[key]==p.tMin:stationary[key]=np.nextafter(p.tMin,-math.inf)
            elif stationary[key]==p.tMax:stationary[key]=np.nextafter(p.tMax,math.inf)
            values=np.asarray([stationary[k] for k in names],dtype=float)
            self.f.setTime(self.time_offset);self.f.setContinuousStates(np.ctypeslib.as_ctypes(values),self.nx)
            indicators=np.zeros(self.nz);self.f.getEventIndicators(np.ctypeslib.as_ctypes(indicators),self.nz);self.update()
            rates=np.zeros(self.nx);self.f.getDerivatives(np.ctypeslib.as_ctypes(rates),self.nx)
            self.stationary_initial_residual=float(max(abs(rates)))
            if not np.isfinite(rates).all() or max(abs(rates))>1e-8 or (abs(self.output())>1e-12 if p.initialization=='inactive_equilibrium' else self.output()>1e-12 if p.initialization=='selected_equilibrium' else self.output()>=0.):
                raise ValueError('native stationary equilibrium residual check failed: '+str(dict(rates=rates.tolist(),signals=self.signals(0.))))
        self.a=None;self.z=None

    def update(self,initial=False):
        if not initial:self.f.enterEventMode()
        for _ in range(100):
            info=self.f.newDiscreteStates()
            if info[1]:raise ValueError('native OEL2C requested early termination')
            if not info[0]:break
        else:raise ValueError('native OEL2C event iteration budget')
        self.next_time=info[5]-self.time_offset if info[4] else math.inf;self.f.enterContinuousTimeMode()

    def output(self):return self.f.getReal([self.real['out_output_pu']])[0]

    def signals(self,t):
        result=dict(zip(REAL_VARIABLES,self.f.getReal([self.real['out_'+k] for k in REAL_VARIABLES])))
        for label,delay in [('enable',self.p.tEn),('reset',self.p.tOff)]:
            active=self.f.getBoolean([self.boolean['out_'+label+'_timer_active']])[0];age=self.f.getReal([self.real['out_'+label+'_timer_age']])[0]
            result.update({label+'_timer_active':float(active),label+'_timer_expired':float(active and age>=delay),label+'_timer_start_s':t-age if active else -1.})
        return result

    def close(self):self.f.terminate();self.f.freeInstance()


def scope(spec):
    if spec.study!='coupled_rms' or spec.backend!='scipy' or not spec.coupled or not spec.coupled.oel2c_limiters:raise ValueError('coupled OEL2C profile required')
    s=spec.coupled
    for p in s.oel2c_limiters:OEL2CPassport.model_validate(p.model_dump(mode='json'))
    if s.operating_point is not None and any(p.initialization=='dynawo_startup' for p in s.oel2c_limiters):raise ValueError('loaded native OEL2C requires explicit stationary initialization')
    if s.operating_point is None and any(p.initialization in ('active_limited_equilibrium','current_regulating_equilibrium','selected_equilibrium') for p in s.oel2c_limiters):raise ValueError('native active equilibrium requires a loaded operating point')
    if any(p.initialization!='current_regulating_equilibrium' and (p.initialization in ('active_limited_equilibrium','selected_equilibrium'))!=(p.avr_reference_initialization=='derive_from_operating_point_with_OEL') for p in s.oel2c_limiters):raise ValueError('native explicit AVR reference policy mismatch')
    if not 1<=len(spec.network.generators)<=4 or s.faults or s.duration_s>5.:raise ValueError('one-to-four-machine OEL2C reference subset required')
    if any(m.contactor or m.thermal for m in s.motors):raise ValueError('motor contactors/thermal controls outside current native OEL2C master scope')
    if any(e.kind not in ('source_voltage','motor_open','motor_close','generator_open','governor_step','voltage_reference_step') for e in s.events):raise ValueError('unsupported native OEL2C plant event')
    if len({p.generator_id for p in s.oel2c_limiters})!=len(s.oel2c_limiters):raise ValueError('unique native OEL2C targets required')
    if not {p.generator_id for p in s.oel2c_limiters}<={g.id for g in spec.network.generators}:raise ValueError('unknown native OEL2C target')
    if {p.generator_id for p in s.oel2c_limiters}&{p.generator_id for p in s.overexcitation_limiters}:raise ValueError('ambiguous native OEL implementations')
    if any(m.model!='GENROU' or m.exciter is None or m.governor is None or m.stabilizer is not None for m in s.machines):raise ValueError('native GENROU/TGOV1/SEXS subset required')
    if any(p.initialization!=('inactive_equilibrium' if s.operating_point is not None else 'zero_output_startup') for p in s.underexcitation_limiters):raise ValueError('native joint UEL requires inactive_equilibrium for loaded / zero_output_startup for cold')
    bases={p.generator_id:p.rated_field_reaction_pu for p in s.generator_field_thermal}
    if any(p.generator_id in bases and not math.isclose(p.rated_field_reaction_pu,bases[p.generator_id],rel_tol=1e-12) for p in s.oel2c_limiters):raise ValueError('native field thermal and OEL2C current bases must agree')
    if len(s.oel2c_limiters)>4:raise ValueError('native OEL2C block budget')
    data=spec.model_dump(mode='json');data['coupled']['oel2c_limiters']=[]
    return StudySpec.model_validate(data)


def run(spec,out,root,tolerance=1e-10):
    if not math.isfinite(tolerance) or not 0<tolerance<=1e-6:raise ValueError('native tolerance required')
    baseline=scope(spec);out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False);identity=verify(root)
    if any(p.model=='OEL2C_Dynawo_equilibrium_guard_v2_SEXS_summation' for p in spec.coupled.oel2c_limiters):
        from benchmarks.dynawo_reference_setup import verify_equilibrium_copy,sha,PATCH_FILE
        verify_equilibrium_copy(root);identity['equilibrium_guard_v2_sha256']=sha(Path(root)/'dynawo-equilibrium-guard-v2'/PATCH_FILE)
    fmpy_path=str(Path(root).resolve()/'fmpy')
    if fmpy_path not in sys.path:sys.path.insert(0,fmpy_path)
    from fmpy.fmi2 import FMU2Model
    from fmpy.sundials import CVodeSolver
    from fmpy.sundials.libraries import sundials_cvode
    import fmpy
    if fmpy.__version__!=identity['fmpy_version'] or not Path(fmpy.__file__).resolve().is_relative_to(Path(fmpy_path)):raise ValueError('wrong FMI master package')
    constraints={p.generator_id:p for p in spec.coupled.oel2c_limiters if p.initialization=='current_regulating_equilibrium'}
    plant=ExcitationCircuit(baseline,maximum_duration_s=5.,current_constraints=constraints);state=plant.state.copy();nplant=len(state);blocks=[];time_s=0.;output_times=[];sides=[];channels={};node_voltage={};event_log=[]
    calls=0;events_count=0;started=time.monotonic();callback_error=[]
    ports={g.g.id:(i,node,g,at) for i,(node,g,at) in enumerate(plant.ports)}
    def reaction(gid):
        i,node,g,at=ports[gid];plant.electrical(state)
        return float(ExcitationCircuit.reaction(g,state[at],plant.generator_currents[i]))
    try:
        nx=nplant;nz=0
        for p in spec.coupled.oel2c_limiters:
            initial=abs(reaction(p.generator_id))/p.rated_field_reaction_pu
            desired=plant.cfg['generator_vref'][ports[p.generator_id][0]]-p.specified_avr_reference_pu if p.initialization=='current_regulating_equilibrium' else None
            block=NativeBlock(p,initial,out/('block-'+str(len(blocks))),root,spec.coupled.duration_s,tolerance,FMU2Model,desired);block.a=slice(nx,nx+block.nx);block.z=slice(nz,nz+block.nz);nx+=block.nx;nz+=block.nz;blocks.append(block)
        native_nz=nz;reference_audit={}
        for b in blocks:
            if b.p.initialization not in ('active_limited_equilibrium','current_regulating_equilibrium','selected_equilibrium'):continue
            i,node,g,at=ports[b.p.generator_id];original=float(plant.cfg['generator_vref'][i]);bias=b.output()
            chosen=b.p.specified_avr_reference_pu if b.p.initialization=='current_regulating_equilibrium' else original-bias
            plant.cfg['generator_vref'][i]=chosen
            reference_audit[b.p.generator_id]=dict(baseline_reference_pu=original,limiter_output_pu=bias,derived_reference_pu=chosen)
        def sync():
            for b in blocks:
                i,node,g,at=ports[b.p.generator_id]
                b.f.setReal([b.real['fieldCurrent']],[abs(reaction(b.p.generator_id))/b.p.rated_field_reaction_pu])
                g.vref=plant.cfg['generator_vref'][i]+b.output()
        def set_time(t):
            nonlocal time_s
            time_s=float(t)
            for b in blocks:b.f.setTime(time_s+b.time_offset)
        def get_x(ptr,n):
            arr=np.ctypeslib.as_array(ptr,(n,));arr[:nplant]=state
            for b in blocks:b.f.getContinuousStates(np.ctypeslib.as_ctypes(arr[b.a]),b.nx)
        def set_x(ptr,n):
            nonlocal state
            arr=np.ctypeslib.as_array(ptr,(n,));state=arr[:nplant].copy()
            for b in blocks:b.f.setContinuousStates(np.ctypeslib.as_ctypes(arr[b.a]),b.nx)
        def get_dx(ptr,n):
            nonlocal calls
            calls+=1
            if calls>2_000_000 or time.monotonic()-started>180:raise ValueError('native coupled work budget')
            sync();arr=np.ctypeslib.as_array(ptr,(n,));arr[:nplant]=plant.rhs(time_s,state)
            for b in blocks:b.f.getDerivatives(np.ctypeslib.as_ctypes(arr[b.a]),b.nx)
            if not np.isfinite(arr).all():raise ValueError('nonfinite native coupled RHS')
        def nominals(ptr,n):
            arr=np.ctypeslib.as_array(ptr,(n,));arr[:nplant]=np.maximum(1.,abs(plant.state))
            for b in blocks:b.f.getNominalsOfContinuousStates(np.ctypeslib.as_ctypes(arr[b.a]),b.nx)
        guards=[]
        def get_z(ptr,n):
            sync();arr=np.ctypeslib.as_array(ptr,(n,))
            for b in blocks:b.f.getEventIndicators(np.ctypeslib.as_ctypes(arr[b.z]),b.nz)
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
            sync();guards=plant.extra_guards(state)
            solver=Checked(nx,native_nz+len(guards),get_x,set_x,get_dx,get_z,nominals,set_time,Input(),t,maxStep=min(.002,spec.coupled.step_s),relativeTolerance=tolerance,maxNumSteps=20000)
            direction=(C.c_int*(native_nz+len(guards)))(*([0]*native_nz+[int(g.direction) for _,g in guards]))
            function=sundials_cvode.CVodeSetRootDirection;function.argtypes=[C.c_void_p,C.POINTER(C.c_int)];function.restype=C.c_int
            if function(solver.cvode_mem,direction)!=0:raise ValueError('native root-direction setup failed')
            return solver
        def record(t,side):
            set_time(t);sync();v,st,rot,torque=plant.electrical(state);values={}
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
                prefix=b.p.generator_id+'.OEL2C.';values.update({prefix+k:float(x) for k,x in b.signals(t).items()})
                values[prefix+'field_reaction_pu']=reaction(b.p.generator_id);values[prefix+'field_current_input_pu']=abs(values[prefix+'field_reaction_pu'])/b.p.rated_field_reaction_pu
            if not all(math.isfinite(x) for x in values.values()):raise ValueError('nonfinite native output')
            if channels and set(values)!=set(channels):raise ValueError('native channel inventory drift')
            output_times.append(float(t));sides.append(side)
            for k,value in values.items():channels.setdefault(k,[]).append(float(value))
            for node,index in plant.nodes.items():node_voltage.setdefault(node,[]).append(complex(v[index]))
        conditional_spectrum=None
        if constraints:
            set_time(0.);initial=np.zeros(nx);get_x(np.ctypeslib.as_ctypes(initial),nx)
            selected=[]
            for i,p in enumerate(plant.p):
                if plant.cfg['connected'][i]:selected.extend(range(p['state'].start,p['state'].stop));selected.append(p['speed'])
            for node,g,at in plant.ports:selected.extend(range(at.start,at.stop))
            for b in blocks:
                if b.p.initialization!='current_regulating_equilibrium':continue
                values=b.signals(0.)
                for j,name in enumerate(b.native_state_names):
                    if name=='limiter.oelReferenceCurrent.limIntegrator1.y':continue
                    if name=='limiter.oelReferenceCurrent.limIntegrator.y' and (values['timer_s']<=b.p.tMin or values['timer_s']>=b.p.tMax):continue
                    selected.append(b.a.start+j)
            if len(selected)!=len(set(selected)):raise ValueError('native conditional state inventory duplicated')
            matrix=np.empty((len(selected),len(selected)))
            def regions():
                result=[]
                for b in blocks:
                    if b.p.initialization!='current_regulating_equilibrium':continue
                    q=b.signals(0.);p=b.p
                    raw=[q['pid_raw_pu'],p.tC2Oel/p.tB2Oel*(q['pid_output_pu']-q['leadlag2_memory_pu']),p.tC1Oel/p.tB1Oel*(q['leadlag2_output_pu']-q['leadlag1_memory_pu'])]
                    result.extend((v<=lo,v>=0.) for v,lo in zip(raw,[p.VOel3MinPu,p.VOel2MinPu,p.VOel1MinPu]))
                return tuple(result)
            original_regions=regions()
            def evaluate(vector):
                set_x(np.ctypeslib.as_ctypes(vector),nx);derivative=np.zeros(nx);get_dx(np.ctypeslib.as_ctypes(derivative),nx)
                return derivative[selected],regions()
            for j,k in enumerate(selected):
                step=1e-7*max(1e-3,abs(initial[k]))
                for node,g,at in plant.ports:
                    for coordinate,lo,hi in [(at.start+2,g.p.governor.VMIN,g.p.governor.VMAX),(at.start+5,g.p.exciter.EMIN,g.p.exciter.EMAX)]:
                        if k==coordinate:step=min(step,.2*min(initial[k]-lo,hi-initial[k]))
                for _ in range(30):
                    plus=initial.copy();minus=initial.copy();plus[k]+=step;minus[k]-=step
                    if step<=0 or plus[k]==minus[k]:raise ValueError('native conditional difference not representable')
                    fp,rp=evaluate(plus);fm,rm=evaluate(minus)
                    if rp==rm==original_regions:break
                    step*=.5
                else:raise ValueError('native conditional difference crosses a clipping boundary')
                matrix[:,j]=(fp-fm)/(2*step)
            set_x(np.ctypeslib.as_ctypes(initial),nx);sync()
            for b in blocks:b.update()
            eigen=np.linalg.eigvals(matrix)
            if not np.isfinite(eigen).all():raise ValueError('nonfinite native conditional spectrum')
            conditional_spectrum=dict(eigenvalues_per_s=[[float(v.real),float(v.imag)] for v in eigen],states=len(selected),relative_difference_step=1e-7,
                scope='hot branch, constrained reference and bound-held timer excluded; not full hybrid stability')
        scheduled={}
        for e in spec.coupled.events:scheduled.setdefault(e.time_s,[]).append(e)
        grid=np.r_[np.arange(math.ceil(spec.coupled.duration_s/spec.coupled.step_s))*spec.coupled.step_s,spec.coupled.duration_s]
        targets=sorted(set([float(t) for t in grid if t>0]+list(scheduled)));cursor=0.;record(0.,'initial');solver=solver_at(0.)
        for target in targets:
            while cursor<target-1e-13:
                next_time=min([target,*[b.next_time for b in blocks]])
                event,roots,at=solver.step(cursor,next_time)
                if callback_error:raise ValueError('native callback: '+callback_error[0])
                set_time(at);sync();step_event=False
                for b in blocks:
                    signal,terminate=b.f.completedIntegratorStep()
                    if terminate:raise ValueError('native early termination')
                    step_event |= signal
                at_target=abs(at-target)<=1e-12;timed=at_target and target in scheduled;native_timed=any(abs(at-b.next_time)<1e-12 for b in blocks)
                root_keys={key for (key,_),hit in zip(guards,roots[native_nz:]) if hit}
                hybrid=event or step_event or timed or native_timed
                if hybrid:
                    events_count+=1
                    if events_count>5000:raise ValueError('native hybrid event budget')
                    record(at,'pre_event')
                    cfg=copy.deepcopy(plant.cfg)
                    if root_keys:event_log.extend(plant.extra_apply(root_keys,at,state,cfg));plant.set_context(cfg)
                    if timed:
                        for e in scheduled[target]:
                            if e.kind=='source_voltage':cfg['scale']=e.value
                            elif e.kind in ('motor_open','motor_close'):cfg['connected'][next(i for i,m in enumerate(plant.s.motors) if m.id==e.equipment_id)]=e.kind=='motor_close'
                            else:plant.handle_event(e,cfg)
                            event_log.append(dict(time_s=float(at),kind=e.kind,equipment_id=e.equipment_id))
                        plant.set_context(cfg)
                    sync();event_log.extend(plant.after_commands(at,state,cfg));plant.set_context(cfg);sync()
                    for b in blocks:b.update()
                    record(at,'post_event')
                    del solver;solver=solver_at(at)
                elif at_target:record(at,'regular')
                if at<=cursor and not hybrid:raise ValueError('native coupled time did not advance')
                cursor=at
        del solver
        if abs(cursor-spec.coupled.duration_s)>1e-10:raise ValueError('incomplete native time axis')
        return dict(completed=True,status='completed',time_s=output_times,sample_side=sides,channels=channels,
            node_voltage={k:dict(real_v=[z.real for z in v],imag_v=[z.imag for z in v]) for k,v in node_voltage.items()},event_log=event_log,
            native_OEL2C_in_feedback=True,native_reference_variant=('Dynawo equilibrium guard v2 / FMI2 Model Exchange / CVODE' if any(p.model=='OEL2C_Dynawo_equilibrium_guard_v2_SEXS_summation' for p in spec.coupled.oel2c_limiters) else 'Dynawo cap guard v1 / FMI2 Model Exchange / CVODE'),
            independent_SI_network=True,production_stamps_used=False,production_limiter_RHS_used=False,primary_event_clocks_used=False,
            tolerance=tolerance,reference=identity,study_sha256=canonical_hash(spec),rhs_evaluations=calls,hybrid_batches=events_count,elapsed_s=time.monotonic()-started,
            initialization=plant.initialization,conditional_regulating_spectrum=conditional_spectrum,AVR_reference_initialization=reference_audit,limiter_initialization={b.p.generator_id:dict(mode=b.p.initialization,model=b.p.model,boundary_neighborhood=b.boundary_audit,native_time_offset_s=b.time_offset,initial_continuous_residual_per_s=b.stationary_initial_residual,held_cap_rounding='one outward ULP on held native LimIntegrator states' if b.p.initialization!='dynawo_startup' else None) for b in blocks},whole_stock_Dynawo_network_qualified=False)
    finally:
        for b in blocks:b.close()
