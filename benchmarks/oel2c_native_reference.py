"""Pinned Dynawo-derived cap-guard-v1 OEL2C: DASSL component and Model Exchange export."""
from pathlib import Path
import argparse,csv,json,math,copy,subprocess,time
import numpy as np
from scipy.integrate import solve_ivp
from benchmarks.dynawo_reference_setup import verify,library_paths,environment
from power_grid_env.generator_oel2c import OEL2CPassport,OEL2CDynamics,CHANNELS
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint

NAMES=('hold','overload_recovery','inverse_ramp','timer_upper_bound','reference_upper_reset','zero_enable_delay','pid_antiwindup','linear_current','constant_overload')
TOLERANCES=(1e-8,1e-10,1e-12)
LIMITS={k:(1e-6 if k in ('timer_s','timer_error_s','enable_timer_start_s','reset_timer_start_s') else 2e-6) for k in CHANNELS}
for k in ('enable_timer_active','enable_timer_expired','reset_timer_active','reset_timer_expired'):LIMITS[k]=0.
REAL_VARIABLES=dict(measured_current_pu='firstOrder.y',timer_s='oelReferenceCurrent.limIntegrator.y',reference_current_pu='oelReferenceCurrent.limIntegrator1.y',
 filtered_reference_pu='firstOrder1.y',pid_integral_pu='pid.I.y',pid_derivative_memory_pu='pid.D.x',leadlag2_memory_pu='limitedLeadLag.firstOrder.y',leadlag1_memory_pu='limitedLeadLag1.firstOrder.y',
 actual_current_pu='gain4.y',timer_error_s='oelReferenceCurrent.tErr',inverse_time_pu='oelReferenceCurrent.limiter.y',timer_target_pu='oelReferenceCurrent.add.y',reference_rate_pu_s='oelReferenceCurrent.multiSwitch1.y',
 bias_current_pu='oelActivation.IBiasPu',regulator_error_pu='add3.y',pid_raw_pu='pid.gainPID.y',pid_output_pu='pid.y',leadlag2_output_pu='limitedLeadLag.y',output_pu='UOelPu')


def example(name):
    if name not in NAMES:raise ValueError('unknown OEL2C native case')
    p=OEL2CPassport(generator_id='reference',rated_field_reaction_pu=1.);rows=[[0.,.8],[.2,2.],[1.2,.8]];duration=5.
    if name=='hold':rows=[[0.,.8]];duration=.5
    if name=='constant_overload':rows=[[0.,1.2]];duration=1.5
    if name=='inverse_ramp':p.Sw1=True
    if name=='timer_upper_bound':rows[1][1]=4.
    if name=='reference_upper_reset':rows=[[0.,.8]];duration=3.4
    if name=='zero_enable_delay':p.tEn=0.
    if name=='pid_antiwindup':p.KpOel=2.;p.KiOel=8.;p.KdOel=.04;p.VOel3MinPu=-.1
    return dict(passport=p.model_dump(mode='json'),schedule=rows,duration_s=duration,step_s=.002,interpolation='linear' if name=='linear_current' else 'constant')


def validate(case):
    p=OEL2CPassport.model_validate(case['passport']);rows=np.asarray(case['schedule'],float);duration=case['duration_s'];step=case['step_s']
    if rows.ndim!=2 or rows.shape[1]!=2 or not 1<=len(rows)<=100 or not np.isfinite(rows).all() or rows[0,0]!=0 or np.any(np.diff(rows[:,0])<=0) or np.any(rows[:,1]<0):raise ValueError('invalid current schedule')
    if not math.isfinite(duration) or not 0<duration<=10 or not 0<step<=duration or duration/step>20000 or rows[-1,0]>=duration or abs(duration/step-round(duration/step))>1e-10:raise ValueError('invalid declared clock')
    if case.get('interpolation','constant') not in ('constant','linear'):raise ValueError('unsupported input interpolation')
    return p,rows


def initialization_modifiers():
    return ','.join(['firstOrder(initType=Modelica.Blocks.Types.Init.InitialOutput)','firstOrder1(initType=Modelica.Blocks.Types.Init.InitialOutput)',
        'limitedLeadLag(firstOrder(initType=Modelica.Blocks.Types.Init.InitialOutput))','limitedLeadLag1(firstOrder(initType=Modelica.Blocks.Types.Init.InitialOutput))',
        'oelReferenceCurrent(limIntegrator(initType=Modelica.Blocks.Types.Init.InitialOutput),limIntegrator1(initType=Modelica.Blocks.Types.Init.InitialOutput))',
        'pid(initType=Modelica.Blocks.Types.InitPID.InitialState)'])


def build_model(p,input0,out,root,*,case=None,tolerance=1e-10,mode='dassl',variant='guarded'):
    if variant not in ('guarded','original'):raise ValueError('registered native source variant required')
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False);paths=library_paths(root,variant);name='Reference'
    params=','.join(k+'='+('true' if v else 'false') if isinstance(v,bool) else k+'='+format(v,'.17g') for k,v in p.native_parameters().items())
    params+=',Input0Pu='+format(input0,'.17g')+(','+'oelActivation(ICapTolPu='+format(p.cap_tolerance_pu,'.17g')+')' if variant=='guarded' else '')+','+initialization_modifiers()
    if mode=='me':declaration=f'input Real fieldCurrent(start={input0:.17g});';drive='fieldCurrent';observer='';duration=10.;step=.002
    else:
        _,rows=validate(case);duration=case['duration_s'];step=case['step_s'];declaration='Integer query(start=0,fixed=true);';drive=format(rows[-1,1],'.17g')
        for j in range(len(rows)-2,-1,-1):
            segment=format(rows[j,1],'.17g')
            if case['interpolation']=='linear':segment+=f'+(time-{rows[j,0]:.17g})*({rows[j+1,1]-rows[j,1]:.17g})/({rows[j+1,0]-rows[j,0]:.17g})'
            drive=f'if time < {rows[j+1,0]:.17g} then ({segment}) else ({drive})'
        observer=f'when sample(0,{step:.17g}) then query=pre(query)+1; end when;'
    observables='\n'.join('  output Real out_'+key+' = limiter.'+var+';' for key,var in REAL_VARIABLES.items())
    observables+='\n'+'\n'.join('  output '+kind+' out_'+label+'_timer_'+suffix+' = limiter.oelActivation.'+source+'.'+native+';' for label,source in [('enable','timer'),('reset','timer1')] for kind,suffix,native in [('Real','age','y'),('Boolean','active','u')])
    model=f'model {name}\n  {declaration}\n{observables}\n  Dynawo.Electrical.Controls.Machines.OverExcitationLimiters.Standard.Oel2c limiter({params});\nequation\n  limiter.inputPu={drive};\n  {observer}\n  annotation(experiment(StartTime=0,StopTime={duration:.17g},Tolerance={tolerance:.17g},Interval={step:.17g}));\nend {name};\n'
    (out/'Reference.mo').write_text(model)
    modelica_path=str(paths[0].parent)+':'+str(paths[-1].parent.parent)
    script='setCompiler("gcc");\nsetCXXCompiler("g++");\nsetModelicaPath('+json.dumps(modelica_path)+');\n'
    script+=''.join('loadFile('+json.dumps(str(f))+');\n' for f in [*paths,out/'Reference.mo'])
    script+=('buildModelFMU(Reference,version="2.0",fmuType="me");\n' if mode=='me' else f'simulate(Reference,stopTime={duration:.17g},numberOfIntervals={round(duration/step)},tolerance={tolerance:.17g},method="dassl",outputFormat="csv");\n')+'getErrorString();\n'
    (out/'run.mos').write_text(script);env=environment(root);env['OPENMODELICALIBRARY']=modelica_path
    with (out/'compiler.log').open('w') as log:subprocess.run([str(Path(root).resolve()/'runtime/usr/bin/omc'),'run.mos'],cwd=out,env=env,check=True,timeout=120,stdout=log,stderr=subprocess.STDOUT)
    target=out/('Reference.fmu' if mode=='me' else 'Reference_res.csv')
    if not target.is_file():raise ValueError('native OEL2C compilation failed: '+str(out))
    return target


def primary(case):
    p,rows=validate(case);k=OEL2CDynamics(p);x=k.initial(rows[0,1]);segments=[];events=[];attempts=0;started=time.monotonic()
    for j,row in enumerate(rows):
        cursor=row[0];end=rows[j+1,0] if j+1<len(rows) else case['duration_s'];k.apply(set(),cursor,x,events)
        drive=lambda t,row=row:float(np.interp(t,rows[:,0],rows[:,1])) if case['interpolation']=='linear' else row[1]
        while cursor<end:
            attempts+=1
            if attempts>2000 or time.monotonic()-started>120:raise ValueError('OEL2C component budget exceeded')
            guards=k.guards(x);context=copy.deepcopy(k.context)
            s=solve_ivp(lambda t,y:k.rhs(y,drive(t)),(cursor,end),x.copy(),method='Radau',rtol=2e-11,atol=1e-12,max_step=.001,dense_output=True,events=[g for _,g in guards])
            if not s.success or not np.isfinite(s.y).all():raise ValueError('primary OEL2C component failed')
            stop=float(s.t[-1]);segments.append((cursor,stop,s.sol,context));x=s.y[:,-1].copy()
            roots={name for (name,_),hits in zip(guards,s.t_events) if len(hits)};k.apply(roots,stop,x,events)
            if stop<=cursor and not roots:raise ValueError('OEL2C component clock did not advance')
            cursor=stop
    grid=np.linspace(0,case['duration_s'],round(case['duration_s']/case['step_s'])+1);channels={n:[] for n in CHANNELS}
    for at in grid:
        choices=[s for s in segments if s[0]<=at<=s[1]]
        if not choices:raise ValueError('primary sample outside trajectory')
        _,_,dense,k.context=choices[-1];values=k.signals(dense(at))
        for name in channels:channels[name].append(values[name])
    return dict(completed=True,time_s=grid.tolist(),channels=channels,event_log=events,code_sha256=package_fingerprint())


def native(case,out,root,tolerance,variant='guarded'):
    p,rows=validate(case);path=build_model(p,rows[0,1],out,root,case=case,tolerance=tolerance,variant=variant)
    with path.open() as f:records=list(csv.DictReader(f))
    channels={name:[float(r['out_'+name]) for r in records] for name,var in REAL_VARIABLES.items()}
    for r in records:
        for label,source,delay in [('enable','timer',p.tEn),('reset','timer1',p.tOff)]:
            active=bool(float(r['out_'+label+'_timer_active']));age=float(r['out_'+label+'_timer_age'])
            values={label+'_timer_active':float(active),label+'_timer_expired':float(active and age>=delay),label+'_timer_start_s':float(r['time'])-age if active else -1.}
            for name,value in values.items():channels.setdefault(name,[]).append(value)
    times=[float(r['time']) for r in records]
    if not times or times[0]!=0 or abs(times[-1]-case['duration_s'])>1e-10 or np.any(np.diff(times)<0) or any(not np.isfinite(v).all() for v in channels.values()):raise ValueError('native OEL2C incomplete/nonfinite trace')
    return dict(completed=True,time_s=times,channels=channels,tolerance=tolerance,original_Dynawo_equations=variant=='original',reference_variant='b932ef89 + cap guard v1 (OelActivation only)' if variant=='guarded' else 'b932ef89 unmodified')


def compare(a,b):
    t=np.asarray(b['time_s']);indices=[]
    for at in a['time_s']:
        ids=np.flatnonzero(t==at)
        if not len(ids):ids=np.flatnonzero(abs(t-at)<1e-12)
        if not len(ids):raise ValueError('native query grid sample missing')
        indices.append(ids[-1])
    errors={name:float(max(abs(np.asarray(a['channels'][name])-np.asarray(b['channels'][name])[indices]))) for name in CHANNELS}
    return dict(passed=all(v<=LIMITS[k] for k,v in errors.items()),errors=errors,limits=LIMITS)


def original_cap_diagnostic(out,root):
    """Record literal-equality sensitivity; this does not qualify the raw variant."""
    out=Path(out);out.mkdir(parents=True,exist_ok=False);case=example('reference_upper_reset')
    write_json(out/'case.json',case);rows=[]
    for j,tol in enumerate(TOLERANCES):
        trace=native(case,out/('native-'+str(j)),root,tol,variant='original')
        write_json(out/('native-'+str(j)+'.json'),trace)
        ch=trace['channels'];mask=np.asarray(trace['time_s'])>=3.
        cap=case['passport']['IInstPu'];bias=case['passport']['IResetPu']
        rows.append(dict(tolerance=tol,maximum_held_cap_error_pu=float(max(abs(np.asarray(ch['reference_current_pu'])[mask]-cap))),
            minimum_bias_pu=float(min(np.asarray(ch['bias_current_pu'])[mask])),maximum_bias_pu=float(max(np.asarray(ch['bias_current_pu'])[mask])),
            reset_expected_pu=bias,literal_equality_missed_reset=bool(min(np.asarray(ch['bias_current_pu'])[mask])<bias/2)))
    result=dict(original_source_unchanged=True,raw_variant_qualified=False,original_condition='IRefPu == IInstPu',
        guarded_condition='IRefPu >= IInstPu - ICapTolPu',cap_tolerance_pu=case['passport']['cap_tolerance_pu'],observations=rows,
        conclusion='Literal floating equality can change reset behavior while reference is held numerically at its cap; guarded v1 is a separately named model, not a claim of unchanged original equations.')
    write_json(out/'diagnostic.json',result);return result


def run(out,root,names=NAMES):
    from benchmarks.native_online_validation import source_identity
    if not names or len(set(names))!=len(names) or any(n not in NAMES for n in names):raise ValueError('registered unique OEL2C cases required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);identity=verify(root);code=package_fingerprint();sources=source_identity()
    write_json(out/'protocol.json',dict(cases={n:example(n) for n in names},tolerances=list(TOLERANCES),limits=LIMITS,reference=identity,code_sha256=code,source_sha256=sources,native_before_primary=True,reference_variant='Dynawo b932ef89 with explicit OelActivation cap guard v1; original exact-equality discrepancy retained separately',initialization='explicit initial-output filters/integrators and initial-state PID; explicit cap guard v1 is the only equation change'))
    original_cap_diagnostic(out/'original-cap-diagnostic',root)
    checks=[];write_json(out/'summary.json',dict(passed=False,status='running'))
    for name in names:
        print(name,flush=True);folder=out/name;folder.mkdir();case=example(name);refs=[]
        for j,tol in enumerate(TOLERANCES):
            value=native(case,folder/('native-'+str(j)),root,tol);write_json(folder/('native-'+str(j)+'.json'),value);refs.append(value)
        a=primary(case);write_json(folder/'production.json',a);rows=[compare(a,b) for b in refs]
        converged=all(rows[-1]['errors'][k]<=rows[0]['errors'][k] or rows[-1]['errors'][k]<1e-8 for k in CHANNELS)
        c=dict(passed=rows[-1]['passed'] and converged,steps=rows,convergence_passed=converged);write_json(folder/'comparison.json',c)
        if not c['passed']:raise ValueError('OEL2C component comparison failed: '+name)
        checks.append(c)
    if code!=package_fingerprint() or sources!=source_identity() or identity!=verify(root):raise ValueError('reference or producer changed')
    result=dict(passed=True,cases=len(checks),native_runs=len(checks)*3,maximum_fine_errors={k:max(c['steps'][-1]['errors'][k] for c in checks) for k in CHANNELS},code_sha256=code,
                artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in out.rglob('*') if p.is_file() and p!=out/'summary.json'})
    write_json(out/'summary.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);p.add_argument('--root',required=True,type=Path);p.add_argument('--cases',nargs='+',choices=NAMES,default=list(NAMES));a=p.parse_args();print(json.dumps(run(a.out,a.root,tuple(a.cases)),indent=2))
