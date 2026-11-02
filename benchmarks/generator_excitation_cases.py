"""Declared excitation-current feedback experiments; no fitted manufacturer model."""
from pathlib import Path
from benchmarks.multigen_cases import example as multi
from power_grid_env.coupled_rms import CoupledEvent
from power_grid_env.generator_excitation import OverexcitationLimiter
from power_grid_env.study_spec import StudySpec

NAMES=('hold','voltage_step','recovery','limited_authority','saturated','two_limiters','motor_start','breaker_open','thermal_trip','exciter_recovery','exciter_floor')


def example(name):
    if name not in NAMES:raise ValueError('unknown excitation experiment')
    s=multi('dol2' if name=='motor_start' else 'hold2')
    s.id='generator-excitation-'+name;s.coupled.duration_s=2.
    # Explicit illustrative normalization, not a measured nameplate field base.
    p=OverexcitationLimiter(generator_id='g2',rated_field_reaction_pu=1.,current_limit_pu=1.4,
        measurement_time_s=.04,feedback_gain_pu=.3,bias_time_s=.08,maximum_voltage_bias_pu=.15)
    if name=='exciter_recovery':s.coupled.machines[1].exciter.EMAX=1.5
    if name=='limited_authority':p.maximum_voltage_bias_pu=.001
    if name=='saturated':
        for m in s.coupled.machines:
            m.saturation='quadratic';m.saturation_s10=.05;m.saturation_s12=.15
        p.current_limit_pu=1.6
    s.coupled.overexcitation_limiters=[p]
    if name=='two_limiters':s.coupled.overexcitation_limiters.append(p.model_copy(update={'generator_id':'g'}))
    if name not in ('hold','motor_start'):
        s.coupled.events=[CoupledEvent(time_s=.2,kind='voltage_reference_step',equipment_id='g2',value=.08)]
    if name in ('recovery','exciter_recovery','exciter_floor'):s.coupled.events.append(CoupledEvent(time_s=.8,kind='voltage_reference_step',equipment_id='g2',value=-.08))
    if name=='exciter_floor':
        s.coupled.events[0].value=-.08;s.coupled.events[1].value=.08
    if name=='breaker_open':s.coupled.events.append(CoupledEvent(time_s=.7,kind='generator_open',equipment_id='g2'))
    if name=='thermal_trip':
        from power_grid_env.generator_thermal import GeneratorThermalProtection
        s.coupled.generator_thermal=[GeneratorThermalProtection(generator_id='g2',time_constant_s=.1,trip_level=.008,initialization='equilibrium')]
    return StudySpec.model_validate(s.model_dump(mode='json'))


if __name__=='__main__':
    from power_grid_env.report import write_json
    for name in NAMES:write_json(Path('cases/studies/generator_excitation_'+name+'.json'),example(name).model_dump(mode='json'))
