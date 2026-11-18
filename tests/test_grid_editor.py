import copy
import pytest
from benchmarks.grid_editor import compile_draft,solve


def draft():
    return dict(schema='power-grid-env.grid-editor/v1',nominal_ll_v=400.,source_node='n0',source_r_ohm=.006,source_x_ohm=.024,duration_s=.2,step_s=.002,nodes=[dict(id='n0',x=100.,y=100.),dict(id='n1',x=400.,y=100.),dict(id='n2',x=700.,y=100.)],lines=[dict(id='l1',from_node='n0',to_node='n1',length_m=40.,r_ohm_km=.4,x_ohm_km=.08,rating_a=180.),dict(id='l2',from_node='n1',to_node='n2',length_m=50.,r_ohm_km=.4,x_ohm_km=.08,rating_a=180.)],motors=[dict(id='m1',node_id='n2',rated_kva=75.,inertia_s=.3,fan_torque_pu=.7,stator_r_pu=.02,stator_x_pu=.1,rotor_r_pu=.015,rotor_x_pu=.1,magnetizing_x_pu=3.,start_s=0.)])


def test_line_si_scaling_and_motor_passport():
    d,s,g=compile_draft(draft())
    assert s.network.lines[0].r_ohm[0][0]==pytest.approx(.016)
    assert s.network.lines[0].x_ohm[0][0]==pytest.approx(.0032)
    assert s.induction.motors[0].rated_va==75000
    assert s.induction.motors[0].thermal is None
    assert len(g['nodes'])==3


def test_solve_actual_channels_and_empty_intermediate_bus():
    result=solve(draft());s=result['designs'][0]['studies'][0]
    assert s['time_s'][-1]==.2
    assert set(s['series']['nodes'])=={'n0','n1','n2'}
    assert max(s['series']['motors']['m1']['current_a'])>0
    assert s['series']['motors']['m1']['speed_pu'][-1]>0
    assert min(s['series']['branches']['l1']['loss_kw'])>=0
    assert 'engineering_status' not in result['designs'][0]


def test_staged_start_has_both_event_sides():
    d=draft();d['motors'][0]['start_s']=.1
    s=solve(d)['designs'][0]['studies'][0]
    pairs=[i for i,t in enumerate(s['time_s']) if t==.1]
    assert [s['sample_side'][i] for i in pairs]==['pre_event','post_event']
    assert [s['series']['motors']['m1']['connected'][i] for i in pairs]==[0,1]


@pytest.mark.parametrize('change',[
 lambda d:d['lines'][1].update(to_node='n0'),
 lambda d:d['nodes'].append(dict(id='n3',x=0.,y=0.)),
 lambda d:d['motors'][0].update(node_id='missing'),
 lambda d:d['motors'][0].update(start_s=.2),
 lambda d:d.update(nominal_ll_v=float('nan')),
 lambda d:d.update(script='bad'),
 lambda d:d['motors'][0].update(id='source'),
 lambda d:d['lines'][0].update(r_ohm_km=0.),
])
def test_invalid_draft_rejected_before_physics(change):
    d=copy.deepcopy(draft());change(d)
    with pytest.raises(ValueError):compile_draft(d)


def test_load_power_is_voltage_squared_and_source_balance():
    d=draft();d['loads']=[dict(id='z1',node_id='n1',p_kw=20.,q_kvar=5.)]
    result=solve(d);s=result['designs'][0]['studies'][0]['series']
    for i in (0,-1):
        z=s['loads']['z1'];v=z['bus_voltage_pu'][i]
        assert z['p_kw'][i]==pytest.approx(20*v*v,abs=3e-5)
        assert z['q_kvar'][i]==pytest.approx(5*v*v,abs=1e-5)
        total=z['p_kw'][i]+sum(m['p_kw'][i] for m in s['motors'].values())+sum(b['loss_kw'][i] for b in s['branches'].values())
        assert s['source']['p_kw'][i]==pytest.approx(total,abs=3e-6)


def test_double_cage_passport_and_actual_solution():
    d=draft();d['motors'][0].update(model='double_cage',rotor2_r_pu=.04,rotor2_x_pu=.15)
    _,study,_=compile_draft(d)
    assert study.induction.motors[0].model=='double_cage'
    s=solve(d)['designs'][0]['studies'][0]
    assert s['series']['motors']['m1']['speed_pu'][-1]>0


def test_thermal_trip_opens_contactor_and_preserves_coast():
    d=draft();d['motors'][0].update(thermal_enabled=True,thermal_tau_s=.1,thermal_trip=.5)
    s=solve(d)['designs'][0]['studies'][0];m=s['series']['motors']['m1']
    assert 'thermal_level' in m
    i=next(i for i in range(1,len(m['connected'])) if m['connected'][i-1]==1 and m['connected'][i]==0)
    assert s['sample_side'][i]=='post_event'
    assert m['current_a'][i]==0
    assert m['speed_pu'][i]==pytest.approx(m['speed_pu'][i-1],abs=1e-6)


def test_source_sag_and_recovery_change_recorded_voltage():
    d=draft();d['events']=[dict(time_s=.05,kind='source_voltage',equipment_id='source',value=.6),dict(time_s=.15,kind='source_voltage',equipment_id='source',value=1.)]
    s=solve(d)['designs'][0]['studies'][0];times=s['time_s'];v=s['series']['nodes']['n0']['voltage_pu']
    i=next(i for i,t in enumerate(times) if t==.05 and s['sample_side'][i]=='post_event')
    assert v[i]<v[i-1]*.7
    assert v[-1]>v[i]*1.3


@pytest.mark.parametrize('events',[
 [dict(time_s=.1,kind='source_voltage',equipment_id='source',value=None)],
 [dict(time_s=.1,kind='motor_open',equipment_id='m1',value=1.)],
 [dict(time_s=.1,kind='source_voltage',equipment_id='bad',value=.7)],
 [dict(time_s=.1,kind='motor_open',equipment_id='m1',value=None),dict(time_s=.1,kind='motor_close',equipment_id='m1',value=None)],
])
def test_invalid_scenario_commands_rejected(events):
    d=draft();d['events']=events
    with pytest.raises(ValueError):compile_draft(d)


def multivoltage_draft(group='Dyn11'):
    d=draft();d.update(schema='power-grid-env.grid-editor/v3',profile='multivoltage_motor_start',nominal_ll_v=10000.,source_node='hv',source_r_ohm=.05,source_x_ohm=.2)
    for n in d['nodes']:n['nominal_ll_v']=400.
    d['nodes'].append(dict(id='hv',x=0.,y=100.,nominal_ll_v=10000.))
    d['transformers']=[dict(id='tr1',from_node='hv',to_node='n0',rated_kva=400.,r_percent=.8,x_percent=4.,tap_primary=1.,tap_secondary=1.,vector_group=group)]
    return d


@pytest.mark.parametrize('group',['Yy0','Dyn11','Dyn1','Dd0'])
def test_transformer_multivoltage_real_flows_and_power_balance(group):
    d=multivoltage_draft(group);result=solve(d);design=result['designs'][0];s=design['studies'][0]['series'];tr=s['branches']['tr1']
    assert result['profile']=='multivoltage_motor_start'
    assert design['motors'][0]['rated_ll_v']==400
    assert min(tr['loss_kw'])>=-1e-6
    for i in (0,-1):
        assert tr['p_kw'][i]-tr['receiving_p_kw'][i]==pytest.approx(tr['loss_kw'][i],abs=2e-6)
        assert tr['receiving_current_a'][i]/tr['current_a'][i]==pytest.approx(25,rel=2e-6)
        assert s['source']['p_kw'][i]==pytest.approx(sum(m['p_kw'][i] for m in s['motors'].values())+sum(b['loss_kw'][i] for b in s['branches'].values()),abs=4e-6)


def test_transformer_vector_group_and_fixed_tap_affect_solution():
    d=multivoltage_draft('Yy0');_,s,g=compile_draft(d)
    from power_grid_env.rms_study import transformer_parameters
    from power_grid_env.topology import compile_topology
    p=transformer_parameters(s.network.transformers[0],compile_topology(s.network),s.network.s_base_va)
    assert p['r']==pytest.approx(.008*1e6/400e3)
    assert p['x']==pytest.approx(.04*1e6/400e3)
    d['transformers'][0]['tap_primary']=1.1
    _,s,g=compile_draft(d);p=transformer_parameters(s.network.transformers[0],compile_topology(s.network),s.network.s_base_va)
    assert p['tap']==pytest.approx(1.1)
    base=solve(multivoltage_draft())['designs'][0]['studies'][0]['series']['nodes']['n0']['voltage_pu'][0]
    tapped=solve(d)['designs'][0]['studies'][0]['series']['nodes']['n0']['voltage_pu'][0]
    assert tapped<base


def test_different_voltage_cable_and_wrong_profile_rejected():
    d=multivoltage_draft();d['nodes'][1]['nominal_ll_v']=690.
    with pytest.raises(ValueError,match='Cable cannot'):compile_draft(d)
    d=multivoltage_draft();d['profile']='lv_motor_start'
    with pytest.raises(ValueError,match='profile'):compile_draft(d)


def test_clock_changes_angle_by_thirty_degrees_not_just_label():
    s0=solve(multivoltage_draft('Yy0'))['designs'][0]['studies'][0]['series']
    s11=solve(multivoltage_draft('Dyn11'))['designs'][0]['studies'][0]['series']
    assert s11['nodes']['n0']['angle_deg'][0]-s0['nodes']['n0']['angle_deg'][0]==pytest.approx(30.,abs=2e-6)
    assert s11['nodes']['n0']['voltage_pu'][0]==pytest.approx(s0['nodes']['n0']['voltage_pu'][0],abs=1e-6)


def test_reverse_transformer_supply_has_correct_side_currents_and_balance():
    d=multivoltage_draft();d.update(source_node='n0',nominal_ll_v=400.,source_r_ohm=.006,source_x_ohm=.024)
    d['loads']=[dict(id='zhv',node_id='hv',p_kw=20.,q_kvar=5.)]
    design=solve(d)['designs'][0];b=next(b for b in design['graph']['branches'] if b['corridor_id']=='tr1')
    assert b['parent_node']=='n0'
    s=design['studies'][0]['series'];tr=s['branches']['tr1']
    assert tr['p_kw'][-1]>0
    assert tr['current_a'][-1]/tr['receiving_current_a'][-1]==pytest.approx(25,rel=2e-5)
    assert tr['p_kw'][-1]-tr['receiving_p_kw'][-1]==pytest.approx(tr['loss_kw'][-1],abs=2e-6)


def test_secondary_fixed_tap_changes_voltage_and_current_ratio():
    d=multivoltage_draft();d['transformers'][0]['tap_secondary']=1.05
    s=solve(d)['designs'][0]['studies'][0]['series'];tr=s['branches']['tr1']
    assert tr['receiving_current_a'][-1]/tr['current_a'][-1]==pytest.approx(25/1.05,rel=2e-5)
