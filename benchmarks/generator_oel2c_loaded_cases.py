"""Loaded inactive-OEL2C starts; authored perturbations, never fitted settings."""
from benchmarks.generator_excitation_cases import example as previous
from power_grid_env.generator_oel2c import OEL2CPassport
from power_grid_env.coupled_rms import CoupledEvent
from power_grid_env.study_spec import StudySpec

NAMES=('hold','voltage_step','recovery','inverse_ramp','two_limiters','saturated','timer_lower_bound','UEL_coordination','no_reset_clock','zero_enable_delay','scaled_input','below_thermal_limit')


def example(name):
    if name not in NAMES:raise ValueError('unknown loaded OEL2C case')
    s=previous('hold');s.id='generator-oel2c-loaded-'+name
    s.coupled.overexcitation_limiters=[];s.coupled.duration_s=3.;s.coupled.events=[]
    for m in s.coupled.motors:m.contactor=None;m.thermal=None
    p=OEL2CPassport(generator_id='g2',rated_field_reaction_pu=1.5,IInstPu=1.2,initialization='inactive_equilibrium')
    s.coupled.oel2c_limiters=[p]
    if name=='hold':s.coupled.duration_s=.5
    else:s.coupled.events=[CoupledEvent(time_s=.2,kind='voltage_reference_step',equipment_id='g2',value=.08)]
    if name=='recovery':s.coupled.events.append(CoupledEvent(time_s=1.4,kind='voltage_reference_step',equipment_id='g2',value=-.08))
    if name=='inverse_ramp':p.Sw1=True
    if name=='two_limiters':s.coupled.oel2c_limiters.append(p.model_copy(update={'generator_id':'g','tOff':.25}))
    if name=='saturated':
        for m in s.coupled.machines:m.saturation='quadratic';m.saturation_s10=.05;m.saturation_s12=.15
    if name=='timer_lower_bound':p.tMin=-.1
    if name=='no_reset_clock':p.IThOffPu=2.
    if name=='zero_enable_delay':p.tEn=0.
    if name=='scaled_input':p.KScale=.8;p.KAct=1.2
    if name=='below_thermal_limit':p.rated_field_reaction_pu=4.
    if name=='UEL_coordination':
        from power_grid_env.generator_underexcitation import UnderexcitationLimiter
        s.coupled.underexcitation_limiters=[UnderexcitationLimiter(generator_id='g2',Q_0=.1,Radius=.15,K_M=2.,T_M=.1,K_F2=.05,T_F2=.2,MEL_MAX=.06)]
        s.coupled.events[0].value=-.04
        s.coupled.events.append(CoupledEvent(time_s=.8,kind='voltage_reference_step',equipment_id='g2',value=.20))
    return StudySpec.model_validate(s.model_dump(mode='json'))


if __name__=='__main__':
    from pathlib import Path
    from power_grid_env.report import write_json
    for n in NAMES:write_json(Path('cases/studies/generator_oel2c_loaded_'+n+'.json'),example(n).model_dump(mode='json'))
