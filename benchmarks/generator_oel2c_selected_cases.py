"""Explicit neutral memory and active-set starts on the loaded PV network."""
from benchmarks.generator_oel2c_loaded_cases import example as base
from power_grid_env.generator_oel2c import OEL2CPassport
from power_grid_env.coupled_rms import CoupledEvent
from power_grid_env.study_spec import StudySpec

NAMES=('inactive','active','zero_cold_ramp_upper','zero_cold_ramp_interior','zero_hot_ramp_interior','deadband_memory','timer_lower_contact','timer_upper_contact','zero_error_zero_output','zero_error_PID_lower','zero_error_downstream_limit','zero_error_interior','zero_error_voltage_up','zero_error_voltage_down','hot_memory_release','two_selected')


def example(name):
    if name not in NAMES:raise ValueError(name)
    s=base('hold');s.id='generator-oel2c-selected-'+name;s.coupled.duration_s=.5;s.coupled.events=[];s.coupled.rtol=1e-11;s.coupled.atol=1e-13
    data=dict(generator_id='g2',model='OEL2C_Dynawo_equilibrium_guard_v2_SEXS_summation',rated_field_reaction_pu=1.5,initialization='selected_equilibrium',avr_reference_initialization='derive_from_operating_point_with_OEL',equilibrium_stability_policy='stationarity_only')
    if name in ('active','zero_hot_ramp_interior','timer_upper_contact','hot_memory_release'):data['rated_field_reaction_pu']=.8
    if name=='zero_cold_ramp_upper':data.update(Kru=0.,equilibrium_reference_current_pu=1.6)
    if name=='zero_cold_ramp_interior':data.update(Kru=0.,equilibrium_reference_current_pu=1.4)
    if name in ('zero_hot_ramp_interior','hot_memory_release'):data.update(Krd=0.,equilibrium_reference_current_pu=1.2)
    if name=='deadband_memory':data.update(rated_field_reaction_pu=.8,KFb=7.,equilibrium_reference_current_pu=1.4)
    if name=='timer_lower_contact':data.update(K2=.5,FixedRu=0.,VInvMinPu=-.1,tMin=-.1)
    if name=='timer_upper_contact':data.update(K2=1.,FixedRd=.5,VInvMaxPu=.5,tMax=1.)
    if name.startswith('zero_error'):
        # Deliberately set reference memory to the independent SI PV
        # fixture current, not a fitted current normalization.
        data.update(Kru=0.,Krd=0.,IThOffPu=2.,KpOel=.1,KiOel=.1,KdOel=.002,equilibrium_reference_current_pu=0.7307761564612826)
        data['equilibrium_pid_output_pu']={'zero_error_zero_output':0.,'zero_error_PID_lower':-.4,'zero_error_downstream_limit':-.3,'zero_error_interior':-.08,'zero_error_voltage_up':-.08,'zero_error_voltage_down':-.08}[name]
        if name=='zero_error_downstream_limit':data['VOel2MinPu']=-.1
    s.coupled.oel2c_limiters=[OEL2CPassport(**data)]
    if name in ('zero_error_voltage_up','zero_error_voltage_down'):
        s.coupled.duration_s=2.;s.coupled.events=[CoupledEvent(time_s=.2,kind='voltage_reference_step',equipment_id='g2',value=.01 if name.endswith('up') else -.01)]
    if name=='hot_memory_release':
        s.coupled.duration_s=3.;s.coupled.events=[CoupledEvent(time_s=.2,kind='source_voltage',equipment_id=s.network.sources[0].id,value=.85)]
    if name=='two_selected':s.coupled.oel2c_limiters.append(OEL2CPassport(**(data|dict(generator_id='g',Kru=0.,equilibrium_reference_current_pu=1.4))))
    return StudySpec.model_validate(s.model_dump(mode='json'))

if __name__=='__main__':
    from pathlib import Path
    from power_grid_env.report import write_json
    for n in NAMES:write_json(Path('cases/studies/generator_oel2c_selected_'+n+'.json'),example(n).model_dump(mode='json'))
