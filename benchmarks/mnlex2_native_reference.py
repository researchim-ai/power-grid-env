"""Execute original OpenIPSL MNLEX2 with pinned OpenModelica, independent of RMS.

The native input schedule is authored before either solution. This proves the
limiter component, not an original-Modelica closed-loop generator/network.
"""
from pathlib import Path
from types import SimpleNamespace
import argparse,csv,json,math,os,subprocess,time
import numpy as np
from scipy.integrate import solve_ivp
from benchmarks.modelica_reference_setup import verify,environment
from power_grid_env.generator_underexcitation import UnderexcitationLimiter,MinimumExcitationBank
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json,sha256_file

NAMES=('hold','activation_recovery','saturation_recovery','voltage','active_power','fast_feedback','inactive_equilibrium','linear_recovery')
TOLERANCES=(1e-8,1e-10,1e-12)
LIMITS=dict(output_pu=2e-7,raw_lag_state_pu=2e-7,feedback_pu=2e-7,circle_error_pu2=1e-10)


def example(name):
    if name not in NAMES:raise ValueError('unknown registered MNLEX2 component')
    p=UnderexcitationLimiter(generator_id='reference',Q_0=.3,Radius=.4,initialization='zero_output_startup')
    rows=[[0.,.1,.1,1.]]
    if name not in ('hold','inactive_equilibrium'):rows += [[.2,.1,-.6,1.],[.8,.1,.1,1.]]
    if name=='saturation_recovery':rows[1][2]=-1.5
    if name=='voltage':rows[1]=[.2,.1,-.2,.6]
    if name=='active_power':rows[1]=[.2,1.2,.1,1.]
    if name=='fast_feedback':p.K_F2=.23;p.T_F2=.07;p.K_M=.8;p.T_M=.04;p.MEL_MAX=.02
    if name=='inactive_equilibrium':p.initialization='inactive_equilibrium'
    return dict(passport=p.model_dump(mode='json'),schedule=rows,duration_s=2.,step_s=.002,interpolation='linear' if name=='linear_recovery' else 'constant')


def validate_input(case):
    p=UnderexcitationLimiter.model_validate(case['passport']);rows=np.asarray(case['schedule'],float)
    if rows.ndim!=2 or rows.shape[1]!=4 or not 1<=len(rows)<=100 or not np.isfinite(rows).all() or rows[0,0]!=0 or np.any(np.diff(rows[:,0])<=0) or np.any(rows[:,3]<=0):raise ValueError('invalid native input schedule')
    if case.get('interpolation','constant') not in ('constant','linear'):raise ValueError('explicit constant or linear inputs required')
    duration=case['duration_s'];step=case['step_s']
    if not math.isfinite(duration) or not 0<duration<=10 or not 0<step<=duration or duration/step>100000 or rows[-1,0]>=duration:raise ValueError('invalid component duration or sampling')
    if abs(duration/step-round(duration/step))>1e-10:raise ValueError('component horizon must contain an integer number of declared steps')
    return p,rows


def primary_reference(case):
    p,rows=validate_input(case);m=SimpleNamespace(generators=[SimpleNamespace(id=p.generator_id)],settings=SimpleNamespace(underexcitation_limiters=[p]),drive=rows[0,1:])
    drive=lambda x:np.array([np.interp(x[2],rows[:,0],rows[:,k]) for k in (1,2,3)]) if case.get('interpolation')=='linear' else m.drive
    m.electrical=lambda x:(np.array([drive(x)[2]],complex),)
    m.generator_currents=lambda x,v:np.array([complex(drive(x)[0],-drive(x)[1])/drive(x)[2]])
    m.generator_terminal_voltage=lambda x,v:v
    bank=MinimumExcitationBank(m,0);x=np.zeros(3);error=bank.initialize(x)
    if error:raise ValueError(str(error))
    grid=np.linspace(0,case['duration_s'],round(case['duration_s']/case['step_s'])+1)
    segments=[];log=[];started=time.monotonic();attempts=0
    for j,row in enumerate(rows):
        m.drive=row[1:];cursor=row[0];end=rows[j+1,0] if j+1<len(rows) else case['duration_s']
        bank.refresh(cursor,x,log)
        while cursor<end:
            attempts+=1
            if attempts>2000 or time.monotonic()-started>60:raise ValueError('component reference integration budget')
            ids,guards=bank.guards(x)
            sol=solve_ivp(lambda t,y:np.r_[bank.derivatives(y),1.],(cursor,end),x.copy(),method='Radau',rtol=2e-11,atol=1e-12,max_step=.0005,events=guards,dense_output=True)
            if not sol.success or not np.isfinite(sol.y).all():raise ValueError('component primary failed')
            stop=float(sol.t[-1]);segments.append((cursor,stop,sol.sol,row[1:].copy()));x=sol.y[:,-1].copy()
            roots={key for key,hit in zip(ids,sol.t_events) if len(hit)};bank.apply(roots,stop,x,log)
            if stop<=cursor and not roots:raise ValueError('component integration did not advance')
            cursor=stop
    channels={k:[] for k in LIMITS}
    for at in grid:
        choices=[s for s in segments if s[0]<=at<=s[1]]
        if not choices:raise ValueError('component sample outside trajectory')
        _,_,dense,saved_drive=choices[-1];m.drive=saved_drive;x=dense(at);values=bank.signals(x)
        for key in channels:channels[key].append(values[p.generator_id+'.UEL.'+key])
    return dict(completed=True,time_s=grid.tolist(),channels=channels,event_log=log,code_sha256=package_fingerprint())


def native_reference(case,out,root,tolerance):
    if not math.isfinite(tolerance) or not 0<tolerance<=1e-4:raise ValueError('finite positive native tolerance required')
    p,rows=validate_input(case);out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False);root=Path(root).resolve()
    def expression(column):
        value=format(rows[-1,column],'.17g')
        for j in range(len(rows)-2,-1,-1):
            segment=format(rows[j,column],'.17g')
            if case.get('interpolation')=='linear':segment+=f'+(time-{rows[j,0]:.17g})*({rows[j+1,column]-rows[j,column]:.17g})/({rows[j+1,0]-rows[j,0]:.17g})'
            value=f'if time < {rows[j+1,0]:.17g} then ({segment}) else ({value})'
        return value
    params=','.join(k+'='+format(v,'.17g') for k,v in p.model_dump().items() if k not in ('model','generator_id','initialization'))
    if p.initialization=='inactive_equilibrium':
        _,active,reactive,voltage=rows[0]
        params+=f',simpleLagLim(y_start={p.K_M:.17g}*({active:.17g}^2+({reactive:.17g}-{p.Q_0:.17g}*{voltage:.17g}^2)^2-({p.Radius:.17g}*{voltage:.17g}^2)^2))'
    model='model Reference\n  Integer query(start=0,fixed=true);\n  OpenIPSL.Electrical.Controls.PSSE.UEL.MNLEX2 limiter('+params+');\nequation\n'+''.join('  limiter.'+name+' = '+expression(col)+';\n' for name,col in [('PELEC',1),('QELEC',2),('Eterm',3)])+f'  when sample(0,{case["step_s"]:.17g}) then query = pre(query)+1; end when;\nend Reference;\n'
    (out/'Reference.mo').write_text(model)
    msl=root/'libraries/ModelicaStandardLibrary-4.0.0';ipsl=root/'libraries/OpenIPSL-0ee96c3bcf71330916fbaba60a82eeaac5e1ffb5/OpenIPSL/package.mo'
    files=[msl/'Complex.mo',msl/'ModelicaServices/package.mo',msl/'Modelica/package.mo',ipsl,out/'Reference.mo']
    script='setCompiler("gcc");\nsetCXXCompiler("g++");\n'+''.join('loadFile('+json.dumps(str(f))+');\n' for f in files)
    script+=f'simulate(Reference,stopTime={case["duration_s"]:.17g},numberOfIntervals={round(case["duration_s"]/case["step_s"])},tolerance={tolerance:.17g},method="dassl",outputFormat="csv");\ngetErrorString();\n'
    (out/'run.mos').write_text(script)
    with (out/'compiler.log').open('w') as log:subprocess.run([str(root/'runtime/usr/bin/omc'),'run.mos'],cwd=out,env=environment(root),check=True,timeout=90,stdout=log,stderr=subprocess.STDOUT)
    path=out/'Reference_res.csv'
    if not path.is_file() or 'Failed to build' in (out/'compiler.log').read_text():raise ValueError('native Modelica build/simulation failed: '+str(out))
    with path.open() as stream:records=list(csv.DictReader(stream))
    if len(records)<2:raise ValueError('empty native component trace')
    columns=dict(output_pu='limiter.VUEL',raw_lag_state_pu='limiter.simpleLagLim.state',feedback_pu='limiter.derivativeLag.y',circle_error_pu2='limiter.add3_1.y')
    times=np.array([float(r['time']) for r in records]);channels={k:[float(r[v]) for r in records] for k,v in columns.items()}
    if not np.isfinite(times).all() or times[0]!=0 or abs(times[-1]-case['duration_s'])>1e-12 or np.any(np.diff(times)<0) or any(not np.isfinite(v).all() for v in channels.values()):raise ValueError('incomplete/nonfinite native component trace')
    return dict(completed=True,time_s=times.tolist(),channels=channels,tolerance=tolerance,solver='OpenModelica DASSL',original_OpenIPSL_source=True,source_sha256=sha256_file(ipsl.parent/'Electrical/Controls/PSSE/UEL/MNLEX2.mo'))


def compare(primary,native):
    # Pick post-event samples on the exact registered grid. No interpolation of
    # raw reset jumps, and no alignment to primary-computed event times.
    nt=np.asarray(native['time_s']);errors={}
    indices=[]
    for at in primary['time_s']:
        candidates=np.flatnonzero(abs(nt-at)<=1e-12)
        if not len(candidates):raise ValueError('native declared-grid sample missing')
        indices.append(int(candidates[-1]))
    for key,limit in LIMITS.items():
        values=np.asarray(primary['channels'][key]);other=np.asarray(native['channels'][key])[indices]
        if values.shape!=other.shape or not np.isfinite(values).all():raise ValueError('invalid comparison values')
        errors[key]=float(max(abs(values-other)))
    return dict(passed=all(errors[k]<=v for k,v in LIMITS.items()),errors=errors,limits=LIMITS,sampling='post-event values at independent authored query-clock markers; no interpolation or primary event-time alignment')


def run(out,root,names=NAMES):
    if not names or len(names)!=len(set(names)) or any(n not in NAMES for n in names):raise ValueError('unique registered component names required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);identity=verify(root);code=package_fingerprint()
    from benchmarks.native_online_validation import source_identity
    sources=source_identity();cases={name:example(name) for name in names}
    write_json(out/'protocol.json',dict(cases=cases,tolerances=list(TOLERANCES),limits=LIMITS,reference=identity,code_sha256=code,source_sha256=sources,scope='original Modelica limiter component; not native closed-loop network',native_before_primary=True,query_markers='independent authored export clock; observation counter has no feedback to limiter'))
    write_json(out/'summary.json',dict(passed=False,status='running'));checks=[]
    for name,case in cases.items():
        folder=out/name;folder.mkdir();print(name,flush=True)
        natives=[]
        for j,tolerance in enumerate(TOLERANCES):
            native=native_reference(case,folder/('native-'+str(j)),root,tolerance);write_json(folder/('native-'+str(j)+'.json'),native);natives.append(native)
        primary=primary_reference(case);write_json(folder/'primary.json',primary)
        comparisons=[compare(primary,native) for native in natives]
        errors=[max(c['errors'].values()) for c in comparisons]
        converged=all(comparisons[-1]['errors'][k]<=comparisons[0]['errors'][k] or comparisons[-1]['errors'][k]<1e-9 for k in LIMITS)
        result=dict(passed=comparisons[-1]['passed'] and converged,steps=comparisons,convergence_passed=converged)
        write_json(folder/'comparison.json',result)
        if not result['passed']:raise ValueError('native MNLEX2 comparison failed: '+name)
        checks.append(result)
    assert code==package_fingerprint() and sources==source_identity(),'producer changed during native corpus'
    assert identity==verify(root),'native sources changed during corpus'
    result=dict(passed=True,cases=len(checks),native_runs=len(checks)*len(TOLERANCES),maximum_fine_errors={k:max(c['steps'][-1]['errors'][k] for c in checks) for k in LIMITS},code_sha256=code,reference=identity,native_network_feedback_qualified=False,
                artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in out.rglob('*') if p.is_file() and p!=out/'summary.json'})
    write_json(out/'summary.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,default=os.environ.get('PGE_OPENMODELICA_ROOT'));p.add_argument('--cases',nargs='+',choices=NAMES,default=list(NAMES));a=p.parse_args()
    if a.root is None:p.error('--root or PGE_OPENMODELICA_ROOT required')
    print(json.dumps(run(a.out,a.root,tuple(a.cases)),indent=2))
