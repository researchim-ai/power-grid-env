"""Authored EX-02 engineering-profile corpus; not measured equipment data."""
from benchmarks.generator_esst1a_joint_cases import example as joint
from power_grid_env.excitation_takeover import TakeoverOELPassport
from power_grid_env.rms_controls import IEEESTPassport
from power_grid_env.coupled_rms import CoupledEvent
from power_grid_env.study_spec import StudySpec

NAMES=('hold','overload_recovery','fault_clear','field_trip','generator_open','motor_open','governor_step','source_sag','takeover_priority','saturation_recovery','fast_feedback','two_limiters_sensitive','pss_limited','pss_voltage_gate','authority_limited')
MODAL=('avr','avr_pss','avr_oel','avr_pss_oel','avr_pss_oel_unstable')

def example(name):
    if name not in NAMES:raise ValueError('unknown EX02 case')
    base=name if name in ('fault_clear','field_trip','generator_open','motor_open','governor_step','source_sag','takeover_priority','saturation_recovery','fast_feedback','two_limiters_sensitive') else 'hold'
    s=joint(base);s.id='ex02-'+name
    s.coupled.takeover_oels=[TakeoverOELPassport(generator_id=p.generator_id,current_limit_pu=1.2,integral_gain=20.) for p in s.coupled.esst1a_exciters]
    for m in s.coupled.machines:
        if m.generator_id not in {p.generator_id for p in s.coupled.esst1a_exciters}:continue
        m.stabilizer_connection='external_ESST1A'
        m.stabilizer=IEEESTPassport(A1=.2,A2=.02,A3=.3,A4=.03,A5=.05,A6=.001,T1=.1,T2=.05,T3=.1,T4=.05,T5=1.,T6=1.,KS=2.,LSMIN=-.1,LSMAX=.1,VCL=.8,VCU=1.2)
    if name in ('overload_recovery','authority_limited'):
        s.coupled.events=[CoupledEvent(time_s=t,kind='voltage_reference_step',equipment_id='g2',value=v) for t,v in ((.2,.25),(2.2,-.25))]
    if name=='authority_limited':s.coupled.takeover_oels[0].minimum_field_pu=1.35
    if name=='pss_limited':
        s.coupled.rtol=1e-13;s.coupled.atol=1e-15
        for p in s.coupled.underexcitation_limiters:p.event_detection='conditioned_1e-8'
        m=next(m for m in s.coupled.machines if m.generator_id=='g2');m.stabilizer.LSMIN=-.0002;m.stabilizer.LSMAX=.0002
        s.coupled.events=[CoupledEvent(time_s=.2,kind='governor_step',equipment_id='g2',value=.1)]
    if name=='pss_voltage_gate':
        m=next(m for m in s.coupled.machines if m.generator_id=='g2');m.stabilizer.VCL=.97;m.stabilizer.VCU=1.02
        s.coupled.events=[CoupledEvent(time_s=t,kind='source_voltage',equipment_id=s.network.sources[0].id,value=v) for t,v in ((.45,.85),(1.1,1.))]
    return StudySpec.model_validate(s.model_dump(mode='json'))

def modal(name):
    if name not in MODAL:raise ValueError('unknown EX02 modal case')
    s=example('hold');s.id='ex02-modal-'+name;s.study='coupled_small_signal';s.coupled.events=[];s.coupled.underexcitation_limiters=[]
    if 'pss' not in name:
        for m in s.coupled.machines:m.stabilizer=None;m.stabilizer_connection='SEXS'
    if 'oel' not in name:s.coupled.takeover_oels=[]
    if name.endswith('_unstable'):
        s.coupled.esst1a_exciters[0].K_A=20.
        next(m for m in s.coupled.machines if m.stabilizer).stabilizer.KS=3000.
    return StudySpec.model_validate(s.model_dump(mode='json'))
