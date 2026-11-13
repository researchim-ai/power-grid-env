"""Adaptive-budget refinement against archived fixed-mesh trajectories.

An archived production result is a regression comparator, not an independent
continuous-time oracle. Preserve all failures and report elapsed time only as
an observation; other jobs or different hosts invalidate speed comparisons.
"""
import argparse
import hashlib
import time
from pathlib import Path
from benchmarks.extended_closed_loop_acceptance import NAMES,document
from power_grid_env.switching_drive import SwitchingDriveStudy,run_switching_drive
from power_grid_env.switching_convergence import SwitchingTemporalLimits,audited,differences
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,seal


def run(out,reference_root,*,names=NAMES,fractions=(.2,.1,.05)):
    if len(fractions)<3 or any(not 1e-6<=v<=1 for v in fractions) or any(a<=b for a,b in zip(fractions,fractions[1:])):raise ValueError('at least three strictly decreasing adaptive budgets required')
    if not names or len(set(names))!=len(names) or any(n not in NAMES for n in names):raise ValueError('registered unique cases required')
    root=Path(reference_root)
    refs={name:root/(name+'.json') for name in names}
    # Resolve inputs before creating an output directory.
    reference={n:read_json(p) for n,p in refs.items()}
    before=package_fingerprint();out=Path(out);out.mkdir(parents=True,exist_ok=False)
    protocol=dict(fractions=fractions,limits=SwitchingTemporalLimits().model_dump(mode='json'),references={n:hashlib.sha256(p.read_bytes()).hexdigest() for n,p in refs.items()},code_sha256=before)
    (out/'protocol.json').write_bytes(encoded(protocol));rows=[]
    for name in names:
        previous=None;levels=[]
        for index,fraction in enumerate(fractions):
            doc=document(name);doc['adaptive']={'error_budget_fraction':fraction};p=SwitchingDriveStudy.model_validate(doc)
            folder=out/name/f'level-{index}';folder.mkdir(parents=True)
            (folder/'study.json').write_bytes(encoded(p.model_dump(mode='json')))
            start=time.perf_counter()
            try:
                result=run_switching_drive(doc);(folder/'result.json').write_bytes(encoded(result))
                audits=audited(p,result,SwitchingTemporalLimits());reference_audits=audited(p,reference[name],SwitchingTemporalLimits())
                check=differences(p,reference[name],result,SwitchingTemporalLimits())
                pair=differences(p,previous,result,SwitchingTemporalLimits()) if previous is not None else None
                assessment=dict(completed=True,passed=check['passed'] and all(audits.values()) and all(reference_audits.values()) and (pair is None or pair['passed']),audits=audits,reference_audits=reference_audits,reference_difference=check,previous_budget_difference=pair,integration=result['integration'],accepted_steps=result['metrics']['steps'],elapsed_s=time.perf_counter()-start)
                previous=result
            except (ValueError,RuntimeError) as exc:
                assessment=dict(completed=False,passed=False,error=str(exc),elapsed_s=time.perf_counter()-start);previous=None
            levels.append(assessment);(folder/'assessment.json').write_bytes(encoded(assessment))
            print(name,fraction,assessment['passed'],flush=True)
        rows.append(dict(case=name,passed=all(v['passed'] for v in levels),levels=levels))
    if package_fingerprint()!=before:raise RuntimeError('adaptive acceptance producer changed during run')
    summary=dict(schema='power-grid-env.switching-adaptive-acceptance/v1',passed=all(r['passed'] for r in rows),rows=rows,absolute_error_certified=False,independent_reference=False,scope='three decreasing local budgets, full archived fixed-mesh comparison and pairwise physical caps; no monotonicity/order claim; no external simulator ranking')
    (out/'summary.json').write_bytes(encoded(summary));seal(out);return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True);parser.add_argument('--reference-root',required=True);parser.add_argument('--case',action='append',choices=NAMES)
    args=parser.parse_args();result=run(args.out,args.reference_root,names=tuple(args.case) if args.case else NAMES)
    raise SystemExit(0 if result['passed'] else 1)
