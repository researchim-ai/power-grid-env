"""Three IEC families, two solvers, all six complete normal/fault scenarios."""
import argparse
import shutil
from pathlib import Path
from power_grid_env.closed_loop_protection import run_closed_loop,verify_closed_loop,ClosedLoopPlan
from power_grid_env.study_artifacts import encoded,read_json,seal,verify,digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.closed_loop_protection_reference import run as reference

FAMILIES=['standard_inverse','very_inverse','extremely_inverse']


def run(cases,out,*,reuse_si_radau=None):
    cases=Path(cases);root=Path(out);root.mkdir(parents=True,exist_ok=False);code=package_fingerprint();rows=[]
    for family in FAMILIES:
        for method in ['radau','network_dae']:
            plan=read_json(cases/(family+'-'+method+'.json'));target=root/family/method
            reused=family=='standard_inverse' and method=='radau' and reuse_si_radau is not None
            if reused:
                source=Path(reuse_si_radau);verify_closed_loop(source,expected_plan=plan)
                target.parent.mkdir(exist_ok=True);shutil.copytree(source,target)
                result=read_json(target/'result.json')
            else:result=run_closed_loop(plan,target)
            if not result['passes_declared_profile']:raise ValueError('unqualified inverse profile: '+family+'/'+method)
            checked=verify_closed_loop(target,expected_plan=plan)
            rows.append(dict(family=family,method=method,passed=True,grid_runs=18,reused_current_artifact=reused,manifest_sha256=checked['manifest_sha256']))
            print(family,method,'passed',flush=True)
        ref=reference(root/family/'radau',root/family/'network_dae',root/family/'reference')
        if not ref['passed']:raise ValueError('inverse solver/reference comparison failed: '+family)
    if package_fingerprint()!=code:raise ValueError('producer changed during matrix')
    summary=dict(passed=True,code_sha256=code,rows=rows,unique_physical_grid_runs=108,
        new_runs_in_command=108-18*sum(r['reused_current_artifact'] for r in rows),reference_grid_pairs=54,
        producer_sha256=digest(Path(__file__).read_bytes()),independent_differential_trajectory=False,normative_approval=False)
    (root/'summary.json').write_bytes(encoded(summary));seal(root);return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--cases',required=True);p.add_argument('--out',required=True);p.add_argument('--reuse-si-radau');a=p.parse_args()
    r=run(a.cases,a.out,reuse_si_radau=a.reuse_si_radau);print(encoded(r).decode())
