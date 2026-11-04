"""Preregistered bounded long-start and independent node/motor/machine matrix."""
from power_grid_env.study_spec import StudySpec
from power_grid_env.build import NetBuilder
from power_grid_env.induction import InductionSettings,InductionMotor,InductionEvent
from power_grid_env.coupled_rms import CoupledSettings,CoupledEvent
from benchmarks.multibus_prestart_cases import example as prestart

LONG=('grid_long_start','gen_long_restart','gen_staggered_restart','gen_thermal_cooling')
SCALES=((64,4,1),(256,8,2),(512,32,4),(1024,16,4),(2000,8,4))


def long_case(name):
    if name not in LONG:raise ValueError('registered EX03 long case required')
    spec=prestart('grid_simultaneous' if name.startswith('grid') else 'gen_simultaneous');spec.id='ex03-'+name
    s=spec.coupled or spec.induction;s.duration_s=45.;s.step_s=.05;s.rtol=1e-10;s.atol=1e-12
    b=NetBuilder('tie','mesh',10.);b.node('section_a');b.node('section_b');b.line_z1('section_a','section_b',.1+.2j,line_id='ex03_tie');n=b.build();spec.network.lines+=n.lines;spec.network.terminals+=n.terminals
    for i,m in enumerate(s.motors):
        m.inertia_s=1.5+.25*i;m.connected=False
        if name=='gen_staggered_restart':m.contactor.pickup_delay_s=.2+.35*i
    event=CoupledEvent if spec.coupled else InductionEvent
    s.events=[event(time_s=.1,kind='motor_close',equipment_id=m.id) for m in s.motors]
    if not name.startswith('grid'):
        s.events += [event(time_s=t,kind='source_voltage',equipment_id=spec.network.sources[0].id,value=v) for t,v in ((12.,.4),(14.,1.))]
    if name=='gen_thermal_cooling':
        from power_grid_env.motor_thermal import MotorThermalProtection
        s.motors[0].thermal=MotorThermalProtection(time_constant_s=5.,trip_level=2.,initial_level=0.)
        s.events.append(event(time_s=15.,kind='motor_close',equipment_id=s.motors[0].id))
    return StudySpec.model_validate(spec.model_dump(mode='json'))


def scale_case(nodes,motors,generators):
    if (nodes,motors,generators) not in SCALES:raise ValueError('registered EX03 scale point required')
    b=NetBuilder(f'ex03-scale-{nodes}-{motors}-{generators}','Synthetic meshed fixed-total-VA dynamics',10.,s_base_va=10e6)
    for i in range(nodes):b.node(f'n{i}')
    b.source('n0',.05,.2)
    for i in range(1,nodes):
        b.line_z1(f'n{(i-1)//2}',f'n{i}',.02+.06j,line_id=f'l{i}')
        b.load(f'n{i}',1e5/(nodes-1),2e4/(nodes-1),model='constant_z',load_id=f'load{i}')
    for i in range(nodes//8,nodes,nodes//8):b.line_z1(f'n{i}',f'n{max(0,i-nodes//16)}',.03+.08j,line_id=f'tie{i}')
    template=prestart('gen_simultaneous').coupled.machines[0];machines=[]
    for i in range(generators):
        gid=f'g{i}';b.generator(f'n{1+i}',1e5/generators,mode='pv',v_set_pu=.995,q_min_var=-1e7,q_max_var=1e7,generator_id=gid)
        m=template.model_copy(deep=True);m.generator_id=gid;m.rated_va=10e6/generators;machines.append(m)
    group=[InductionMotor(id=f'm{i}',node_id=f'n{nodes//2+i*(nodes//2-1)//max(1,motors-1)}',rated_va=1e6/motors,rated_ll_v=10000.,stator_r_pu=.02,stator_x_pu=.1,rotor_r_pu=.015,rotor_x_pu=.1,magnetizing_x_pu=3.,inertia_s=.3,fan_torque_pu=.8,connected=False) for i in range(motors)]
    s=CoupledSettings(duration_s=6.,step_s=.2,motors=group,machines=machines,rtol=1e-9,atol=1e-11,events=[CoupledEvent(time_s=.1,kind='motor_close',equipment_id=m.id) for m in group])
    return StudySpec(id=b.network_id,study='coupled_rms',backend='scipy',network=b.build(),coupled=s)


def native_long():
    from power_grid_env.exciter_esst1a import ESST1APassport
    s=long_case('gen_long_restart');s.id='ex03-native-avr-start-restart';p=s.coupled
    p.duration_s=30.;p.step_s=.05;p.rtol=1e-11;p.atol=1e-13
    for m in p.motors:m.contactor=None;m.thermal=None
    for m in p.machines:m.exciter=None
    p.esst1a_exciters=[ESST1APassport(generator_id=p.machines[0].generator_id)]
    p.events=[CoupledEvent(time_s=t,kind=kind,equipment_id=m.id) for t,kind in ((.1,'motor_close'),(12.,'motor_open'),(14.,'motor_close')) for m in p.motors]
    return StudySpec.model_validate(s.model_dump(mode='json'))
