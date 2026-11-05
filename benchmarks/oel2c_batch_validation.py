"""Explicit opt-in variants of the eight legacy multiple-OEL profiles."""
import argparse
from pathlib import Path
from benchmarks.oel2c_jacobian_validation import case as original,MULTI
from benchmarks.coupled_jacobian_validation import worker,qualify as corpus
from power_grid_env.oel2c_jacobian import KIND
from power_grid_env.oel2c_events import POLICY
from power_grid_env.study_spec import StudySpec

NAMES=MULTI


def case(name):
    s=original(name)
    for p in s.coupled.oel2c_limiters:p.coincidence_policy=POLICY
    s.id='atomic-below-clock-'+name
    return StudySpec.model_validate(s.model_dump(mode='json'))


def qualify(out,repeats=2):return corpus(out,repeats,names=NAMES,case_factory=case,worker_module='benchmarks.oel2c_batch_validation')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--child',action='store_true');p.add_argument('--study',type=Path);p.add_argument('--mode',choices=['analytic','fd']);a=p.parse_args()
    if a.child:worker(a.study,a.mode,a.out,analytic_kind=KIND)
    else:qualify(a.out)
