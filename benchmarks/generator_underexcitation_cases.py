"""Authored MNLEX2 network cases; coefficients are illustrative, not OEM data."""
from benchmarks.generator_excitation_cases import example as excitation
from power_grid_env.generator_underexcitation import UnderexcitationLimiter
from power_grid_env.coupled_rms import CoupledEvent
from power_grid_env.study_spec import StudySpec

NAMES=('hold','activation','recovery','limited_authority','two_limiters','saturated','breaker_open','field_trip','motor_start','zero_output_startup','oel_coordination')


def example(name):
    if name not in NAMES:raise ValueError('unknown MNLEX2 case')
    s=excitation('motor_start' if name=='motor_start' else 'hold');s.id='generator-underexcitation-'+name
    if name!='oel_coordination':s.coupled.overexcitation_limiters=[]
    p=UnderexcitationLimiter(generator_id='g2',Q_0=.1,Radius=.15,K_M=2.,T_M=.1,K_F2=.05,T_F2=.2,MEL_MAX=.06)
    if name=='limited_authority':p.MEL_MAX=.001
    s.coupled.underexcitation_limiters=[p]
    if name not in ('hold','motor_start','zero_output_startup'):
        s.coupled.events=[CoupledEvent(time_s=.2,kind='voltage_reference_step',equipment_id='g2',value=-.04)]
    if name=='oel_coordination':s.coupled.events.append(CoupledEvent(time_s=.8,kind='voltage_reference_step',equipment_id='g2',value=.12))
    if name=='recovery':s.coupled.events.append(CoupledEvent(time_s=1.2,kind='voltage_reference_step',equipment_id='g2',value=.04))
    if name=='two_limiters':s.coupled.underexcitation_limiters.append(p.model_copy(update={'generator_id':'g'}))
    if name=='saturated':
        for m in s.coupled.machines:m.saturation='quadratic';m.saturation_s10=.05;m.saturation_s12=.15
    if name=='breaker_open':s.coupled.events.append(CoupledEvent(time_s=.7,kind='generator_open',equipment_id='g2'))
    if name=='field_trip':
        from power_grid_env.generator_field_thermal import GeneratorFieldThermalProtection
        s.coupled.generator_field_thermal=[GeneratorFieldThermalProtection(generator_id='g2',rated_field_reaction_pu=1.,time_constant_s=.2,trip_level=.3)]
    if name=='zero_output_startup':
        p.initialization='zero_output_startup';s.coupled.operating_point=None
        for m in s.coupled.motors:m.connected=False
    return StudySpec.model_validate(s.model_dump(mode='json'))


if __name__=='__main__':
    from pathlib import Path
    from power_grid_env.report import write_json
    for name in NAMES:write_json(Path('cases/studies/generator_underexcitation_'+name+'.json'),example(name).model_dump(mode='json'))
