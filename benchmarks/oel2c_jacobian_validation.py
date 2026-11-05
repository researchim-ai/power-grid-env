"""Registered unchanged OEL2C inputs: startup, loaded and stationary modes."""
import argparse
from pathlib import Path
from benchmarks.coupled_jacobian_validation import worker,qualify as corpus
from power_grid_env.oel2c_jacobian import KIND
from power_grid_env.study_spec import StudySpec
from benchmarks import generator_oel2c_cases as cold,generator_oel2c_loaded_cases as loaded,generator_oel2c_active_cases as active,generator_oel2c_regulating_cases as regulating,generator_oel2c_selected_cases as selected

FAMILIES=dict(cold=cold,loaded=loaded,active=active,regulating=regulating,selected=selected)
ALL_NAMES=tuple(family+'__'+n for family,module in FAMILIES.items() for n in module.NAMES)
# Explicit multi-limiter exclusion: coincident cross-device event order has
# a retained negative witness. No input or event tolerance is changed.
MULTI=('cold__two_limiters','cold__four_machines','loaded__two_limiters','active__two_limiters','active__mixed_modes','regulating__two_regulators','regulating__mixed_modes','selected__two_selected')
NAMES=tuple(n for n in ALL_NAMES if n not in MULTI)


def case(name):
    if name not in ALL_NAMES:raise ValueError('registered OEL2C case required')
    family,n=name.split('__',1);s=FAMILIES[family].example(n);s.id='analytic-oel2c-'+name
    return StudySpec.model_validate(s.model_dump(mode='json'))


def qualify(out,repeats=2):return corpus(out,repeats,names=NAMES,case_factory=case,worker_module='benchmarks.oel2c_jacobian_validation')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--child',action='store_true');p.add_argument('--study',type=Path);p.add_argument('--mode',choices=['analytic','fd']);a=p.parse_args()
    if a.child:worker(a.study,a.mode,a.out,analytic_kind=KIND)
    else:qualify(a.out)
