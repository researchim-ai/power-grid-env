"""Loaded two-generator ESST1A studies; ports have explicit source units."""
from benchmarks.generator_oel2c_selected_cases import example as base
from power_grid_env.exciter_esst1a import ESST1APassport
from power_grid_env.coupled_rms import CoupledEvent,ESST1APortEvent
from power_grid_env.study_spec import StudySpec

NAMES=('hold','reference_up','reference_down','oel_takeover','uel_error','uel_field','gate_priority','supplementary_field','regulator_reset','current_limit','two_exciters','source_sag','fault_clear','field_trip','generator_open','uel_summation')


def example(name):
    if name not in NAMES:raise ValueError(name)
    s=base('inactive');s.id='generator-esst1a-'+name;s.coupled.oel2c_limiters=[]
    s.coupled.duration_s=2.;s.coupled.events=[]
    for p in s.coupled.machines:
        if p.generator_id=='g2':p.exciter=None
    p=ESST1APassport(generator_id='g2');s.coupled.esst1a_exciters=[p]
    def port(time,key,value):return ESST1APortEvent(time_s=time,equipment_id='g2',port=key,value=value)
    def ref(time,value):return CoupledEvent(time_s=time,kind='voltage_reference_step',equipment_id='g2',value=value)
    if name in ('reference_up','reference_down'):s.coupled.events=[ref(.2,.02 if name.endswith('up') else -.02)]
    if name=='oel_takeover':s.coupled.events=[port(.2,'oel_ceiling_pu',.7),port(1.,'oel_ceiling_pu',4.)]
    if name=='uel_error':s.coupled.events=[port(.2,'uel_error_floor_pu',.18),port(1.,'uel_error_floor_pu',-.2)]
    if name=='uel_field':s.coupled.events=[port(.2,'uel_field_floor_pu',1.8),port(1.,'uel_field_floor_pu',0.)]
    if name=='gate_priority':s.coupled.events=[port(.2,'uel_field_floor_pu',1.8),port(.2,'oel_ceiling_pu',.7),port(1.,'uel_field_floor_pu',0.),port(1.,'oel_ceiling_pu',4.)]
    if name=='supplementary_field':s.coupled.events=[port(.2,'supplementary_field_pu',.3),port(1.,'supplementary_field_pu',0.)]
    if name=='regulator_reset':p.V_AMAX=1.2;s.coupled.events=[ref(.2,.3),ref(1.,-.4)]
    if name=='current_limit':p.I_LR=.5;s.coupled.events=[ref(.2,.02)]
    if name=='two_exciters':
        next(p for p in s.coupled.machines if p.generator_id=='g').exciter=None
        s.coupled.esst1a_exciters.append(ESST1APassport(generator_id='g'));s.coupled.events=[ref(.2,.02)]
    if name=='source_sag':s.coupled.events=[CoupledEvent(time_s=.2,kind='source_voltage',equipment_id=s.network.sources[0].id,value=.85)]
    if name=='fault_clear':
        from power_grid_env.coupled_rms import CoupledFault
        p.K_C=0.
        s.coupled.faults=[CoupledFault(id='f',node_id=next(t.connectivity_node_id for t in s.network.terminals if t.id==s.network.generators[1].terminal_id),apply_s=.2,clear_s=.3,r_ohm=1.,x_ohm=5.)]
    if name=='field_trip':
        from power_grid_env.generator_field_thermal import GeneratorFieldThermalProtection
        p.K_C=0.
        s.coupled.generator_field_thermal=[GeneratorFieldThermalProtection(generator_id='g2',rated_field_reaction_pu=1.,time_constant_s=.2,trip_level=.3)]
    if name=='generator_open':s.coupled.events=[CoupledEvent(time_s=.2,kind='generator_open',equipment_id='g2')]
    if name=='uel_summation':
        from benchmarks.generator_oel2c_loaded_cases import example as uel_base
        s.coupled.underexcitation_limiters=uel_base('UEL_coordination').coupled.underexcitation_limiters
        s.coupled.duration_s=4.;s.coupled.events=[ref(.2,-.04),ref(2.,.04)]
    return StudySpec.model_validate(s.model_dump(mode='json'))

if __name__=='__main__':
    from pathlib import Path
    from power_grid_env.report import write_json
    for n in NAMES:write_json(Path('cases/studies/generator_esst1a_'+n+'.json'),example(n).model_dump(mode='json'))
