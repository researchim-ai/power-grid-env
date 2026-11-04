"""Preregistered PSS, droop-OEL and joint analytic/FD algorithm corpus."""
import argparse
from pathlib import Path
from power_grid_env.study_spec import StudySpec
from power_grid_env.generator_excitation import OverexcitationLimiter
from power_grid_env.pss_droop_jacobian import KIND
from benchmarks.generator_excitation_cases import NAMES as OEL_NAMES
from benchmarks.coupled_jacobian_validation import worker as run_worker,qualify as run_corpus

PSS_NAMES=('hold','motor_load','fault','saturated_fault','classical_fault','voltage_gate','limits')
JOINT=('joint_hold','joint_fault','joint_saturated_fault','joint_gate')
NAMES=tuple('pss_'+n for n in PSS_NAMES)+tuple('oel_'+n for n in OEL_NAMES)+JOINT+('joint_breaker_open','scale_512_32_4','scale_2000_8_4')


def case(name):
    if name not in NAMES:raise ValueError('registered PSS/droop case required')
    if name.startswith('oel_'):
        from benchmarks.generator_excitation_cases import example
        s=example(name[4:])
    elif name.startswith('scale_'):
        from benchmarks.ex03_cases import scale_case
        s=scale_case(*map(int,name.split('_')[1:]))
        p=StudySpec.model_validate_json(Path('cases/studies/pss_coupled_hold.json').read_text()).coupled.machines[0].stabilizer
        for m in s.coupled.machines:m.stabilizer=p.model_copy(deep=True)
        s.coupled.overexcitation_limiters=[limiter(m.generator_id) for m in s.coupled.machines[:2]]
    else:
        base=name[4:] if name.startswith('pss_') else {'joint_hold':'hold','joint_fault':'fault','joint_saturated_fault':'saturated_fault','joint_gate':'voltage_gate','joint_breaker_open':'fault'}[name]
        s=StudySpec.model_validate_json(Path('cases/studies/pss_coupled_'+base+'.json').read_text())
        if name in JOINT or name=='joint_breaker_open':s.coupled.overexcitation_limiters=[limiter('g')]
        if name=='joint_breaker_open':
            from power_grid_env.coupled_rms import CoupledEvent
            s.coupled.events.append(CoupledEvent(time_s=1.2,kind='generator_open',equipment_id='g'))
    s.id='analytic-pss-droop-'+name
    return StudySpec.model_validate(s.model_dump(mode='json'))


def limiter(gid):
    return OverexcitationLimiter(generator_id=gid,rated_field_reaction_pu=1.,current_limit_pu=1.9,measurement_time_s=.04,
        feedback_gain_pu=.3,bias_time_s=.08,maximum_voltage_bias_pu=.15)


def qualify(out,repeats=2):
    return run_corpus(out,repeats,names=NAMES,case_factory=case,worker_module='benchmarks.pss_droop_jacobian_validation')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--child',action='store_true');p.add_argument('--study',type=Path);p.add_argument('--mode',choices=['analytic','fd']);a=p.parse_args()
    if a.child:run_worker(a.study,a.mode,a.out,analytic_kind=KIND)
    else:qualify(a.out)


if __name__=='__main__':main()
