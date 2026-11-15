"""Preserve rejected original backup timing and anchor a separate revision."""
from pathlib import Path
from power_grid_env.closed_loop_protection import ClosedLoopPlan
from power_grid_env.running_fault_protection import run,verify_clearance
from power_grid_env.study_artifacts import read_json,encoded,seal,digest
from benchmarks.running_fault_protection_reference import recipes
from benchmarks.closed_loop_protection_reference import algebra
from types import SimpleNamespace


def validate_revision(original,candidate):
    original=ClosedLoopPlan.model_validate(original).model_dump(mode='json')
    candidate=ClosedLoopPlan.model_validate(candidate).model_dump(mode='json')
    a=original['backup']['time_multiplier'];b=candidate['backup']['time_multiplier']
    for data in [original,candidate]:
        data.pop('id');data['backup'].pop('time_multiplier')
    if original!=candidate or not 0<b<a:
        raise ValueError('revision may only reduce backup TMS and rename plan')
    return dict(original_backup_tms=a,candidate_backup_tms=b)


def qualify(original,candidate,out):
    revision=validate_revision(original,candidate)
    root=Path(out);root.mkdir(parents=True,exist_ok=False);rows=[]
    for kind,uv,method,grid,study,req in recipes(original):
        if kind!='measurement_loss' or not uv:continue
        name=f'original-{method}-grid{grid}';print(name,flush=True)
        r=run(study,req,root/name);verify_clearance(root/name,expected_study=study,expected_requirements=req)
        report=read_json(root/name/'trace.json');oracle=algebra(SimpleNamespace(study=study),report)
        progress=max(report['channels']['backup-oc.progress'])
        control=(not r['passed'] and r['checks']['prefault_running'] and r['checks']['clock_audits'] and not r['checks']['grid_isolation_deadline'] and progress<1 and oracle['passed'])
        rows.append(dict(id=name,rejection_reproduced=control,profile_passed=r['passed'],max_backup_progress=progress,algebra=oracle))
    result=dict(passed=len(rows)==6 and all(r['rejection_reproduced'] for r in rows),revision=revision,rows=rows,
                original_profile_accepted=False,candidate_profile_qualified_here=False,
                producer_sha256=digest(Path(__file__).read_bytes()),
                scope='Six negative controls with UV present on original VI .07 backup timing. Separate .015 candidate requires its full paired matrix; only backup TMS and label change in base plan.')
    for name,value in [('original',original),('candidate',candidate),('summary',result)]:
        (root/(name+'.json')).write_bytes(encoded(value))
    seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['original','candidate','out']:p.add_argument('--'+name,required=True)
    a=p.parse_args();r=qualify(read_json(a.original),read_json(a.candidate),a.out);print(dict(passed=r['passed']))
    if not r['passed']:raise SystemExit(2)
