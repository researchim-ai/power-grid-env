"""Authored cases for two native regulators in closed network feedback."""
from benchmarks.generator_esst1a_cases import example as base
from power_grid_env.generator_underexcitation import UnderexcitationLimiter
from power_grid_env.coupled_rms import CoupledEvent,ESST1APortEvent
from power_grid_env.study_spec import StudySpec

NAMES=('hold','activation_recovery','saturation_recovery','fast_feedback','two_limiters_sensitive','fault_clear','field_trip','generator_open','takeover_priority','source_sag','motor_open','governor_step')

NEGATIVE=('two_limiters_sensitive',)
POSITIVE=tuple(n for n in NAMES if n not in NEGATIVE)

def example(name):
    if name not in NAMES:raise ValueError('unknown joint ESST1A/MNLEX2 case')
    s=base(name if name in ('fault_clear','field_trip','generator_open') else 'two_exciters' if name=='two_limiters_sensitive' else 'hold')
    s.id='generator-esst1a-joint-'+name;s.coupled.duration_s=4.;s.coupled.events=[]
    p=UnderexcitationLimiter(generator_id='g2',Q_0=.1,Radius=.15,K_M=2.,T_M=.1,K_F2=.05,T_F2=.2,MEL_MAX=.06)
    s.coupled.underexcitation_limiters=[p]
    def ref(t,value,gid='g2'):return CoupledEvent(time_s=t,kind='voltage_reference_step',equipment_id=gid,value=value)
    if name not in ('hold','field_trip','generator_open','fault_clear'):s.coupled.events=[ref(.2,-.04),ref(2.,.04)]
    if name=='saturation_recovery':
        p.K_M=8.;p.MEL_MAX=.002;s.coupled.events=[ref(.2,-.08),ref(2.,.08)]
    if name=='fast_feedback':
        p.K_F2=.23;p.T_F2=.07;p.T_M=.04;p.MEL_MAX=.01;s.coupled.events=[ref(.2,-.07),ref(2.,.07)]
    if name=='two_limiters_sensitive':
        s.coupled.underexcitation_limiters.append(p.model_copy(update=dict(generator_id='g',Radius=.5)))
        s.coupled.events += [ref(.3,-.03,'g'),ref(2.1,.03,'g')]
    if name=='generator_open':s.coupled.events=[CoupledEvent(time_s=.7,kind='generator_open',equipment_id='g2')]
    if name=='takeover_priority':
        s.coupled.events += [ESST1APortEvent(time_s=.8,equipment_id='g2',port='oel_ceiling_pu',value=.7),ESST1APortEvent(time_s=.8,equipment_id='g2',port='uel_field_floor_pu',value=1.8),ESST1APortEvent(time_s=2.5,equipment_id='g2',port='oel_ceiling_pu',value=4.),ESST1APortEvent(time_s=2.5,equipment_id='g2',port='uel_field_floor_pu',value=0.)]
    if name=='source_sag':s.coupled.events.append(CoupledEvent(time_s=.5,kind='source_voltage',equipment_id=s.network.sources[0].id,value=.85))
    if name=='motor_open':
        s.coupled.duration_s=.8;s.coupled.events=[ref(.2,-.04),CoupledEvent(time_s=.5,kind='motor_open',equipment_id=s.coupled.motors[0].id)]
    if name=='governor_step':s.coupled.events.append(CoupledEvent(time_s=.5,kind='governor_step',equipment_id='g2',value=.02))
    return StudySpec.model_validate(s.model_dump(mode='json'))

if __name__=='__main__':
    from pathlib import Path
    from power_grid_env.report import write_json
    for name in NAMES:write_json(Path('cases/studies/generator_esst1a_joint_'+name+'.json'),example(name).model_dump(mode='json'))
