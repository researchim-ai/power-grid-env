"""Authored shared-network generator/motor cases with distinct power bases."""
from pathlib import Path
from benchmarks.multibus_loaded_cases import example as loaded
from power_grid_env.study_spec import StudySpec

NAMES=('hold2','governor2','exciter2','simultaneous3','permuted2','base2','dol2','hold4')
NEGATIVE=('q_limit_second','duplicate_node','missing_passport','unknown_control')


def example(name):
    if name not in (*NAMES,*NEGATIVE):raise ValueError('unknown multiple-generator case')
    spec=loaded('gen_lv_clock');data=spec.model_dump(mode='json');data['id']='multigen-'+name
    n,s=data['network'],data['coupled'];s['duration_s']=.8;s['events']=[]
    g=n['generators'][0];g['ll_v']=10000.;g['terminal_id']='g:section_b'
    t=next(t for t in n['terminals'] if t['equipment_id']==g['id']);t.update(id=g['terminal_id'],connectivity_node_id='section_b')
    import copy
    g2=copy.deepcopy(g);g2.update(id='g2',terminal_id='g2:lv_b',ll_v=690.,p_w=50000.,v_set_pu=.975)
    n['generators'].append(g2);t2=copy.deepcopy(t);t2.update(id=g2['terminal_id'],equipment_id='g2',connectivity_node_id='lv_b');n['terminals'].append(t2)
    p2=copy.deepcopy(s['machines'][0]);p2.update(generator_id='g2',rated_va=1000000.);p2['governor']['R']=.06;s['machines'].append(p2)
    if name in ('simultaneous3','hold4'):
        g3=copy.deepcopy(g);g3.update(id='g3',terminal_id='g3:section_a',p_w=75000.,v_set_pu=.9998);n['generators'].append(g3)
        t3=copy.deepcopy(t);t3.update(id=g3['terminal_id'],equipment_id='g3',connectivity_node_id='section_a');n['terminals'].append(t3)
        p3=copy.deepcopy(p2);p3.update(generator_id='g3',rated_va=1500000.);s['machines'].append(p3)
        s['events']=[dict(time_s=.2,kind='governor_step',equipment_id='g2',value=.025),dict(time_s=.2,kind='voltage_reference_step',equipment_id='g3',value=.01),dict(time_s=.55,kind='governor_step',equipment_id='g2',value=-.025),dict(time_s=.55,kind='voltage_reference_step',equipment_id='g3',value=-.01)]
    if name=='hold4':
        g4=copy.deepcopy(g);g4.update(id='g4',terminal_id='g4:mv',p_w=25000.,v_set_pu=1.0001);n['generators'].append(g4)
        t4=copy.deepcopy(t);t4.update(id=g4['terminal_id'],equipment_id='g4',connectivity_node_id='mv');n['terminals'].append(t4)
        p4=copy.deepcopy(p2);p4.update(generator_id='g4',rated_va=3000000.);s['machines'].append(p4);s['events']=[];s['duration_s']=.2
    if name in ('governor2','permuted2','base2'):
        s['events']=[dict(time_s=.2,kind='governor_step',equipment_id='g2',value=.03),dict(time_s=.55,kind='governor_step',equipment_id='g2',value=-.03)]
    if name=='exciter2':s['events']=[dict(time_s=.2,kind='voltage_reference_step',equipment_id='g2',value=.01),dict(time_s=.55,kind='voltage_reference_step',equipment_id='g2',value=-.01)]
    if name=='permuted2':s['machines'].reverse();n['generators'].reverse()
    if name=='base2':n['s_base_va']=5000000.
    if name=='dol2':
        s['operating_point']=None;s['duration_s']=3.
        for m in s['motors']:m['connected']=False
        s['events']=[dict(time_s=.1,kind='motor_close',equipment_id=m['id']) for m in s['motors']]
    if name=='q_limit_second':g2.update(q_min_var=-1.,q_max_var=1.)
    if name=='duplicate_node':t2['connectivity_node_id']='section_b';g2['ll_v']=10000.
    if name=='missing_passport':s['machines'].pop()
    if name=='unknown_control':s['events']=[dict(time_s=.2,kind='governor_step',equipment_id='missing',value=.01)]
    return StudySpec.model_validate(data)


if __name__=='__main__':
    from power_grid_env.report import write_json
    for name in (*NAMES,*NEGATIVE):write_json(Path('cases/studies/multigen_'+name+'.json'),example(name).model_dump(mode='json'))
