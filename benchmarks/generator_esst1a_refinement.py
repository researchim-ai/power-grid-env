"""Independent numerical refinement of ESST1A/MNLEX2 hybrid trajectories.

Self-convergence and cross-solver agreement are separate observations. A
converged negative witness never becomes qualified by this diagnostic.
"""
from pathlib import Path
import math
from benchmarks.generator_esst1a_joint_cases import example,NAMES as JOINT_NAMES
from benchmarks.generator_esst1a_joint_validation import significant_resets,compare,EVENT_LIMIT,STATE_LIMIT
from benchmarks.esst1a_network import run as native_run
from benchmarks.mnlex2_guard_audit import audit
from power_grid_env.study_spec import run_study,StudySpec
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json

STRESS='two_active_limiters_stress'
NAMES=(*JOINT_NAMES,STRESS)
CASES=('activation_recovery','saturation_recovery','fast_feedback','fault_clear','two_limiters_sensitive',STRESS)
PRIMARY=((1e-11,1e-13,1.),(1e-12,1e-14,.5),(1e-13,1e-15,.25))
NATIVE=(1e-11,1e-12)


def case(name):
    if name==STRESS:
        path=Path(__file__).resolve().parents[1]/'cases/studies/generator_esst1a_two_active_limiters_stress.json'
        return StudySpec.model_validate_json(path.read_text())
    return example(name)


def reset_distance(left,right,left_native=False,right_native=False):
    """Compare each generator's ordered significant reset inventory in full."""
    a=significant_resets(left,left_native);b=significant_resets(right,right_native)
    if not left.get('completed') or not right.get('completed'):raise ValueError('complete refinement trajectories required')
    ids=sorted({e['equipment_id'] for e in a+b});inventory=[];pairs=[]
    for gid in ids:
        x=[e for e in a if e['equipment_id']==gid];y=[e for e in b if e['equipment_id']==gid]
        inventory.append(dict(generator_id=gid,left=len(x),right=len(y)))
        if any(e['time_s']>f['time_s'] for z in (x,y) for e,f in zip(z,z[1:])):raise ValueError('unordered reset evidence')
        for p,q in zip(x,y):
            dt=abs(p['time_s']-q['time_s']);jump=max(abs(p[k]-q[k]) for k in ('raw_state_before_pu','raw_state_after_pu'))
            pairs.append(dict(generator_id=gid,left_time_s=p['time_s'],right_time_s=q['time_s'],time_error_s=dt,raw_jump_error_pu=jump,same_bound=p['bound']==q['bound']))
    return dict(passed=all(r['left']==r['right'] for r in inventory) and all(p['same_bound'] and p['time_error_s']<=EVENT_LIMIT and p['raw_jump_error_pu']<=STATE_LIMIT for p in pairs),inventory=inventory,pairs=pairs,maximum_time_error_s=max((p['time_error_s'] for p in pairs),default=0.),maximum_raw_jump_error_pu=max((p['raw_jump_error_pu'] for p in pairs),default=0.),event_limit_s=EVENT_LIMIT,state_limit_pu=STATE_LIMIT)


def validate_tiers(primary,native):
    if len(primary)<2 or len(native)<2:raise ValueError('at least two primary and native refinement tiers required')
    if len(set(primary))!=len(primary) or len(set(native))!=len(native):raise ValueError('duplicate refinement tiers')
    for r,a,f in primary:
        if not all(math.isfinite(v) and v>0 for v in (r,a,f)) or r<1e-13 or r>1e-4 or a>1e-6 or f>1:raise ValueError('primary refinement tier outside numerical profile')
    if any(not math.isfinite(t) or not 0<t<=1e-6 for t in native):raise ValueError('native refinement tier outside numerical profile')
    if any(not (q[0]<p[0] and q[1]<p[1] and q[2]<=p[2]) for p,q in zip(primary,primary[1:])) or any(q>=p for p,q in zip(native,native[1:])):raise ValueError('strictly tighter tolerances and nonincreasing step factors required')


def refine(out,root,names=CASES,primary_tiers=PRIMARY,native_tiers=NATIVE):
    from benchmarks.native_online_validation import source_identity
    from benchmarks.mnlex2_model_exchange import verify
    if not names or len(set(names))!=len(names) or not set(names)<=set(NAMES):raise ValueError('unique registered joint cases required')
    validate_tiers(primary_tiers,native_tiers)
    code=package_fingerprint();sources=source_identity();identity=verify(root)
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    write_json(out/'summary.json',dict(passed=False,status='running',industrial_profile_qualified=False))
    write_json(out/'protocol.json',dict(code_sha256=code,source_sha256=sources,reference=identity,cases={n:case(n).model_dump(mode='json') for n in names},primary_tiers=[dict(rtol=r,atol=a,maximum_step_factor=f) for r,a,f in primary_tiers],native_tiers=list(native_tiers),comparison_grid='original declared case grid; never the refined primary grid',event_limit_s=EVENT_LIMIT,state_limit_pu=STATE_LIMIT,negative_witnesses_accepted_as_agreement=False,native_UEL_event_policy='causal',native_UEL_root_policy='continuous_atoms'))
    rows=[]
    for name in names:
        print(name,flush=True);spec=case(name);directory=out/name;directory.mkdir();primary=[];native=[]
        for index,(rtol,atol,factor) in enumerate(primary_tiers):
            s=spec.model_copy(deep=True);s.coupled.rtol=rtol;s.coupled.atol=atol;s.coupled.step_s=spec.coupled.step_s*factor
            s=StudySpec.model_validate(s.model_dump(mode='json'))
            a=run_study(s);write_json(directory/('primary-'+str(index)+'.json'),a)
            if not a.get('completed'):raise ValueError('primary refinement failed: '+name+' '+str(index))
            primary.append(a)
        for index,tolerance in enumerate(native_tiers):
            b=native_run(spec,directory/('native-'+str(index)),root,tolerance,native_uels=True)
            write_json(directory/('native-'+str(index))/'reference.json',b)
            if not b.get('completed'):raise ValueError('native refinement failed: '+name+' '+str(index))
            write_json(directory/('native-'+str(index))/'guard-audit.json',audit(spec,b))
            native.append(b)
        pchecks=[dict(tier=i,**reset_distance(a,primary[-1])) for i,a in enumerate(primary[:-1])]
        nchecks=[dict(tier=i,**reset_distance(b,native[-1],True,True)) for i,b in enumerate(native[:-1])]
        cross=[]
        for i,a in enumerate(primary):
            for j,b in enumerate(native):
                check=compare(spec,a,b)
                write_json(directory/('cross-'+str(i)+'-'+str(j)+'.json'),check)
                cross.append(dict(primary_tier=i,native_tier=j,passed=check['passed'],reset_failures=check['reset_failures'],maximum_reset_time_error_s=max((r['time_error_s'] for r in check['native_UEL_reset_checks']),default=0.)))
        row=dict(case=name,primary_reset_refinement=pchecks,native_reset_refinement=nchecks,primary_reset_clocks_self_converged=all(c['passed'] for c in pchecks),native_reset_clocks_self_converged=all(c['passed'] for c in nchecks),all_cross_solver_comparisons_agree=all(c['passed'] for c in cross),cross_solver=cross,industrial_profile_qualified=False)
        write_json(directory/'summary.json',row);rows.append(row)
    if code!=package_fingerprint() or sources!=source_identity() or identity!=verify(root):raise ValueError('refinement producer changed')
    result=dict(passed=True,status='complete',diagnostic_completed=True,code_sha256=code,source_sha256=sources,all_cross_solver_comparisons_agree=all(r['all_cross_solver_comparisons_agree'] for r in rows),self_convergence_is_not_cross_solver_validation=True,industrial_profile_qualified=False,runs=rows)
    write_json(out/'summary.json',result);return result


if __name__=='__main__':
    import argparse,json
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--cases',nargs='+',choices=NAMES,default=CASES);p.add_argument('--require-agreement',action='store_true',help='fail if any cross-solver trajectory disagrees, including negative witnesses')
    a=p.parse_args();r=refine(a.out,a.root,a.cases)
    print(json.dumps(dict(diagnostic_completed=r['diagnostic_completed'],all_cross_solver_comparisons_agree=r['all_cross_solver_comparisons_agree'],industrial_profile_qualified=False)))
    raise SystemExit(1 if a.require_agreement and not r['all_cross_solver_comparisons_agree'] else 0)
