"""Numerical-profile refinement and immutable weak-clock counterexample.

A rejected historical trajectory is never evidence qualifying current code.
"""
from pathlib import Path
import hashlib,json,gzip
from benchmarks.ex02_cases import example
from benchmarks.ex02_validation import compare
from benchmarks.esst1a_network import run
from benchmarks.generator_esst1a_refinement import reset_distance
from benchmarks.mnlex2_guard_audit import audit
from benchmarks.native_online_validation import source_identity
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json

ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/'requirements/ex02-conditioning-witness.json'

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def frozen_rejection(out):
    lock=json.loads(LOCK.read_text())
    if set(lock['artifacts'].values())!=set(lock['files']):raise ValueError('every frozen artifact must be pinned')
    for name,want in lock['files'].items():
        p=(ROOT/name).resolve()
        if not p.is_relative_to(ROOT) or not p.is_file() or sha(p)!=want:raise ValueError('changed or missing frozen conditioning witness: '+name)
    def read(key):
        with gzip.open(ROOT/lock['artifacts'][key],'rb') as f:data=f.read(64*1024*1024+1)
        if len(data)>64*1024*1024:raise ValueError('conditioning witness exceeds input budget')
        return json.loads(data)
    protocol=read('protocol')
    if protocol['code_sha256']!=lock['recorded_code_sha256']:raise ValueError('frozen producer identity differs')
    s=StudySpec.model_validate(protocol['cases']['pss_limited'])
    if any(p.event_detection!='machine_epsilon' for p in s.coupled.underexcitation_limiters):raise ValueError('legacy policy witness required')
    a=read('primary');rows=[]
    for tolerance in ('1e-11','1e-12'):
        b=read(tolerance);guard=audit(s,b);check=compare(s,a,b)
        failures=check['reset_failures']
        good=not check['passed'] and bool(failures) and guard['passed'] and all(v<=check['limits'][k] for k,v in check['errors'].items())
        good=good and all(r['primary_resets']==r['native_resets'] for r in check['reset_inventory']) and all(f['reason']=='native_UEL_reset_sides_differ' and f['event_time_error_s']>1e-6 and f['raw_jump_error_pu']<=2e-5 for f in failures)
        rows.append(dict(tolerance=tolerance,rejection_reproduced=bool(good),maximum_reset_time_error_s=max(r['time_error_s'] for r in check['native_UEL_reset_checks']),comparison=check))
    result=dict(negative_regression_passed=all(r['rejection_reproduced'] for r in rows),trajectory_qualified=False,qualifies_current_producer=False,recorded_code_sha256=lock['recorded_code_sha256'],witness_lock_sha256=sha(LOCK),runs=rows)
    write_json(Path(out)/'negative-witness.json',result);return result

def qualify(out,root):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();source=source_identity();s=example('pss_limited')
    tiers=((1e-12,1e-14,1.),(1e-13,1e-15,.5));tolerances=(1e-13,1e-14)
    write_json(out/'protocol.json',dict(code_sha256=code,source_sha256=source,study=s.model_dump(mode='json'),primary_tiers=tiers,native_tolerances=tolerances,event_limit_s=1e-6,state_limit_pu=2e-5,event_detection='conditioned_1e-8',event_floor_per_s=1e-8,ODE_or_physical_parameters_retuned=False))
    negative=frozen_rejection(out);primary=[];native=[]
    for i,(r,a,f) in enumerate(tiers):
        p=s.model_copy(deep=True);p.coupled.rtol=r;p.coupled.atol=a;p.coupled.step_s*=f
        result=run_study(p);write_json(out/f'primary-{i}.json',result)
        if not result.get('completed'):raise ValueError('conditioned primary refinement failed')
        primary.append(result)
    for i,t in enumerate(tolerances):
        result=run(s,out/f'native-{i}',root,t,native_uels=True);write_json(out/f'native-{i}/reference.json',result);write_json(out/f'native-{i}/guard-audit.json',audit(s,result));native.append(result)
    checks=[]
    for i,a in enumerate(primary):
        for j,b in enumerate(native):
            c=compare(s,a,b);write_json(out/f'cross-{i}-{j}.json',c);checks.append(dict(primary_tier=i,native_tier=j,passed=c['passed'],maximum_reset_error_s=max(r['time_error_s'] for r in c['native_UEL_reset_checks'])))
    pc=reset_distance(*primary);nc=reset_distance(*native,True,True)
    if code!=package_fingerprint() or source!=source_identity():raise ValueError('conditioning producer changed')
    result=dict(passed=all(r['passed'] for r in checks) and pc['passed'] and nc['passed'] and negative['negative_regression_passed'],code_sha256=code,source_sha256=source,cross_solver=checks,primary_self_refinement=pc,native_self_refinement=nc,negative_witness_qualified=False,event_detection='conditioned_1e-8',industrial_profile_qualified=False)
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();r=qualify(a.out,a.root);print(json.dumps(dict(passed=r['passed'])));raise SystemExit(0 if r['passed'] else 1)
