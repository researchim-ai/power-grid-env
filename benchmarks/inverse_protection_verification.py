"""Recompute finite-corner evidence and conditionally independent SI measurements."""
import argparse
from pathlib import Path
from power_grid_env.protection_envelope import verify_envelope,effective_plans
from power_grid_env.closed_loop_protection import compile_closed_loop,verify_closed_loop
from power_grid_env.study_artifacts import encoded,read_json,seal,verify,digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.closed_loop_protection_reference import algebra,run as compare
from benchmarks.inverse_protection_acceptance import FAMILIES


def envelope_reference(root,owner,out):
    root=Path(root);checked=verify_envelope(root,expected_envelope=owner);rows=[]
    max_clock=[0.,0.,0.]
    for name,plan,recipe in effective_plans(owner):
        _,pack,_,_=compile_closed_loop(plan)
        stored=read_json(root/name/'result.json')
        for action in stored['actions']:
            for level,row in enumerate(action['grids']):
                max_clock[level]=max(max_clock[level],*(r['max_progress_error'] for r in row['clock_audits'].values()))
        for case in pack.cases:
            for level in range(3):
                report=read_json(root/name/'studies'/case.id/f'grid-{level}'/'result.json')
                check=algebra(case,report)
                rows.append(dict(corner=name,case=case.id,grid=level,**check))
    result=dict(passed=checked['passes_all_enumerated_corners'] and all(r['passed'] for r in rows),
        envelope_manifest_sha256=verify(root),code_sha256=package_fingerprint(),rows=rows,
        nominal_plus_corner_count=17,unique_physical_grid_runs=len(rows),max_clock_error_by_grid=max_clock,
        continuous_uncertainty_qualified=False,independent_differential_trajectory=False,
        source_kind='independent SI algebra conditioned on saved EMFs; clock replay from event-sided measured currents')
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    (out/'summary.json').write_bytes(encoded(result));seal(out);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--envelope',required=True);p.add_argument('--input',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    r=envelope_reference(a.envelope,read_json(a.input),a.out)
    print(encoded(dict(passed=r['passed'],grid_checks=len(r['rows']),max_clock_error_by_grid=r['max_clock_error_by_grid'])).decode());raise SystemExit(0 if r['passed'] else 2)
