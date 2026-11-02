"""Explicit rotor heat memory and zero impressed-field-voltage trip experiments."""
from pathlib import Path
from benchmarks.generator_excitation_cases import example as excitation
from power_grid_env.generator_field_thermal import GeneratorFieldThermalProtection
from power_grid_env.coupled_rms import CoupledEvent
from power_grid_env.study_spec import StudySpec

NAMES=('cold','warm','equilibrium_hold','OEL_coordination','saturated','manual_stator_open','two_relays')


def example(name):
    if name not in NAMES:raise ValueError('unknown field thermal case')
    s=excitation('voltage_step' if name=='OEL_coordination' else 'hold')
    s.id='generator-field-'+name
    if name!='OEL_coordination':s.coupled.overexcitation_limiters=[]
    p=GeneratorFieldThermalProtection(generator_id='g2',rated_field_reaction_pu=1.,time_constant_s=.1,trip_level=.8)
    if name=='warm':p.initial_level=.7
    if name=='equilibrium_hold':p.trip_level=2.;p.initialization='equilibrium'
    if name=='OEL_coordination':p.trip_level=2.;p.initialization='equilibrium';p.time_constant_s=.2
    if name=='saturated':
        for m in s.coupled.machines:m.saturation='quadratic';m.saturation_s10=.05;m.saturation_s12=.15
    if name=='manual_stator_open':
        s.coupled.events=[CoupledEvent(time_s=.005,kind='generator_open',equipment_id='g2')];p.trip_level=.2
    s.coupled.generator_field_thermal=[p]
    if name=='two_relays':s.coupled.generator_field_thermal.append(p.model_copy(update={'generator_id':'g'}))
    return StudySpec.model_validate(s.model_dump(mode='json'))


if __name__=='__main__':
    from power_grid_env.report import write_json
    for name in NAMES:write_json(Path('cases/studies/generator_field_'+name+'.json'),example(name).model_dump(mode='json'))
