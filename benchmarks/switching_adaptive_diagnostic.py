"""Reproduce an adaptive minimum-step rejection without changing its estimator.

The wrapper records the last small rejected estimate, then calls the original
estimator unchanged. It is a diagnostic tool, not a qualification certificate.
"""
import argparse
from pathlib import Path
import numpy as np
from benchmarks.extended_closed_loop_acceptance import document,NAMES
import power_grid_env.switching_adaptive as adaptive
from power_grid_env.switching_drive import run_switching_drive
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded


def diagnose(name='double_cage',fraction=.1):
    p=document(name);p['adaptive']={'error_budget_fraction':fraction}
    original=adaptive._error;last={}
    def record(state,coarse,fine,gates,cfg):
        value=original(state,coarse,fine,gates,cfg)
        if value>1 and fine['elapsed']<1e-9:
            c=coarse['state'];f=fine['state']
            last.clear();last.update(estimate=value,elapsed_s=fine['elapsed'],coarse_elapsed_s=coarse['elapsed'],source_phases=fine['source_phases'],source_before_a=state.line.tolist(),source_coarse_a=c.line.tolist(),source_fine_a=f.line.tolist(),coarse_kcl_max_a=c.kcl_max,fine_kcl_max_a=f.kcl_max,deltas={k:float(np.max(abs(getattr(c,k)-getattr(f,k)))) if np.size(getattr(f,k)) else 0. for k in ['line','flux','temperature','winding_temperature','voltage']})
        return value
    adaptive._error=record
    try:
        result=run_switching_drive(p);status=dict(completed=True,integration=result['integration'])
    except (ValueError,RuntimeError) as exc:status=dict(completed=False,error=str(exc))
    finally:adaptive._error=original
    return dict(schema='power-grid-env.switching-adaptive-diagnostic/v1',case=name,error_budget_fraction=fraction,code_sha256=package_fingerprint(),last_small_rejection=last,**status)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--case',choices=NAMES,default='double_cage');parser.add_argument('--fraction',type=float,default=.1);parser.add_argument('--out',required=True)
    args=parser.parse_args();r=diagnose(args.case,args.fraction);Path(args.out).write_bytes(encoded(r));raise SystemExit(0 if r['completed'] else 2)
