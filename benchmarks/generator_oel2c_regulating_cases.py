"""Declared current-regulating starts; fixed field bases and specified AVR inputs."""
from benchmarks.generator_oel2c_loaded_cases import example as previous
from power_grid_env.generator_oel2c import OEL2CPassport
from power_grid_env.coupled_rms import CoupledEvent
from power_grid_env.study_spec import StudySpec

NAMES=('hold','voltage_up','voltage_down','governor_step','motor_open','two_regulators','mixed_modes','saturated','different_reference','scaled_input','timer_upper_bound','field_memory')


def passport(gid='g2',**changes):
    data=dict(generator_id=gid,rated_field_reaction_pu=.8,model='OEL2C_Dynawo_equilibrium_guard_v2_SEXS_summation',initialization='current_regulating_equilibrium',avr_reference_initialization='specified_for_current_regulation',specified_avr_reference_pu=1.10,regulating_voltage_bounds_pu=(.85,1.10),ILimPu=1.3,KiOel=.1,KpOel=.1,KdOel=.002)
    return OEL2CPassport(**(data|changes))


def example(name):
    if name not in NAMES:raise ValueError('unknown regulating case')
    s=previous('hold');s.id='generator-oel2c-regulating-'+name;s.coupled.duration_s=3.;s.coupled.events=[];s.coupled.rtol=1e-10;s.coupled.atol=1e-12
    p=passport();s.coupled.oel2c_limiters=[p]
    if name=='hold':s.coupled.duration_s=.5
    if name in ('voltage_up','voltage_down'):s.coupled.events=[CoupledEvent(time_s=.2,kind='voltage_reference_step',equipment_id='g2',value=.01 if name=='voltage_up' else -.01)]
    if name=='governor_step':s.coupled.events=[CoupledEvent(time_s=.2,kind='governor_step',equipment_id='g2',value=.01)]
    if name=='motor_open':s.coupled.events=[CoupledEvent(time_s=.2,kind='motor_open',equipment_id=s.coupled.motors[-1].id)]
    if name=='two_regulators':s.coupled.oel2c_limiters.append(passport('g',rated_field_reaction_pu=.9,specified_avr_reference_pu=1.15))
    if name=='mixed_modes':
        from power_grid_env.generator_oel2c import OEL2CPassport
        s.coupled.oel2c_limiters.append(OEL2CPassport(generator_id='g',rated_field_reaction_pu=1.5,initialization='inactive_equilibrium'))
    if name=='saturated':
        for m in s.coupled.machines:m.saturation='quadratic';m.saturation_s10=.05;m.saturation_s12=.15
    if name=='different_reference':p.specified_avr_reference_pu=1.14
    if name=='scaled_input':p.KScale=.9;p.KAct=1.1
    if name=='timer_upper_bound':p.tMax=.5
    if name=='field_memory':
        from power_grid_env.generator_field_thermal import GeneratorFieldThermalProtection
        s.coupled.generator_field_thermal=[GeneratorFieldThermalProtection(generator_id='g2',rated_field_reaction_pu=.8,time_constant_s=.3,trip_level=3.,initialization='equilibrium')]
    return StudySpec.model_validate(s.model_dump(mode='json'))


if __name__=='__main__':
    from pathlib import Path
    from power_grid_env.report import write_json
    for n in NAMES:write_json(Path('cases/studies/generator_oel2c_regulating_'+n+'.json'),example(n).model_dump(mode='json'))
