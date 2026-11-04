"""Strict native ESST1A/MNLEX2 agreement with per-component event routing.

Historical broadcast counterexamples remain available in the legacy runner.
Here every case must agree: there are no expected rejections or wider budgets.
"""
from pathlib import Path
import math,json
from benchmarks.generator_esst1a_refinement import case,NAMES
from benchmarks.generator_esst1a_joint_validation import compare,EVENT_LIMIT,STATE_LIMIT
from benchmarks.esst1a_network import run
from benchmarks.mnlex2_guard_audit import audit
from power_grid_env.study_spec import run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json


def qualify(out,root,names=NAMES,tolerances=(1e-11,1e-12)):
    from benchmarks.native_online_validation import source_identity
    from benchmarks.mnlex2_model_exchange import verify
    if not names or len(set(names))!=len(names) or not set(names)<=set(NAMES):raise ValueError('unique registered causal cases required')
    if len(tolerances)<2 or len(set(tolerances))!=len(tolerances) or any(not math.isfinite(t) or not 0<t<=1e-6 for t in tolerances):raise ValueError('at least two unique positive finite native tolerances required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();source=source_identity();identity=verify(root)
    write_json(out/'summary.json',dict(passed=False,status='running'))
    write_json(out/'protocol.json',dict(code_sha256=code,source_sha256=source,reference=identity,cases={n:case(n).model_dump(mode='json') for n in names},native_tolerances=list(tolerances),native_UEL_event_policy='causal',native_UEL_root_policy='continuous_atoms',expected_rejections_accepted=False,event_limit_s=EVENT_LIMIT,state_limit_pu=STATE_LIMIT,source_equations_modified=False,primary_states_or_clocks_imported=False))
    rows=[]
    for name in names:
        print(name,flush=True);s=case(name);directory=out/name;directory.mkdir();a=run_study(s);write_json(directory/'primary.json',a)
        if not a.get('completed'):raise ValueError('primary causal case failed: '+name)
        for tolerance in tolerances:
            b=run(s,directory/str(tolerance),root,tolerance,native_uels=True,uel_event_policy='causal',uel_root_policy='continuous_atoms')
            write_json(directory/str(tolerance)/'reference.json',b)
            if b.get('native_UEL_event_policy')!='causal':raise ValueError('causal native event routing required')
            if b.get('native_UEL_root_policy')!='continuous_atoms':raise ValueError('continuous native UEL roots required')
            guard_audit=audit(s,b);write_json(directory/str(tolerance)/'guard-audit.json',guard_audit)
            check=compare(s,a,b);check['native_guard_audit']=guard_audit;write_json(directory/str(tolerance)/'comparison.json',check)
            rows.append(dict(case=name,tolerance=tolerance,**check))
    if code!=package_fingerprint() or source!=source_identity() or identity!=verify(root):raise ValueError('causal reference producer changed')
    result=dict(passed=all(r['passed'] for r in rows),status='complete',all_registered_trajectories_agree=all(r['passed'] for r in rows),expected_rejections_accepted=False,native_UEL_event_policy='causal',native_UEL_root_policy='continuous_atoms',code_sha256=code,source_sha256=source,runs=rows,industrial_profile_qualified=False)
    write_json(out/'summary.json',result);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--cases',nargs='+',choices=NAMES,default=NAMES)
    a=p.parse_args();r=qualify(a.out,a.root,a.cases);print(json.dumps(dict(passed=r['passed'],runs=len(r['runs']),all_registered_trajectories_agree=r['all_registered_trajectories_agree'])))
    raise SystemExit(0 if r['passed'] else 1)
