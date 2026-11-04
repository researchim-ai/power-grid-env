"""Declared coincident-surface cases; current normalizations are unchanged."""
from benchmarks.generator_oel2c_selected_cases import example as previous
from power_grid_env.generator_oel2c import OEL2CPassport
from power_grid_env.coupled_rms import CoupledEvent
from power_grid_env.study_spec import StudySpec

CENTER=.7307761564612826  # Independent SI PV fixture current at field base 1.5.
HOT=1.370205293364905     # Same fixed PV fixture at field base .8.
NAMES=('scaled_and_release','release_and_timer_contact','hot_plateau','hot_plateau_saturated','double_selected','scaled_voltage_up','scaled_voltage_down','timer_contact_source_sag','roundoff_release_benign')


def data(kind):
    p=dict(generator_id='g2',rated_field_reaction_pu=1.5,model='OEL2C_Dynawo_equilibrium_guard_v2_SEXS_summation',initialization='selected_equilibrium',avr_reference_initialization='derive_from_operating_point_with_OEL',equilibrium_stability_policy='stationarity_only',equilibrium_boundary_policy='reject_sensitive_neighborhood',equilibrium_probe_radius_pu=2e-10,IResetPu=0.)
    if kind=='scaled':p.update(ITfPu=CENTER,FixedRu=0.,FixedRd=0.,Kzru=1.,Kru=0.,Krd=0.,IThOffPu=2.,equilibrium_reference_current_pu=1.4)
    elif kind=='contact':p.update(rated_field_reaction_pu=.8,FixedRd=0.,VInvMaxPu=0.,tMin=0.,Kzru=1.,Kru=0.,Krd=0.,IThOffPu=2.,equilibrium_reference_current_pu=1.4)
    elif kind=='roundoff':p.update(rated_field_reaction_pu=.8,FixedRd=0.,VInvMaxPu=.14,tFcl=.2,Kzru=.3,Kru=0.,Krd=0.,IThOffPu=2.,equilibrium_reference_current_pu=1.4)
    elif kind=='plateau':p.update(rated_field_reaction_pu=.8,FixedRd=0.,VInvMaxPu=.5,tFcl=.5)
    else:raise ValueError(kind)
    return p


def example(name):
    if name not in NAMES:raise ValueError(name)
    s=previous('inactive');s.id='generator-oel2c-boundary-'+name
    kind='roundoff' if name=='roundoff_release_benign' else 'plateau' if name.startswith('hot_plateau') else 'contact' if name in ('release_and_timer_contact','timer_contact_source_sag','roundoff_release_benign') else 'scaled'
    s.coupled.oel2c_limiters=[OEL2CPassport(**data(kind))]
    if name=='hot_plateau_saturated':
        for m in s.coupled.machines:m.saturation='quadratic';m.saturation_s10=.05;m.saturation_s12=.15
    if name=='double_selected':s.coupled.oel2c_limiters.append(OEL2CPassport(**(data('plateau')|dict(generator_id='g'))))
    if name in ('scaled_voltage_up','scaled_voltage_down'):
        s.coupled.duration_s=2.;s.coupled.events=[CoupledEvent(time_s=.2,kind='voltage_reference_step',equipment_id='g2',value=.01 if name.endswith('up') else -.01)]
    if name=='timer_contact_source_sag':
        s.coupled.duration_s=3.;s.coupled.events=[CoupledEvent(time_s=.2,kind='source_voltage',equipment_id=s.network.sources[0].id,value=.85)]
    return StudySpec.model_validate(s.model_dump(mode='json'))

NEGATIVE=('scaled_jump','release_ramp','timer_contact_sensitive','enable_clock_tie','reset_clock_tie','PID_memory_tie','arithmetic_relation_sensitive','missing_radius','radius_without_policy','missing_memory','unsupported_mode')

def negative(name):
    d=example('roundoff_release_benign' if name=='arithmetic_relation_sensitive' else 'release_and_timer_contact' if name=='timer_contact_sensitive' else 'scaled_and_release').model_dump(mode='json');p=d['coupled']['oel2c_limiters'][0]
    if name=='scaled_jump':p.update(FixedRu=-.1,FixedRd=.1)
    elif name=='release_ramp':p.update(Kru=.3,equilibrium_reference_current_pu=1.6)
    elif name=='timer_contact_sensitive':p.update(VInvMaxPu=3.,tMin=HOT**2-1,tFcl=1.5)
    elif name=='enable_clock_tie':p['equilibrium_reference_current_pu']=CENTER-p['current_comparison_tolerance_pu']
    elif name=='reset_clock_tie':p['IThOffPu']=1.4-CENTER-p['current_comparison_tolerance_pu']
    elif name=='PID_memory_tie':p.update(equilibrium_reference_current_pu=CENTER,equilibrium_pid_output_pu=-.08)
    elif name=='arithmetic_relation_sensitive':p.update(Kru=.3,equilibrium_reference_current_pu=1.6)
    elif name=='missing_radius':p['equilibrium_probe_radius_pu']=None
    elif name=='radius_without_policy':p['equilibrium_boundary_policy']='native_relations'
    elif name=='missing_memory':p['equilibrium_reference_current_pu']=None
    elif name=='unsupported_mode':p.update(initialization='inactive_equilibrium',avr_reference_initialization='unmodified')
    else:raise ValueError(name)
    return d

if __name__=='__main__':
    from pathlib import Path
    from power_grid_env.report import write_json
    for n in NAMES:write_json(Path('cases/studies/generator_oel2c_boundary_'+n+'.json'),example(n).model_dump(mode='json'))
