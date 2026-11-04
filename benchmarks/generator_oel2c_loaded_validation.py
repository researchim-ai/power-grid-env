"""Preregistered stationary OEL2C feedback and reference-policy qualification."""
from pathlib import Path
import argparse,json
from benchmarks.generator_oel2c_loaded_cases import example,NAMES
from benchmarks.generator_oel2c_validation import native_reference,compare,TOLERANCES
from benchmarks.native_online_validation import source_identity
from benchmarks.dynawo_reference_setup import verify
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_spec import run_study,StudySpec
from power_grid_env.model import canonical_hash
from power_grid_env.report import write_json,sha256_file

NEGATIVE=('startup_loaded','thermal_overload','instantaneous_overload','zero_ramp','inverse_zero_ramp','thermal_timer_hot','thermal_timer_boundary','unknown','duplicate')


def negative(name):
    s=example('hold').model_dump(mode='json');p=s['coupled']['oel2c_limiters'][0]
    if name=='startup_loaded':p['initialization']='dynawo_startup'
    elif name=='thermal_overload':p['rated_field_reaction_pu']=.5
    elif name=='instantaneous_overload':p['IInstPu']=.65
    elif name=='zero_ramp':p['Kru']=0.
    elif name=='inverse_zero_ramp':p['Sw1']=True;p['K1']=0.
    elif name=='thermal_timer_hot':p['VInvMinPu']=1.
    elif name=='thermal_timer_boundary':p['tMin']=.1
    elif name=='unknown':p['generator_id']='missing'
    elif name=='duplicate':s['coupled']['oel2c_limiters']*=2
    else:raise ValueError('unknown negative loaded OEL2C input')
    return s


def compare_initial_references(spec,primary,native):
    active={p.generator_id:p for p in spec.coupled.oel2c_limiters if p.initialization in ('active_limited_equilibrium','current_regulating_equilibrium')}
    left=primary['operating_point']['oel2c_AVR_reference_initialization'];right=native['AVR_reference_initialization']
    if set(left)!=set(active) or set(right)!=set(active):raise ValueError('initial AVR reference audit inventory mismatch')
    rows={}
    for gid,p in active.items():
        a,b=left[gid],right[gid]
        if p.initialization=='current_regulating_equilibrium':
            if a['policy']!='specified_for_current_regulation' or a['PID_authority_exhausted'] or abs(a['current_excess_pu'])>1e-9 or a['derived_reference_pu']!=p.specified_avr_reference_pu:raise ValueError('specified regulating reference audit inconsistent')
        elif a['policy']!='derive_from_operating_point_with_OEL' or not a['PID_authority_exhausted'] or a['current_excess_pu']<=0:raise ValueError('active constraint audit missing or inconsistent')
        if abs(a['derived_reference_pu']-a['baseline_reference_pu']+a['limiter_output_pu'])>1e-12:raise ValueError('primary reference policy identity violated')
        error=max(abs(a[k]-b[k]) for k in ['baseline_reference_pu','limiter_output_pu','derived_reference_pu'])
        if error>1e-8:raise ValueError('independent AVR initial references differ')
        ch=primary['channels']
        if abs(ch[gid+'.SEXS.reference_pu'][0]-a['derived_reference_pu'])>1e-12 or abs(ch[gid+'.OEL2C.output_pu'][0]-a['limiter_output_pu'])>1e-12:raise ValueError('reference audit does not describe actual initial state')
        if p.initialization=='current_regulating_equilibrium':
            if abs(ch[gid+'.OEL2C.regulator_error_pu'][0])>1e-9 or not p.VOel3MinPu+1e-8<ch[gid+'.OEL2C.pid_output_pu'][0]<-1e-8:raise ValueError('interior current regulation state missing')
        elif abs(ch[gid+'.OEL2C.pid_output_pu'][0]-p.VOel3MinPu)>1e-12 or ch[gid+'.OEL2C.regulator_error_pu'][0]>=0:raise ValueError('active PID authority state missing')
        rows[gid]=dict(reference_difference_pu=error,initial_control_current_excess_pu=a['current_excess_pu'])
    return dict(passed=True,generators=rows,current_limit_achievement_claimed=False)


def compare_conditional_spectra(primary,native):
    from scipy.optimize import linear_sum_assignment
    import numpy as np
    a=np.asarray(primary['operating_point']['conditional_regulating_eigenvalues_per_s']);report=native.get('conditional_regulating_spectrum')
    if report is None:raise ValueError('native conditional spectrum missing')
    b=np.asarray(report['eigenvalues_per_s'])
    if a.ndim!=2 or a.shape!=b.shape or a.shape[1]!=2 or not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('conditional spectral inventory mismatch')
    x=a[:,0]+1j*a[:,1];y=b[:,0]+1j*b[:,1]
    error=abs(x[:,None]-y[None,:])/(1+abs(y[None,:]));i,j=linear_sum_assignment(error);maximum=float(max(error[i,j]))
    passed=bool(maximum<=1e-4 and max(x.real)<-1e-7 and max(y.real)<-1e-7)
    return dict(passed=passed,states=len(x),maximum_normalized_eigenvalue_error=maximum,limit=1e-4,
        primary_spectral_abscissa_per_s=float(max(x.real)),native_spectral_abscissa_per_s=float(max(y.real)),scope='conditional hot branch; not general hybrid stability')


def run(out,root,names=NAMES,*,case_factory=example,negative_factory=negative,registered_names=NAMES,negative_names=NEGATIVE,profile='inactive_equilibrium'):
    if profile not in ('inactive_equilibrium','active_limited_equilibrium_with_explicit_AVR_reference_policy','current_regulating_equilibrium_with_specified_AVR'):raise ValueError('registered stationary profile required')
    if not names or len(set(names))!=len(names) or any(n not in registered_names for n in names):raise ValueError('unique registered loaded cases required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();sources=source_identity();identity=verify(root)
    if profile.startswith('current_regulating'):
        from benchmarks.dynawo_reference_setup import verify_equilibrium_copy,sha,PATCH_FILE
        verify_equilibrium_copy(root);identity['equilibrium_guard_v2_sha256']=sha(Path(root)/'dynawo-equilibrium-guard-v2'/PATCH_FILE)
    write_json(out/'protocol.json',dict(code_sha256=code,source_sha256=sources,reference=identity,cases={n:canonical_hash(case_factory(n)) for n in names},negative={n:negative_factory(n) for n in negative_names},profile=profile,
        tolerances=TOLERANCES,reference_before_primary=True,continuous_initial_residual_limit_per_s=1e-8,initial_reference_difference_limit_pu=1e-8,reference_policy_identity_limit_pu=1e-12,current_limit_achievement_claimed=False,conditional_spectral_normalized_error_limit=1e-4,
        clock_initialization='inactive: +tOff+1 s; active limited: +tEn+1 s; current regulating: zero epoch with inactive clocks; independent FMU states, no production state transfer',
        held_native_integrators='one outward ULP selects strict MSL 3.2.3 branch; no limit changes',comparison_limits='same preregistered OEL2C network limits',
        active_limited_equilibrium_qualified=profile.startswith('active_limited'),active_regulating_equilibria_qualified=profile.startswith('current_regulating'),boundary_equilibria_qualified=False))
    write_json(out/'summary.json',dict(passed=False,status='running'));checks=[]
    for name in names:
        print(name,flush=True);s=case_factory(name)
        active=any(p.initialization=='active_limited_equilibrium' for p in s.coupled.oel2c_limiters)
        regulating=any(p.initialization=='current_regulating_equilibrium' for p in s.coupled.oel2c_limiters)
        if active!=profile.startswith('active_limited') or regulating!=profile.startswith('current_regulating'):raise ValueError('stationary case does not match declared profile')
        folder=out/name;folder.mkdir();write_json(folder/'study.json',s.model_dump(mode='json'))
        refs=[native_reference(s,folder/('native-'+str(j)),root,tol) for j,tol in enumerate(TOLERANCES)]
        if any(r['reference']!=identity for r in refs):raise ValueError('reference dependency identity changed')
        a=run_study(s,isolate=True,wall_timeout_s=120.);write_json(folder/'production.json',a)
        if not a.get('completed'):raise ValueError('loaded primary initialization failed: '+str(a))
        if a['operating_point']['checked_dynamic_residual_per_s']>1e-8:raise ValueError('nonstationary loaded initial state')
        audit=compare_initial_references(s,a,refs[-1]);write_json(folder/'reference-initialization.json',audit)
        if regulating:
            spectral=compare_conditional_spectra(a,refs[-1]);write_json(folder/'conditional-spectrum.json',spectral)
            if not spectral['passed']:raise ValueError('independent conditional spectra differ or are unstable')
        rows=[compare(s,a,r) for r in refs]
        keys=[k for k in rows[-1]['errors'] if k.endswith(('.delta','.omega','.speed_pu','.OEL2C.output_pu'))]
        converged=all(rows[-1]['errors'][k]<=rows[0]['errors'][k] or rows[-1]['errors'][k]<1e-8 for k in keys)
        check=dict(passed=rows[-1]['passed'] and converged,convergence_passed=converged,steps=rows);write_json(folder/'comparison.json',check)
        if not check['passed']:raise ValueError('loaded OEL2C independent feedback mismatch: '+name)
        checks.append(check)
    rejected={}
    for name in negative_names:
        data=negative_factory(name);write_json(out/'negative'/(name+'-input.json'),data)
        try:s=StudySpec.model_validate(data)
        except ValueError:rejected[name]='schema_rejected';continue
        result=run_study(s,isolate=True,wall_timeout_s=120.);write_json(out/'negative'/(name+'-result.json'),result)
        if result.get('completed') or 'channels' in result:raise ValueError('infeasible equilibrium accepted: '+name)
        rejected[name]=result['reason']
    if code!=package_fingerprint() or sources!=source_identity() or identity!=verify(root):raise ValueError('sources or dependencies changed during validation')
    result=dict(passed=True,profile=profile,cases=len(checks),native_feedback_runs=len(checks)*len(TOLERANCES),rejected=rejected,code_sha256=code,
        maximum_physical_event_error_s=max(c['steps'][-1]['maximum_physical_event_time_error_s'] for c in checks),
        artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in out.rglob('*') if p.is_file() and p!=out/'summary.json'})
    write_json(out/'summary.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--cases',nargs='+',choices=NAMES,default=list(NAMES));a=p.parse_args();print(json.dumps(run(a.out,a.root,tuple(a.cases)),indent=2))
