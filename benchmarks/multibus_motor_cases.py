"""Authored balanced multisection motor studies; no measured-data claim."""
from pathlib import Path
from benchmarks.automatic_motor_group import example as common_case
from power_grid_env.build import NetBuilder
from power_grid_env.study_spec import StudySpec

NAMES=('radial','mesh','clock_tap','mixed_sensing','thermal','manual_inhibit','base_change','frequency60','runup3s')


def example(name):
    if name not in NAMES:raise ValueError('unknown multibus motor case')
    base=common_case('grid_staggered')
    b=NetBuilder('multisection-motors-'+name,'Authored multisection motor benchmark',110.,
                 s_base_va=5e6 if name=='base_change' else 10e6,frequency_hz=60. if name=='frequency60' else 50.)
    for node,kv in [('hv',110.),('mv',10.),('section_a',10.),('section_b',10.),('lv_a',.4),('lv_b',.69)]:b.node(node,kv)
    b.source('hv',1.,5.)
    b.transformer('hv','mv',vector_group='YNyn0',kva=20000,hv_kv=110,lv_kv=10,x_pct=6.,r_pct=.8,transformer_id='t_grid')
    b.line_z1('mv','section_a',.05+.10j,line_id='feed_a')
    b.line_z1('mv','section_b',.08+.15j,line_id='feed_b')
    if name=='mesh':b.line_z1('section_a','section_b',.1+.2j,line_id='tie')
    b.transformer('section_a','lv_a',vector_group='Dyn11',kva=1000,hv_kv=10,lv_kv=.4,x_pct=6.,r_pct=1.,hv_ground='not_applicable',transformer_id='t_a')
    group='Dyn5' if name=='clock_tap' else 'YNyn0'
    b.transformer('section_b','lv_b',vector_group=group,kva=1500,hv_kv=10,lv_kv=.69,x_pct=5.,r_pct=.8,hv_ground='not_applicable' if group=='Dyn5' else 'solid',transformer_id='t_b')
    b.load('section_b',1e5,2e4,model='constant_z',load_id='background')
    network=b.build()
    for t in network.transformers:t.ppm_antifloat=0.
    if name=='clock_tap':
        network.transformers[1].windings[0].tap_pu=1.025
        network.transformers[2].windings[1].tap_pu=.975
    motors=[m.model_copy(deep=True) for m in base.induction.motors]
    for i,(m,node,v,va) in enumerate(zip(motors,['lv_a','section_a','lv_b'],[400.,10000.,690.],[1e5,5e5,1.5e5])):
        m.node_id=node;m.rated_ll_v=v;m.rated_va=va;m.contactor.sense_node_id=node
        m.contactor.dropout_delay_s=.08+.02*i;m.contactor.pickup_delay_s=.12+.1*i
    if name=='mixed_sensing':
        for m,node in zip(motors,['hv','section_b','lv_a']):m.contactor.sense_node_id=node
    if name=='thermal':
        template=common_case('grid_thermal_tie').induction.motors[0].thermal
        motors[0].thermal=template.model_copy(deep=True)
    settings=base.induction.model_copy(deep=True);settings.motors=motors;settings.operating_point=None
    settings.duration_s=3. if name=='runup3s' else 1.2;settings.rtol=1e-9;settings.atol=1e-11
    settings.events[0].equipment_id=network.sources[0].id;settings.events[1].equipment_id=network.sources[0].id
    if name=='manual_inhibit':
        from power_grid_env.induction import InductionEvent
        settings.events.append(InductionEvent(time_s=.55,kind='motor_open',equipment_id=motors[0].id))
    return StudySpec(id='multibus-motor-'+name,study='motor_start',backend='scipy',network=network,induction=settings)


if __name__=='__main__':
    from power_grid_env.report import write_json
    for name in NAMES:write_json(Path('cases/studies/multibus_motor_'+name+'.json'),example(name).model_dump(mode='json'))
