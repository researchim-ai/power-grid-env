"""Preregistered selected stationary OEL active sets; no boundary stability claim."""
from pathlib import Path
import argparse,json
from benchmarks.generator_oel2c_selected_cases import NAMES,example
from benchmarks.generator_oel2c_validation import native_reference,compare,TOLERANCES
from benchmarks.native_online_validation import source_identity
from benchmarks.dynawo_reference_setup import verify
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.report import write_json,sha256_file

NEGATIVE=('missing_policy','legacy_model','wrong_AVR_policy','missing_reference_memory','missing_PID_memory','contradictory_reference','contradictory_PID','PID_outside_bounds','reference_outside_bounds','missing_loaded_point')


def negative(name):
    s=example('inactive' if name not in ('missing_reference_memory','missing_PID_memory','PID_outside_bounds','reference_outside_bounds') else 'zero_error_interior').model_dump(mode='json');p=s['coupled']['oel2c_limiters'][0]
    if name=='missing_policy':p['equilibrium_stability_policy']='default_checks'
    elif name=='legacy_model':p['model']='OEL2C_Dynawo_cap_guard_v1_SEXS_summation'
    elif name=='wrong_AVR_policy':p['avr_reference_initialization']='unmodified'
    elif name=='missing_reference_memory':p['equilibrium_reference_current_pu']=None
    elif name=='missing_PID_memory':p['equilibrium_pid_output_pu']=None
    elif name=='contradictory_reference':p['equilibrium_reference_current_pu']=1.2
    elif name=='contradictory_PID':p['equilibrium_pid_output_pu']=-.1
    elif name=='PID_outside_bounds':p['equilibrium_pid_output_pu']=-1.
    elif name=='reference_outside_bounds':p['equilibrium_reference_current_pu']=2.
    elif name=='missing_loaded_point':s['coupled']['operating_point']=None
    else:raise ValueError(name)
    return s


def initial_audit(spec,primary,native):
    op=primary['operating_point'];left=op['oel2c_AVR_reference_initialization'];right=native['AVR_reference_initialization']
    expected={p.generator_id for p in spec.coupled.oel2c_limiters}
    if set(left)!=set(right) or set(left)!=expected or set(op['oel2c_selected_equilibrium'])!=expected:raise ValueError('selected initial audit inventory differs')
    if op['full_OEL_stability_qualified'] is not False:raise ValueError('selected boundary must not claim OEL stability')
    errors={}
    for p in spec.coupled.oel2c_limiters:
        a,b=left[p.generator_id],right[p.generator_id];k=op['oel2c_selected_equilibrium'][p.generator_id]
        if a['policy']!='derive_from_operating_point_with_OEL' or abs(a['derived_reference_pu']-a['baseline_reference_pu']+a['limiter_output_pu'])>1e-12:raise ValueError('explicit selected AVR policy identity violated')
        error=max(abs(a[key]-b[key]) for key in ('derived_reference_pu','baseline_reference_pu','limiter_output_pu'))
        if error>1e-8 or k['maximum_derivative_per_s']>1e-8 or k['full_OEL_stability_qualified'] is not False:raise ValueError('nonstationary or mismatched selected initial point')
        for field,channel in [('equilibrium_reference_current_pu','reference_current_pu'),('equilibrium_pid_output_pu','pid_output_pu')]:
            value=getattr(p,field)
            if value is not None and abs(primary['channels'][p.generator_id+'.OEL2C.'+channel][0]-value)>1e-9:raise ValueError('supplied stationary memory was overwritten')
        errors[p.generator_id]=error
    return dict(passed=True,AVR_reference_error_pu=errors,full_OEL_stability_qualified=False)


def run(out,root,names=NAMES,*,case_factory=example,negative_factory=negative,registered_names=NAMES,negative_names=NEGATIVE,require_boundary_audit=False):
    if not names or len(set(names))!=len(names) or not set(names)<=set(registered_names):raise ValueError('unique registered cases required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();sources=source_identity();identity=verify(root)
    write_json(out/'protocol.json',dict(code_sha256=code,source_sha256=sources,reference=identity,tolerances=TOLERANCES,cases={n:canonical_hash(case_factory(n)) for n in names},negative={n:negative_factory(n) for n in negative_names},full_OEL_stability_qualified=False,initial_derivative_limit_per_s=1e-8,reference_before_primary=True))
    write_json(out/'summary.json',dict(passed=False,status='running'));checks=[]
    for name in names:
        print(name,flush=True);s=case_factory(name);folder=out/name;folder.mkdir();write_json(folder/'study.json',s.model_dump(mode='json'))
        refs=[native_reference(s,folder/('native-'+str(i)),root,tol) for i,tol in enumerate(TOLERANCES)]
        if any(r['reference']!=identity for r in refs):raise ValueError('changed reference identity')
        a=run_study(s,isolate=True,wall_timeout_s=120.);write_json(folder/'production.json',a)
        if not a.get('completed'):raise ValueError('selected primary did not complete: '+str(a))
        write_json(folder/'initial-audit.json',initial_audit(s,a,refs[-1]))
        if require_boundary_audit:
            audits={}
            for gid,initial in a['operating_point']['oel2c_selected_equilibrium'].items():
                left=initial.get('boundary_neighborhood');right=refs[-1]['limiter_initialization'][gid].get('boundary_neighborhood')
                if not left or not right or not left['passed'] or not right['passed'] or left['interval_certified'] or right['interval_certified']:raise ValueError('independent finite boundary audit missing or sensitive')
                audits[gid]=dict(primary=left,native=right)
            write_json(folder/'boundary-audit.json',audits)
        rows=[compare(s,a,r) for r in refs]
        keys=[key for key in rows[-1]['errors'] if key.endswith(('.delta','.omega','.speed_pu','.OEL2C.output_pu'))]
        converged=all(rows[-1]['errors'][k]<=rows[0]['errors'][k] or rows[-1]['errors'][k]<1e-8 for k in keys)
        c=dict(passed=bool(rows[-1]['passed'] and converged),convergence_passed=converged,steps=rows);write_json(folder/'comparison.json',c)
        if not c['passed']:raise ValueError('selected native feedback mismatch: '+name)
        checks.append(c)
    rejected={}
    for name in negative_names:
        data=negative_factory(name);write_json(out/'negative'/(name+'-input.json'),data)
        try:s=StudySpec.model_validate(data)
        except ValueError:rejected[name]='schema_rejected';continue
        r=run_study(s,isolate=True,wall_timeout_s=120.);write_json(out/'negative'/(name+'-result.json'),r)
        if r.get('completed') or 'channels' in r:raise ValueError('invalid stationary memory accepted: '+name)
        rejected[name]=r['reason']
    if code!=package_fingerprint() or sources!=source_identity() or identity!=verify(root):raise ValueError('producer changed during validation')
    result=dict(passed=True,finite_boundary_policy_required=require_boundary_audit,cases=len(checks),native_feedback_runs=len(checks)*len(TOLERANCES),rejected=rejected,code_sha256=code,full_OEL_stability_qualified=False,artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in out.rglob('*') if p.is_file() and p!=out/'summary.json'})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--cases',nargs='+',choices=NAMES,default=list(NAMES));a=p.parse_args();print(json.dumps(run(a.out,a.root,tuple(a.cases)),indent=2))
