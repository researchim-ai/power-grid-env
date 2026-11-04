"""Dynamic ESST1A comparison against unmodified OpenIPSL FMI Model Exchange.

The native path uses normal Modelica initialization and SUNDIALS CVODE;
production equations enter only the separately integrated comparison path.
"""
from pathlib import Path
from types import SimpleNamespace
import json
import math
import numpy as np
from benchmarks.esst1a_native import NativeExciter,REAL

NAMES=('hold','reference_up','reference_down','oel_takeover','uel_error','uel_field','gate_priority','supplementary_voltage','supplementary_field','regulator_reset','current_limit','rectifier_limit')


def scenario(name):
    from power_grid_env.exciter_esst1a import ESST1APassport
    if name not in NAMES:raise ValueError(name)
    p=ESST1APassport(generator_id='reference');events={};initial=[1.,1.1,0.,4.,-.2,0.,0.,0.,0.]
    # Changes use native input names and are applied atomically per timestamp.
    a={};b={}
    if name=='reference_up':a={'referenceIncrement':.03}
    if name=='reference_down':a={'referenceIncrement':-.03}
    if name=='oel_takeover':a={'oelCeiling':.7};b={'oelCeiling':4.}
    if name=='uel_error':a={'uelErrorFloor':.18};b={'uelErrorFloor':-.2}
    if name=='uel_field':a={'uelFieldFloor':1.8};b={'uelFieldFloor':0.}
    if name=='gate_priority':a={'uelFieldFloor':1.8,'oelCeiling':.7};b={'uelFieldFloor':0.,'oelCeiling':4.}
    if name=='supplementary_voltage':a={'supplementaryVoltage':.03};b={'supplementaryVoltage':0.}
    if name=='supplementary_field':a={'supplementaryField':.3};b={'supplementaryField':0.}
    if name=='regulator_reset':p.V_AMAX=1.2;a={'referenceIncrement':.3};b={'referenceIncrement':-.1}
    if name=='current_limit':p.I_LR=.5;a={'fieldCurrent':1.8};b={'fieldCurrent':.9}
    if name=='rectifier_limit':a={'terminalVoltage':.6};b={'terminalVoltage':1.}
    if a:events[.2]=a
    if b:events[1.]=b
    return p,initial,events


def native(p,initial,events,out,root,tolerance):
    from benchmarks.esst1a_native import INPUTS
    block=NativeExciter(p,1.1,initial[0],initial[1],out,root,2.,tolerance)
    from fmpy.sundials import CVodeSolver
    f=block.f;inputs=list(initial);rows=[];errors=[];transitions=[];t=0.;steps=0
    def set_time(value):f.setTime(float(value))
    def get_x(ptr,n):f.getContinuousStates(ptr,n)
    def set_x(ptr,n):f.setContinuousStates(ptr,n)
    def dx(ptr,n):f.getDerivatives(ptr,n)
    def z(ptr,n):f.getEventIndicators(ptr,n)
    def nominal(ptr,n):f.getNominalsOfContinuousStates(ptr,n)
    class Input:
        def apply(self,t):pass
    class Checked(CVodeSolver):
        def f(self,*args):
            try:return super().f(*args)
            except Exception as e:errors.append(str(e));return -1
        def g(self,*args):
            try:return super().g(*args)
            except Exception as e:errors.append(str(e));return -1
    def solver_at(t):return Checked(block.nx,block.nz,get_x,set_x,dx,z,nominal,set_time,Input(),t,maxStep=.002,relativeTolerance=tolerance,maxNumSteps=20000)
    def record(t,side):rows.append(dict(time_s=float(t),side=side,channels=block.signals()))
    try:
        record(0.,'initial');solver=solver_at(0.)
        for target in sorted(set(np.linspace(0.,2.,201)[1:])|set(events)):
            while t<target-1e-13:
                event,roots,at=solver.step(t,min(target,block.next_time));at=float(at);f.setTime(at)
                if errors:raise ValueError('native callback: '+errors[0])
                step_event,terminate=f.completedIntegratorStep()
                if terminate:raise ValueError('native early termination')
                command=abs(at-target)<1e-12 and target in events
                hybrid=event or step_event or command or abs(at-block.next_time)<1e-12
                if hybrid:
                    before=block.signals()
                    if command:
                        record(at,'pre_event')
                        for key,value in events[target].items():inputs[INPUTS.index(key)]=value
                        block.set_inputs(inputs)
                    block.update();after=block.signals()
                    transitions.append(dict(time_s=at,command=bool(command),raw_before=before['regulator_raw_pu'],raw_after=after['regulator_raw_pu']))
                    if command:record(at,'post_event')
                    del solver;solver=solver_at(at)
                if abs(at-target)<1e-12 and not command:record(at,'regular')
                if at<=t and not hybrid:raise ValueError('native time did not advance')
                t=at;steps+=1
                if steps>20000:raise ValueError('native event/step budget')
        return dict(rows=rows,transitions=transitions,initial_residual=block.initial_residual,native_states=block.native_state_names,reference=block.identity)
    finally:block.close()


def primary(p,initial,events,tolerance):
    from scipy.integrate import solve_ivp
    from power_grid_env.exciter_esst1a import ESST1ABank
    from benchmarks.esst1a_native import INPUTS
    model=SimpleNamespace(generators=[SimpleNamespace(id=p.generator_id)],settings=SimpleNamespace(esst1a_exciters=[p]),underexcitation_limiters=SimpleNamespace(output=lambda state:np.zeros(1)))
    bank=ESST1ABank(model,0);inputs=list(initial)
    bank.measurements=lambda state,voltage=None,current=None:{0:(inputs[0],inputs[1])}
    state=np.zeros(5);error=bank.initialize(state,None,None,np.array([1.1]))
    if error:raise ValueError(error)
    rows=[];log=[];t=0.;count=0
    ports=dict(oelCeiling='oel_ceiling_pu',uelErrorFloor='uel_error_floor_pu',uelFieldFloor='uel_field_floor_pu',uelSum='uel_sum_pu',supplementaryVoltage='supplementary_voltage_pu',supplementaryField='supplementary_field_pu')
    def record(t,side):
        allsignals=bank.signals(state,None,None);prefix=p.generator_id+'.ESST1A.'
        rows.append(dict(time_s=float(t),side=side,channels={k:allsignals[prefix+k] for k in [*REAL,'reference_pu','oel_active','uel2_active','uel3_active','rectifier_upper_active','rectifier_lower_active']}))
    record(0.,'initial')
    for target in sorted(set(np.linspace(0.,2.,201)[1:])|set(events)):
        while t<target-1e-13:
            ids,guards=bank.guards(state)
            sol=solve_ivp(lambda t,x:bank.derivatives(x,None,None),(t,target),state,method='Radau',rtol=tolerance,atol=tolerance*.01,max_step=.002,events=guards)
            if not sol.success:raise ValueError(sol.message)
            state=sol.y[:,-1];t=float(sol.t[-1]);roots={key for key,times in zip(ids,sol.t_events) if len(times)}
            if roots:bank.apply(roots,t,state,log)
            count+=1
            if count>5000:raise ValueError('primary component event budget')
        if target in events:
            record(target,'pre_event')
            for key,value in events[target].items():
                inputs[INPUTS.index(key)]=value
                if key=='referenceIncrement':bank.reference_step(0,value-bank.reference_increment[0])
                if key in ports:bank.ports[0][ports[key]]=value
            bank.refresh(target,state,log);record(target,'post_event')
        else:record(target,'regular')
    return dict(rows=rows,events=log)


def compare(a,b,limit=2e-5):
    if len(a['rows'])!=len(b['rows']):raise ValueError('component sample inventory mismatch')
    errors={k:0. for k in a['rows'][0]['channels']}
    discrete={k for k in errors if k.endswith('_active')}
    for x,y in zip(a['rows'],b['rows']):
        if x['side']!=y['side'] or abs(x['time_s']-y['time_s'])>1e-10 or set(x['channels'])!=set(y['channels']):raise ValueError('component timeline/channel mismatch')
        for k in errors:
            error=abs(x['channels'][k]-y['channels'][k])
            if not math.isfinite(error):raise ValueError('nonfinite comparison')
            errors[k]=max(errors[k],error)
    return dict(passed=all(v<=(0. if k in discrete else limit) for k,v in errors.items()),errors=errors,continuous_limit_pu=limit,discrete_limit=0.,samples=len(a['rows']))


def qualify(out,root,names=NAMES,tolerances=(1e-9,1e-10)):
    from power_grid_env.evidence import package_fingerprint
    from power_grid_env.report import write_json
    from benchmarks.native_online_validation import source_identity
    if not names or len(set(names))!=len(names) or not set(names)<=set(NAMES):raise ValueError('unique registered ESST1A cases required')
    if not tolerances or any(not math.isfinite(t) or not 0<t<=1e-6 for t in tolerances):raise ValueError('finite positive native tolerances required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();source=source_identity();results=[]
    write_json(out/'summary.json',dict(passed=False,status='running'))
    for name in names:
        p,initial,events=scenario(name)
        for tolerance in tolerances:
            directory=out/(name+'-'+str(tolerance));directory.mkdir()
            print(name,tolerance,flush=True)
            a=primary(p,initial,events,tolerance);b=native(p,initial,events,directory/'native',root,tolerance);c=compare(a,b)
            write_json(directory/'primary.json',a);write_json(directory/'reference.json',b);write_json(directory/'comparison.json',c)
            results.append(dict(case=name,tolerance=tolerance,**c))
    if code!=package_fingerprint() or source!=source_identity():raise ValueError('producer changed during qualification')
    result=dict(passed=all(r['passed'] for r in results),runs=results,code_sha256=code,source_sha256=source,network_feedback_qualified=False,industrial_profile_qualified=False)
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--cases',nargs='+',choices=NAMES,default=NAMES);args=parser.parse_args()
    result=qualify(args.out,args.root,args.cases);print(json.dumps(result,indent=2));raise SystemExit(0 if result['passed'] else 1)
