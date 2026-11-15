"""Authored fixed-field GENCLS/PV + thermal two-cage radial cases."""
import copy
from pathlib import Path
from itertools import product
from power_grid_env.study_spec import StudySpec
from power_grid_env.study_artifacts import read_json
ROOT=Path(__file__).resolve().parents[1]


def example(mutual=.02,temperatures=(90.,70.),fault=False):
    s=read_json(ROOT/'cases/two-cage-winding/passport-primary.json');c=s['coupled'];n=s['network'];m=c['motors'][0]
    c.update(winding_generator_profile='GENCLS_fixed_field',duration_s=.4,events=[],faults=[])
    c['operating_point'].update(winding_state='passport',thermal_state='equilibrium')
    m['rotor_mutual_x_pu']=mutual;m['winding_thermal']['initial_temperature_c']=list(temperatures);m['contactor']=None
    for r in c['network_automation']['relays']:r['pickup']=1e8
    n['terminals'].append(dict(id='g:n1',equipment_id='g',connectivity_node_id=m['node_id'],phases=['A','B','C']))
    n['generators']=[dict(id='g',terminal_id='g:n1',phases=['A','B','C'],mode='pv',p_w=20000.,q_var=0.,v_set_pu=.96,q_min_var=-150000.,q_max_var=150000.,ll_v=400.,
        provenance=dict(quality='assumed',source='owner://mixed-winding/v1: authored GENCLS/PV parameters and limits,not an OEM generator'))]
    c['machines']=[dict(model='GENCLS',generator_id='g',rated_va=100000.,inertia_s=3.,x_d_prime_pu=.25,damping_pu=5.)]
    if fault:c['faults']=[dict(id='f',node_id=m['node_id'],apply_s=.1,clear_s=.2,r_ohm=.1,x_ohm=.03)]
    return StudySpec.model_validate(s).model_dump(mode='json')


def jobs():
    for mutual,temp,fault in product([0.,.02],[(20.,20.),(90.,70.)],[False,True]):
        id=f'mutual{mutual:.2f}-T{int(temp[0])}-'+('fault' if fault else 'hold')
        yield dict(id=id,study=example(mutual,temp,fault),fault=fault)
