"""Preregistered round-rotor Jacobian algorithm corpus. FD is not a physics oracle."""
import argparse
from pathlib import Path
from power_grid_env.study_spec import StudySpec
from power_grid_env.genrou_jacobian import KIND
from benchmarks.coupled_jacobian_validation import worker as run_worker,qualify as run_corpus

GENROU=('hold','motor_load','voltage_sag','setpoint_steps','limits_recovery','thermal_trip','saturated_hold','saturated_sag')
REGULATED=('hold','limits_recovery','setpoint_steps')
NAMES=tuple('genrou_'+n for n in GENROU)+tuple('regulated_'+n for n in REGULATED)+('stator_control_trip','stator_three_machine','scale_512_32_4','scale_2000_8_4')


def case(name):
    if name not in NAMES:raise ValueError('registered round-rotor Jacobian case required')
    if name.startswith('genrou_'):
        s=StudySpec.model_validate_json(Path('cases/studies/genrou_coupled_'+name[7:]+'.json').read_text())
    elif name.startswith('regulated_'):
        s=StudySpec.model_validate_json(Path('cases/studies/regulated_coupled_'+name[10:]+'.json').read_text())
    elif name.startswith('stator_'):
        from benchmarks.generator_thermal_cases import example
        s=example(name[7:])
    else:
        from benchmarks.ex03_cases import scale_case
        s=scale_case(*map(int,name.split('_')[1:]))
    s.id='analytic-genrou-'+name
    return StudySpec.model_validate(s.model_dump(mode='json'))


def qualify(out,repeats=2):
    return run_corpus(out,repeats,names=NAMES,case_factory=case,worker_module='benchmarks.genrou_jacobian_validation')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--child',action='store_true');p.add_argument('--study',type=Path);p.add_argument('--mode',choices=['analytic','fd']);a=p.parse_args()
    if a.child:run_worker(a.study,a.mode,a.out,analytic_kind=KIND)
    else:qualify(a.out)


if __name__=='__main__':main()
