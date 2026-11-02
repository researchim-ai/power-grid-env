"""Preregistered loaded motor equilibria and controlled multibus generators."""
from pathlib import Path
from benchmarks.multibus_motor_cases import example as grid_case
from benchmarks.automatic_motor_group import example as common_case
from power_grid_env.study_spec import StudySpec

NAMES=('grid_hold','grid_sag','grid_mesh_tap','grid_warm_thermal','gen_hold','gen_sag','gen_cold_start','gen_lv_clock','gen_saturated')
NEGATIVE=('generator_q_limit','cold_generator_q_limit','motor_high_speed_root')


def example(name):
    if name not in (*NAMES,*NEGATIVE):raise ValueError('unknown loaded multibus case')
    base=grid_case('mesh' if name=='grid_mesh_tap' else 'clock_tap' if name=='gen_lv_clock' else 'radial')
    base.id='multibus-loaded-'+name
    base.induction.operating_point=common_case('grid_staggered').induction.operating_point.model_copy(deep=True)
    if name.endswith('_hold'):base.induction.events=[]
    if name=='grid_mesh_tap':base.network.transformers[1].windings[1].tap_pu=1.025
    if name=='grid_warm_thermal':
        from power_grid_env.induction import InductionEvent
        from power_grid_env.motor_thermal import MotorThermalProtection
        base.induction.motors[0].thermal=MotorThermalProtection(time_constant_s=.1,trip_level=2.,initial_level=0.)
        base.induction.operating_point.thermal_state='equilibrium'
        base.induction.events.append(InductionEvent(time_s=.85,kind='motor_close',equipment_id='m'))
    if name=='motor_high_speed_root':
        base.induction.motors[0].fan_torque_pu=20.
        return base
    if name.startswith('grid_'):return base
    old=common_case('generator_staggered');g=old.network.generators[0].model_copy(deep=True);p=old.coupled.machines[0].model_copy(deep=True)
    node='lv_b' if name=='gen_lv_clock' else 'section_b';g.ll_v=690. if node=='lv_b' else 10000.
    g.p_w=2e5;g.q_min_var=-2e6;g.q_max_var=2e6;g.terminal_id='g:'+node
    terminal=next(t for t in old.network.terminals if t.equipment_id==g.id).model_copy(update={'id':g.terminal_id,'connectivity_node_id':node})
    base.network.terminals.append(terminal);base.network.generators=[g]
    if name in ('gen_cold_start','cold_generator_q_limit'):base.induction.operating_point=None;base.induction.duration_s=3.
    if name=='gen_cold_start':p.rated_va=10e6;g.q_min_var=-10e6;g.q_max_var=10e6
    if name=='gen_saturated':p.saturation='quadratic';p.saturation_s10=.1;p.saturation_s12=.3
    if name=='generator_q_limit':g.q_min_var=-1.;g.q_max_var=1.
    data=base.model_dump(mode='json');data['study']='coupled_rms';data['coupled']=dict(data['induction'],machines=[p.model_dump(mode='json')],faults=[]);data['induction']=None
    return StudySpec.model_validate(data)


if __name__=='__main__':
    from power_grid_env.report import write_json
    for name in (*NAMES,*NEGATIVE):write_json(Path('cases/studies/multibus_loaded_'+name+'.json'),example(name).model_dump(mode='json'))
