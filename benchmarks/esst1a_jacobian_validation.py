"""Preregistered ESST1A/PI analytic-vs-FD cases with explicit no-UAL variants."""
import argparse
from pathlib import Path
from power_grid_env.study_spec import StudySpec
from power_grid_env.esst1a_jacobian import KIND
from benchmarks.coupled_jacobian_validation import worker as run_worker,qualify as run_corpus
from benchmarks.generator_esst1a_cases import NAMES as ESST_NAMES

SIMPLE=tuple(n for n in ESST_NAMES if n not in ('field_trip','uel_summation'))
PI=('hold','overload_recovery','generator_open','motor_open','governor_step','source_sag','takeover_priority','saturation_recovery','fast_feedback','pss_limited','pss_voltage_gate','authority_limited')
NAMES=tuple('esst_'+n for n in SIMPLE)+tuple('no_uel_pi_'+n for n in PI)+('scale_512_32_4','scale_2000_8_4')


def case(name):
    if name not in NAMES:raise ValueError('registered ESST1A Jacobian case required')
    if name.startswith('esst_'):
        from benchmarks.generator_esst1a_cases import example
        s=example(name[5:])
    elif name.startswith('no_uel_pi_'):
        from benchmarks.ex02_cases import example
        s=example(name[10:]);s.coupled.underexcitation_limiters=[]
        # This is a distinct explicitly named physical profile. No UEL removal
        # is done by the solver or native adapter.
        s.study='coupled_rms'
    else:
        from benchmarks.ex03_cases import scale_case
        from power_grid_env.exciter_esst1a import ESST1APassport
        from power_grid_env.excitation_takeover import TakeoverOELPassport
        from benchmarks.ex02_cases import modal
        s=scale_case(*map(int,name.split('_')[1:]));pss=next(m.stabilizer for m in modal('avr_pss_oel').coupled.machines if m.stabilizer)
        for m in s.coupled.machines:
            m.exciter=None;m.stabilizer=pss.model_copy(deep=True);m.stabilizer_connection='external_ESST1A'
        s.coupled.esst1a_exciters=[ESST1APassport(generator_id=m.generator_id,K_C=0.,V_IMAX=.5) for m in s.coupled.machines]
        s.coupled.takeover_oels=[TakeoverOELPassport(generator_id=m.generator_id,current_limit_pu=2.,integral_gain=20.) for m in s.coupled.machines[:2]]
    s.id='analytic-esst1a-'+name
    return StudySpec.model_validate(s.model_dump(mode='json'))


def qualify(out,repeats=2):
    return run_corpus(out,repeats,names=NAMES,case_factory=case,worker_module='benchmarks.esst1a_jacobian_validation')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--child',action='store_true');p.add_argument('--study',type=Path);p.add_argument('--mode',choices=['analytic','fd']);a=p.parse_args()
    if a.child:run_worker(a.study,a.mode,a.out,analytic_kind=KIND)
    else:qualify(a.out)


if __name__=='__main__':main()
