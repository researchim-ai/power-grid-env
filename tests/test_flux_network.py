import numpy as np
import pytest
from power_grid_env.build import NetBuilder
from power_grid_env.flux_network import FluxNetworkSettings,FluxNetwork,run_flux_network,FluxSourceEvent
from power_grid_env.flux_machine import WindingThermal
from tests.test_flux_machine import passport


def example(meshed=False,thermal=False):
    b=NetBuilder('flux-net','authored RL network',.4,s_base_va=10000);b.node('grid');b.node('a');b.node('b');b.source('grid',.05,.1)
    b.line_z1('grid','a',.1+.2j,line_id='l1');b.line_z1('a','b',.1+.2j,line_id='l2')
    if meshed:b.line_z1('grid','b',.2+.3j,line_id='tie')
    motors=[passport(id='m1',node_id='a',saturation_beta_per_vs=.84),passport(id='m2',node_id='b')]
    if thermal:
        motors[0].thermal=WindingThermal(stator_capacity_j_per_k=10,rotor_capacity_j_per_k=8,stator_ambient_w_per_k=1,rotor_ambient_w_per_k=1,cross_w_per_k=.2)
        motors[0].provenance['thermal']='authored accelerated thermal stress; not OEM heat capacities'
    return b.build(),FluxNetworkSettings(motors=motors,duration_s=.03,step_s=.0005)


@pytest.mark.parametrize('meshed',[False,True])
@pytest.mark.parametrize('thermal',[False,True])
def test_network_preserves_original_KCL_and_complete_energy(meshed,thermal):
    net,s=example(meshed,thermal);r=run_flux_network(net,s);assert r['completed'],r
    assert r['maximum_kcl_relative_residual']<1e-9 and r['maximum_energy_relative_residual']<1e-7
    if thermal:
        c=r['channels'];assert c['m1.stator_temperature_c'][-1]>20 and c['m1.stator_resistance_ohm'][-1]>s.motors[0].stator_r_ohm
    for id_ in ['m1','m2']:
        c=r['channels'];assert max(abs(np.array(c[id_+'.phase_a_a'])+c[id_+'.phase_b_a']+c[id_+'.phase_c_a']))<1e-12


def test_source_event_has_continuous_winding_currents_and_fluxes():
    net,s=example();s.events=[FluxSourceEvent(time_s=.015,source_id='source',voltage_pu=.5)]
    s=FluxNetworkSettings.model_validate(s.model_dump());r=run_flux_network(net,s);assert r['completed'],r
    ids=[i for i,t in enumerate(r['time_s']) if t==.015];assert len(ids)==2
    for k,v in r['channels'].items():
        if any(x in k for x in ['.current_','.stator_flux_','.cage1_flux_','.speed_','.temperature_']):assert v[ids[0]]==v[ids[1]],k


def test_public_spec_and_isolated_worker_support_flux_profile():
    from power_grid_env.study_spec import StudySpec,run_study
    net,s=example();s.duration_s=.002;s.step_s=.001
    spec=StudySpec(id='flux-isolated',study='flux_network',backend='scipy',network=net,flux_network=s)
    r=run_study(spec,isolate=True,wall_timeout_s=30);assert r['completed'],r
    assert r['execution']['mode']=='isolated_process' and r['network_sha256']


@pytest.mark.parametrize('defect',['capacitance','unbalance','pure_resistance_load','source_event'])
def test_unsupported_physics_rejected_before_integration(defect):
    net,s=example()
    if defect=='capacitance':net.lines[0].b_siemens[0][0]=1e-6
    elif defect=='unbalance':net.lines[0].r_ohm[0][0]*=2
    elif defect=='source_event':s.events=[FluxSourceEvent(time_s=.01,source_id='unknown',voltage_pu=.5)];s=FluxNetworkSettings.model_validate(s.model_dump())
    else:
        b=NetBuilder('x','x',.4);b.node('a');b.load('a',1000,0,load_id='r',model='constant_z');other=b.build();net.loads+=other.loads;net.terminals+=other.terminals
    r=run_flux_network(net,s);assert not r['completed'] and r['status']=='rejected',r


def test_transformer_impedance_is_referred_to_its_secondary_nameplate_not_bus_label():
    from power_grid_env.rms_study import transformer_parameters
    from power_grid_env.topology import compile_topology
    from power_grid_env.model import parse_vector_group
    b=NetBuilder('nameplate','unequal nominal labels',11.,s_base_va=1e6);b.node('hv',11.);b.node('lv',.42)
    b.source('hv',1.,2.,ll_v=11000.)
    b.transformer('hv','lv',hv_kv=10.,lv_kv=.4,kva=1000.,x_pct=6.,r_pct=.5,vector_group='Dyn11',hv_ground='not_applicable',lv_ground='solid',transformer_id='t')
    net=b.build();tr=net.transformers[0];top=compile_topology(net);p=transformer_parameters(tr,top,net.s_base_va)
    second_base=top.buses[top.terminal_bus[tr.windings[1].terminal_id]].nominal_ll_v**2/net.s_base_va
    assert p['x']*second_base==pytest.approx(.06*400**2/1e6)
    assert p['r']*second_base==pytest.approx(.005*400**2/1e6)


def test_equal_parallel_cages_collapse_to_equivalent_single_cage():
    from power_grid_env.flux_machine import FluxMachinePassport
    b=NetBuilder('cage-limit','authored equal-cage collapse',.4,s_base_va=5000);b.node('n');b.source('n',.05,0.)
    one=passport(node_id='n',saturation_beta_per_vs=.84,cages=[dict(resistance_ohm=1.25,leakage_h=.0115)])
    two=one.model_copy(deep=True);two.cages=[one.cages[0].model_copy(update=dict(resistance_ohm=2.5,leakage_h=.023)) for _ in range(2)]
    a=run_flux_network(b.build(),FluxNetworkSettings(motors=[one],duration_s=.02,step_s=.0005))
    c=run_flux_network(b.build(),FluxNetworkSettings(motors=[two],duration_s=.02,step_s=.0005))
    assert a['completed'] and c['completed']
    for key in ['m.current_alpha_a','m.current_beta_a','m.speed_rad_s','m.torque_nm']:
        assert a['channels'][key]==pytest.approx(c['channels'][key],abs=2e-6)
