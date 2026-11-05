"""Authored DAE trajectory/scale inventory; no fitted physical parameters."""
from pathlib import Path
from power_grid_env.study_spec import StudySpec
from benchmarks.network_tangent_validation import case as previous

NAMES=('classical_trip','round_limits','esst_trip','uel_trip','oel_regulating','batch_four','motor_heat','motor_coast','max_genrou_8_32','max_esst_8_32','max_genrou_long_8_32','motor_trip_latch')
SCALES=((512,64,16),(2000,128,32))


def case(name):return previous(name)


def scale_case(nodes,motors,generators,*,duration_s=6.,voltage_profile='original_0995'):
    if (nodes,motors,generators) not in SCALES:raise ValueError('registered extended DAE scale tuple required')
    if voltage_profile not in {'original_0995','nominal_1p0'}:raise ValueError('named PV profile required')
    suffix='' if voltage_profile=='original_0995' else '-nominal-pv'
    from power_grid_env.build import NetBuilder
    from power_grid_env.induction import InductionMotor
    from power_grid_env.coupled_rms import CoupledSettings,CoupledEvent
    from benchmarks.ex03_cases import scale_case as base
    b=NetBuilder(f'dae-scale-{nodes}-{motors}-{generators}'+suffix,'Authored fixed-total-VA meshed DAE profile',10.,s_base_va=10e6)
    for i in range(nodes):b.node(f'n{i}')
    b.source('n0',.05,.2)
    for i in range(1,nodes):
        b.line_z1(f'n{(i-1)//2}',f'n{i}',.02+.06j,line_id=f'l{i}')
        b.load(f'n{i}',1e5/(nodes-1),2e4/(nodes-1),model='constant_z',load_id=f'load{i}')
    for i in range(nodes//8,nodes,nodes//8):b.line_z1(f'n{i}',f'n{max(0,i-nodes//16)}',.03+.08j,line_id=f'tie{i}')
    template=base(64,4,1).coupled.machines[0];machines=[]
    for i in range(generators):
        gid=f'g{i}';b.generator(f'n{i+1}',1e5/generators,mode='pv',v_set_pu=.995 if voltage_profile=='original_0995' else 1.,q_min_var=-1e7,q_max_var=1e7,generator_id=gid)
        m=template.model_copy(deep=True);m.generator_id=gid;m.rated_va=10e6/generators;machines.append(m)
    group=[InductionMotor(id=f'm{i}',node_id=f'n{nodes//2+i*(nodes//2-1)//max(1,motors-1)}',rated_va=1e6/motors,rated_ll_v=10000.,stator_r_pu=.02,stator_x_pu=.1,rotor_r_pu=.015,rotor_x_pu=.1,magnetizing_x_pu=3.,inertia_s=.3,fan_torque_pu=.8,connected=False,model='double_cage',rotor2_r_pu=.07,rotor2_x_pu=.12) for i in range(motors)]
    settings=CoupledSettings(monitored_lines=[],capacity_profile='extended_128x32',integration_method='network_dae',jacobian_format='csc',jacobian_network='direct_solve',event_policy='coincident_roots_v1',duration_s=duration_s,step_s=min(.05,duration_s/4),motors=group,machines=machines,rtol=1e-8,atol=1e-10,events=[CoupledEvent(time_s=.1 if duration_s>.1 else duration_s/4,kind='motor_close',equipment_id=m.id) for m in group])
    settings.runtime_limits.maximum_integration_wall_s=600.
    return StudySpec(id=b.network_id,study='coupled_rms',backend='scipy',network=b.build(),coupled=settings)
