"""Authored cold-network OEL2C cases with explicit internal limiter startup."""
from benchmarks.generator_excitation_cases import example as old
from power_grid_env.generator_oel2c import OEL2CPassport
from power_grid_env.coupled_rms import CoupledEvent
from power_grid_env.study_spec import StudySpec

NAMES=('hold','voltage_step','recovery','inverse_ramp','limited_authority','two_limiters','saturated','motor_start','breaker_open','field_trip','stator_trip','UEL_coordination','four_machines')


def example(name):
    if name not in NAMES:raise ValueError('unknown OEL2C network case')
    if name=='four_machines':
        from benchmarks.multigen_cases import example as multi
        s=multi('hold4')
    else:s=old('hold')
    s.id='generator-oel2c-'+name;s.coupled.operating_point=None;s.coupled.duration_s=3.
    s.coupled.overexcitation_limiters=[];s.coupled.events=[]
    for motor in s.coupled.motors:motor.connected=False;motor.contactor=None;motor.thermal=None
    p=OEL2CPassport(generator_id='g2',rated_field_reaction_pu=1.5,ILimPu=.4)
    s.coupled.oel2c_limiters=[p]
    if name=='hold':s.coupled.duration_s=.5
    elif name=='motor_start':s.coupled.events=[CoupledEvent(time_s=.1,kind='motor_close',equipment_id=m.id) for m in s.coupled.motors]
    else:s.coupled.events=[CoupledEvent(time_s=.2,kind='voltage_reference_step',equipment_id='g2',value=.08)]
    if name in ('recovery','UEL_coordination'):
        s.coupled.events.append(CoupledEvent(time_s=1.2,kind='voltage_reference_step',equipment_id='g2',value=-.08))
    if name=='inverse_ramp':p.Sw1=True
    if name=='limited_authority':p.VOel1MinPu=-.001
    if name=='two_limiters':s.coupled.oel2c_limiters.append(p.model_copy(update={'generator_id':'g'}))
    if name=='saturated':
        for m in s.coupled.machines:m.saturation='quadratic';m.saturation_s10=.05;m.saturation_s12=.15
    if name=='breaker_open':s.coupled.events.append(CoupledEvent(time_s=.7,kind='generator_open',equipment_id='g2'))
    if name=='field_trip':
        from power_grid_env.generator_field_thermal import GeneratorFieldThermalProtection
        s.coupled.generator_field_thermal=[GeneratorFieldThermalProtection(generator_id='g2',rated_field_reaction_pu=1.5,time_constant_s=.2,trip_level=.3)]
    if name=='stator_trip':
        from power_grid_env.generator_thermal import GeneratorThermalProtection
        s.coupled.generator_thermal=[GeneratorThermalProtection(generator_id='g2',time_constant_s=.1,trip_level=.02,initialization='equilibrium')]
    if name=='UEL_coordination':
        from power_grid_env.generator_underexcitation import UnderexcitationLimiter
        s.coupled.underexcitation_limiters=[UnderexcitationLimiter(generator_id='g2',Q_0=.1,Radius=.15,K_M=2.,T_M=.1,K_F2=.05,T_F2=.2,MEL_MAX=.06,initialization='zero_output_startup')]
        s.coupled.events[0].value=-.04;s.coupled.events[1].time_s=.8;s.coupled.events[1].value=.20
    if name=='four_machines':
        s.coupled.duration_s=.6
        s.coupled.oel2c_limiters=[p.model_copy(update={'generator_id':m.generator_id}) for m in s.coupled.machines]
        s.coupled.events=[CoupledEvent(time_s=.1,kind='voltage_reference_step',equipment_id='g2',value=.08),CoupledEvent(time_s=.3,kind='voltage_reference_step',equipment_id='g4',value=.04)]
    return StudySpec.model_validate(s.model_dump(mode='json'))


if __name__=='__main__':
    from pathlib import Path
    from power_grid_env.report import write_json
    for name in NAMES:write_json(Path('cases/studies/generator_oel2c_'+name+'.json'),example(name).model_dump(mode='json'))
