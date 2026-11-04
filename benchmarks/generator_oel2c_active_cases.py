"""Authored active PID-authority-limited OEL2C operating points and disturbances."""
from benchmarks.generator_oel2c_loaded_cases import example as previous
from power_grid_env.generator_oel2c import OEL2CPassport
from power_grid_env.coupled_rms import CoupledEvent
from power_grid_env.study_spec import StudySpec

NAMES=('hold','voltage_up','voltage_down','inverse_ramp','two_limiters','mixed_modes','saturated','timer_upper_bound','pid_authority','leadlag2_authority','field_trip','field_equilibrium')


def example(name):
    if name not in NAMES:raise ValueError('unknown active OEL2C case')
    s=previous('hold');s.id='generator-oel2c-active-'+name;s.coupled.duration_s=2.;s.coupled.events=[]
    p=OEL2CPassport(generator_id='g2',rated_field_reaction_pu=.8,initialization='active_limited_equilibrium',avr_reference_initialization='derive_from_operating_point_with_OEL')
    s.coupled.oel2c_limiters=[p]
    if name in ('hold','mixed_modes','field_equilibrium'):s.coupled.duration_s=.5
    if name=='voltage_up':s.coupled.events=[CoupledEvent(time_s=.2,kind='voltage_reference_step',equipment_id='g2',value=.04)]
    if name=='voltage_down':
        s.coupled.duration_s=5.
        s.coupled.events=[CoupledEvent(time_s=.2,kind='voltage_reference_step',equipment_id='g2',value=-.25)]
    if name=='inverse_ramp':p.Sw1=True
    if name=='two_limiters':s.coupled.oel2c_limiters.append(p.model_copy(update={'generator_id':'g','tEn':.25}))
    if name=='mixed_modes':s.coupled.oel2c_limiters.append(p.model_copy(update={'generator_id':'g','rated_field_reaction_pu':1.5,'initialization':'inactive_equilibrium','avr_reference_initialization':'unmodified'}))
    if name=='saturated':
        for m in s.coupled.machines:m.saturation='quadratic';m.saturation_s10=.05;m.saturation_s12=.15
    if name=='timer_upper_bound':p.rated_field_reaction_pu=.5
    if name=='pid_authority':p.VOel3MinPu=-.05
    if name=='leadlag2_authority':p.VOel2MinPu=-.03
    if name in ('field_trip','field_equilibrium'):
        from power_grid_env.generator_field_thermal import GeneratorFieldThermalProtection
        s.coupled.generator_field_thermal=[GeneratorFieldThermalProtection(generator_id='g2',rated_field_reaction_pu=p.rated_field_reaction_pu,time_constant_s=.3,trip_level=.8 if name=='field_trip' else 3.,initialization='specified' if name=='field_trip' else 'equilibrium')]
    return StudySpec.model_validate(s.model_dump(mode='json'))


if __name__=='__main__':
    from pathlib import Path
    from power_grid_env.report import write_json
    for n in NAMES:write_json(Path('cases/studies/generator_oel2c_active_'+n+'.json'),example(n).model_dump(mode='json'))
