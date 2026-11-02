"""Joint native OEL2C Model Exchange + independently initialized SI winding plant.

SUNDIALS integrates the combined state vector and native event indicators.
Production OEL2C equations, network stamps, states and event clocks are not used.
This is a hybrid reference driver, not a whole stock Dynawo network solver.
"""
from pathlib import Path
import copy,ctypes as C,json,math,sys,time,zipfile,xml.etree.ElementTree as ET
import numpy as np
from benchmarks.dynawo_reference_setup import verify
from benchmarks.oel2c_native_reference import build_model,REAL_VARIABLES
from benchmarks.generator_excitation_si import ExcitationCircuit
from power_grid_env.study_spec import StudySpec
from power_grid_env.model import canonical_hash


class NativeBlock:
    def __init__(self,p,input0,out,root,stop,tolerance,FMU2Model):
        measured=p.KScale*input0;reference=p.KAct*measured
        timer=(p.K2*((measured/p.ITfPu)**p.C2-1)+(p.FixedRu if measured<=p.ITfPu else p.FixedRd))/p.KFb
        if not p.ILimPu<=reference<=p.IInstPu or not p.tMin<=timer<=p.tMax:raise ValueError('native OEL2C initial conditions outside profile')
        self.p=p;out=Path(out).resolve();archive=build_model(p,input0,out,root,mode='me',tolerance=tolerance);folder=out/'unpacked';folder.mkdir()
        with zipfile.ZipFile(archive) as z:
            if any(Path(n).is_absolute() or '..' in Path(n).parts for n in z.namelist()):raise ValueError('unsafe generated FMU')
            z.extractall(folder)
        xml=ET.parse(folder/'modelDescription.xml').getroot();me=xml.find('ModelExchange')
        if me is None or xml.get('fmiVersion')!='2.0':raise ValueError('FMI 2 Model Exchange required')
        self.real={v.get('name'):int(v.get('valueReference')) for v in xml.find('ModelVariables') if v.find('Real') is not None}
        self.boolean={v.get('name'):int(v.get('valueReference')) for v in xml.find('ModelVariables') if v.find('Boolean') is not None}
        self.nx=len(xml.find('ModelStructure/Derivatives'));self.nz=int(xml.get('numberOfEventIndicators'));self.next_time=math.inf
        self.f=FMU2Model(guid=xml.get('guid'),modelIdentifier=me.get('modelIdentifier'),unzipDirectory=str(folder),instanceName=p.generator_id)
        self.f.instantiate();self.f.setupExperiment(startTime=0.,stopTime=stop,tolerance=tolerance);self.f.enterInitializationMode()
        self.f.setReal([self.real['fieldCurrent']],[input0]);self.f.exitInitializationMode();self.update(initial=True)
        self.a=None;self.z=None

    def update(self,initial=False):
        if not initial:self.f.enterEventMode()
        for _ in range(100):
            info=self.f.newDiscreteStates()
            if info[1]:raise ValueError('native OEL2C requested early termination')
            if not info[0]:break
        else:raise ValueError('native OEL2C event iteration budget')
        self.next_time=info[5] if info[4] else math.inf;self.f.enterContinuousTimeMode()

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
    if s.operating_point is not None or not 1<=len(spec.network.generators)<=4 or s.faults or s.duration_s>5.:raise ValueError('cold one-to-four-machine OEL2C reference subset required')
    if any(m.contactor or m.thermal for m in s.motors):raise ValueError('motor contactors/thermal controls outside current native OEL2C master scope')
    if any(e.kind not in ('source_voltage','motor_open','motor_close','generator_open','governor_step','voltage_reference_step') for e in s.events):raise ValueError('unsupported native OEL2C plant event')
    if len({p.generator_id for p in s.oel2c_limiters})!=len(s.oel2c_limiters):raise ValueError('unique native OEL2C targets required')
    if not {p.generator_id for p in s.oel2c_limiters}<={g.id for g in spec.network.generators}:raise ValueError('unknown native OEL2C target')
    if {p.generator_id for p in s.oel2c_limiters}&{p.generator_id for p in s.overexcitation_limiters}:raise ValueError('ambiguous native OEL implementations')
    if any(m.model!='GENROU' or m.exciter is None or m.governor is None or m.stabilizer is not None for m in s.machines):raise ValueError('native GENROU/TGOV1/SEXS subset required')
    if any(p.initialization!='zero_output_startup' for p in s.underexcitation_limiters):raise ValueError('native joint cold-start UEL qualification requires explicit zero_output_startup')
    if len(s.oel2c_limiters)>4:raise ValueError('native OEL2C block budget')
    data=spec.model_dump(mode='json');data['coupled']['oel2c_limiters']=[]
    return StudySpec.model_validate(data)


def run(spec,out,root,tolerance=1e-10):
    if not math.isfinite(tolerance) or not 0<tolerance<=1e-6:raise ValueError('native tolerance required')
    baseline=scope(spec);out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False);identity=verify(root)
    fmpy_path=str(Path(root).resolve()/'fmpy')
    if fmpy_path not in sys.path:sys.path.insert(0,fmpy_path)
    from fmpy.fmi2 import FMU2Model
    from fmpy.sundials import CVodeSolver
    from fmpy.sundials.libraries import sundials_cvode
    import fmpy
    if fmpy.__version__!=identity['fmpy_version'] or not Path(fmpy.__file__).resolve().is_relative_to(Path(fmpy_path)):raise ValueError('wrong FMI master package')
    plant=ExcitationCircuit(baseline);state=plant.state.copy();nplant=len(state);blocks=[];time_s=0.;output_times=[];sides=[];channels={};node_voltage={};event_log=[]
    calls=0;events_count=0;started=time.monotonic();callback_error=[]
    ports={g.g.id:(i,node,g,at) for i,(node,g,at) in enumerate(plant.ports)}
    def reaction(gid):
        i,node,g,at=ports[gid];plant.electrical(state)
        return float(ExcitationCircuit.reaction(g,state[at],plant.generator_currents[i]))
    try:
        nx=nplant;nz=0
        for p in spec.coupled.oel2c_limiters:
            initial=abs(reaction(p.generator_id))/p.rated_field_reaction_pu
            block=NativeBlock(p,initial,out/('block-'+str(len(blocks))),root,spec.coupled.duration_s,tolerance,FMU2Model);block.a=slice(nx,nx+block.nx);block.z=slice(nz,nz+block.nz);nx+=block.nx;nz+=block.nz;blocks.append(block)
        native_nz=nz
        def sync():
            for b in blocks:
                i,node,g,at=ports[b.p.generator_id]
                b.f.setReal([b.real['fieldCurrent']],[abs(reaction(b.p.generator_id))/b.p.rated_field_reaction_pu])
                g.vref=plant.cfg['generator_vref'][i]+b.output()
        def set_time(t):
            nonlocal time_s
            time_s=float(t)
            for b in blocks:b.f.setTime(time_s)
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
            values.update(plant.extra_signals(state))
            for b in blocks:
                prefix=b.p.generator_id+'.OEL2C.';values.update({prefix+k:float(x) for k,x in b.signals(t).items()})
                values[prefix+'field_reaction_pu']=reaction(b.p.generator_id);values[prefix+'field_current_input_pu']=abs(values[prefix+'field_reaction_pu'])/b.p.rated_field_reaction_pu
            if not all(math.isfinite(x) for x in values.values()):raise ValueError('nonfinite native output')
            if channels and set(values)!=set(channels):raise ValueError('native channel inventory drift')
            output_times.append(float(t));sides.append(side)
            for k,value in values.items():channels.setdefault(k,[]).append(float(value))
            for node,index in plant.nodes.items():node_voltage.setdefault(node,[]).append(complex(v[index]))
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
            native_OEL2C_in_feedback=True,native_reference_variant='Dynawo cap guard v1 / FMI2 Model Exchange / CVODE',
            independent_SI_network=True,production_stamps_used=False,production_limiter_RHS_used=False,primary_event_clocks_used=False,
            tolerance=tolerance,reference=identity,study_sha256=canonical_hash(spec),rhs_evaluations=calls,hybrid_batches=events_count,elapsed_s=time.monotonic()-started,
            initialization=plant.initialization,whole_stock_Dynawo_network_qualified=False)
    finally:
        for b in blocks:b.close()
