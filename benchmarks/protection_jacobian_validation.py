"""Registered MNLEX2/rotor-thermal analytic-vs-FD algorithm checks."""
import argparse
from pathlib import Path
from benchmarks.coupled_jacobian_validation import worker,qualify as corpus
from power_grid_env.protection_jacobian import KIND
from power_grid_env.study_spec import StudySpec
from benchmarks.generator_underexcitation_cases import NAMES as UEL
from benchmarks.ex02_cases import NAMES as EX02

NAMES=tuple('uel_'+n for n in UEL)+('esst_field_trip','esst_uel_summation')+tuple('ex02_'+n for n in EX02 if n!='two_limiters_sensitive')


def case(name):
    if name not in NAMES:raise ValueError('registered protection case required')
    if name.startswith('uel_'):
        from benchmarks.generator_underexcitation_cases import example
        s=example(name[4:])
    elif name.startswith('esst_'):
        from benchmarks.generator_esst1a_cases import example
        s=example(name[5:])
    else:
        from benchmarks.ex02_cases import example
        s=example(name[5:]);s.study='coupled_rms'
    s.id='analytic-protection-'+name
    return StudySpec.model_validate(s.model_dump(mode='json'))


def qualify(out,repeats=2):return corpus(out,repeats,names=NAMES,case_factory=case,worker_module=__name__ if __name__!='__main__' else 'benchmarks.protection_jacobian_validation')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--child',action='store_true');p.add_argument('--study',type=Path);p.add_argument('--mode',choices=['analytic','fd']);a=p.parse_args()
    if a.child:worker(a.study,a.mode,a.out,analytic_kind=KIND)
    else:qualify(a.out)


if __name__=='__main__':main()
