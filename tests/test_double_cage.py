"""Physical parallel cages, mixed layouts, exact limits and coupled compatibility."""
from pathlib import Path
import numpy as np
import pytest
from scipy.linalg import expm
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.induction_network import InductionNetwork
from power_grid_env.motor_operating_point import initialize_motor_state
from benchmarks.double_cage_reference import compare


def example(name='cold'):return StudySpec.model_validate_json(Path('cases/studies/double_cage_'+name+'.json').read_text())


@pytest.mark.parametrize('name',['cold','loaded_sag','coast','mixed_group','thermal','mutual_leakage','frequency_base','controlled_restart'])
def test_voltage_states_match_independent_physical_winding_flux_circuit(name):
    r=compare(example(name));assert r['passed'],r
    assert r['trip_identity_matches'] and not r['field_validated']


@pytest.mark.parametrize('name',['mixed_group','thermal','mutual_leakage'])
def test_full_motor_thermal_jacobian_matches_central_differences(name):
    s=example(name);m=InductionNetwork(s.network,s.induction);state,_=initialize_motor_state(m);assert state is not None
    for i in range(len(s.induction.motors)):state[3*i:3*i+3]=[.2,-.1,.8]
    for address in m.cages.addresses.values():state[address]=[.1,.3]
    columns=[]
    for i in range(len(state)):
        a=state.copy();b=state.copy();a[i]+=1e-7;b[i]-=1e-7;columns.append((m.rhs(0,a)-m.rhs(0,b))/2e-7)
    assert np.max(abs(m.jacobian(0,state)-np.array(columns).T))<3e-6
    m.connected[0]=False;m.factorize()
    columns=[]
    for i in range(len(state)):
        a=state.copy();b=state.copy();a[i]+=1e-7;b[i]-=1e-7;columns.append((m.rhs(0,a)-m.rhs(0,b))/2e-7)
    assert np.max(abs(m.jacobian(0,state)-np.array(columns).T))<3e-6


def test_identical_parallel_cages_collapse_to_single_cage_and_labels_are_invariant():
    s=example();m=s.induction.motors[0];m.rotor2_r_pu=m.rotor_r_pu;m.rotor2_x_pu=m.rotor_x_pu;m.rotor_mutual_x_pu=.04
    a=run_study(s);single=s.model_copy(deep=True);m=single.induction.motors[0];m.rotor_r_pu/=2;m.rotor_x_pu=m.rotor_x_pu/2+m.rotor_mutual_x_pu;m.model='single_cage';m.rotor2_r_pu=None;m.rotor2_x_pu=None;m.rotor_mutual_x_pu=0.;b=run_study(single)
    assert a['completed'] and b['completed']
    for key in ['speed_pu','e_real_pu','e_imag_pu','current_pu']:assert max(abs(np.array(a['channels']['m.'+key])-b['channels']['m.'+key]))<2e-6
    s=example();a=run_study(s);m=s.induction.motors[0];m.rotor_r_pu,m.rotor2_r_pu=m.rotor2_r_pu,m.rotor_r_pu;m.rotor_x_pu,m.rotor2_x_pu=m.rotor2_x_pu,m.rotor_x_pu;b=run_study(s)
    assert a['completed'] and b['completed']
    for key in ['speed_pu','e_real_pu','e_imag_pu','current_pu']:assert max(abs(np.array(a['channels']['m.'+key])-b['channels']['m.'+key]))<2e-6
    assert max(abs(np.array(a['channels']['m.cage1_e_real_pu'])-b['channels']['m.cage2_e_real_pu']))<2e-6


def test_open_stator_flux_decay_is_exact_matrix_exponential():
    s=example('coast');r=run_study(s);assert r['completed'];m=s.induction.motors[0];c=r['channels'];t=np.array(r['time_s']);sides=np.array(r['sample_side']);opened,closed=[e.time_s for e in s.induction.events]
    ids=np.flatnonzero(((t>opened)&(t<closed))|((t==opened)&(sides=='post_event'))|((t==closed)&(sides=='pre_event')));i=ids[0]
    wb=2*np.pi*s.network.frequency_hz;zb=m.rated_ll_v**2/m.rated_va;lm=m.magnetizing_x_pu*zb/wb
    matrix=lm*np.ones((2,2))+np.diag([m.rotor_x_pu,m.rotor2_x_pu])*zb/wb;resistance=np.diag([m.rotor_r_pu,m.rotor2_r_pu])*zb;weights=lm*np.linalg.solve(matrix,np.ones(2));vb=m.rated_ll_v/np.sqrt(3)
    u=np.array([complex(c['m.cage1_e_real_pu'][i],c['m.cage1_e_imag_pu'][i]),complex(c['m.cage2_e_real_pu'][i],c['m.cage2_e_imag_pu'][i])]);flux=u*vb/(1j*wb*weights);w0=c['m.speed_pu'][i];rate=m.fan_torque_pu/(2*m.inertia_s)
    for i in ids:
        dt=t[i]-opened;integral=np.log1p(rate*w0*dt)/rate;expected_flux=expm(-resistance@np.linalg.inv(matrix)*dt)@flux*np.exp(-1j*wb*(dt-integral));em=sum(1j*wb*weights*expected_flux/vb)
        assert abs(em-complex(c['m.e_real_pu'][i],c['m.e_imag_pu'][i]))<2e-6
        assert c['m.current_pu'][i]==0


def test_coupled_genrou_controllers_and_local_modes_include_both_cages():
    s=StudySpec.model_validate_json(Path('cases/studies/coupled_modal_pss.json').read_text());original=run_study(s.model_copy(update={'study':'coupled_small_signal'}));assert original['completed']
    m=s.coupled.motors[0];m.model='double_cage';m.rotor2_r_pu=.06;m.rotor2_x_pu=.2;s.coupled.duration_s=.2;s.study='coupled_rms'
    r=run_study(s);assert r['completed'],r
    assert len(r['channels'][m.id+'.cage1_e_real_pu'])==len(r['time_s'])
    s.study='coupled_small_signal';r=run_study(s);assert r['completed'],r
    assert len(r['state_names'])==len(original['state_names'])+2
    assert m.id+'.cage1_e_real_pu' in r['state_names'] and m.id+'.cage1_e_imag_pu' in r['state_names']


def test_thermal_contactor_and_sealed_bundle_support_extra_rotor_states(tmp_path):
    from power_grid_env.motor_bundle import MotorReplayCriteria,export_motor_bundle,replay_motor_bundle
    from power_grid_env.motor_contactor import MotorContactorControl
    s=example('loaded_sag');s.induction.motors[0].contactor=MotorContactorControl(sense_node_id='src',dropout_voltage_pu=.65,pickup_voltage_pu=.85,dropout_delay_s=.08,pickup_delay_s=.12,minimum_off_time_s=.25,maximum_automatic_restarts=1)
    criteria=MotorReplayCriteria.model_validate_json(Path('cases/replay/motor-start.json').read_text());root=tmp_path/'double';export_motor_bundle(s,criteria,root);r=replay_motor_bundle(root)
    assert r['repeatable'] and r['event_audit_matches'] and 'm.cage1_e_real_pu' in r['metrics']


def test_passports_do_not_silently_discard_second_cage_or_invalid_inductance():
    s=example();m=s.induction.motors[0];m.model='single_cage';r=run_study(s);assert not r['completed']
    with pytest.raises(ValueError):StudySpec.model_validate(s.model_dump(mode='json'))
    s=example();s.induction.motors[0].rotor2_x_pu=None;assert not run_study(s)['completed']
    from benchmarks.andes_motor_reference import scope
    with pytest.raises(ValueError):scope(example())


def test_second_cage_parameter_experiments_are_explicit_and_preserve_first_cage():
    from power_grid_env.motor_sensitivity import MotorSensitivityProtocol,ParameterAxis,MotorAssessment,build_parameter_points
    s=example();p=MotorSensitivityProtocol(id='cage2',axes=[ParameterAxis(id='r2',parameter='rotor2_r_pu',equipment_id='m',factors=[1.,2.])],observations=[MotorAssessment(motor_id='m',minimum_final_speed_pu=.95,minimum_bus_voltage_pu=.9,maximum_current_pu=7.)])
    points=build_parameter_points(s,p)
    assert points[1]['study'].induction.motors[0].rotor2_r_pu==2*s.induction.motors[0].rotor2_r_pu
    assert points[1]['study'].induction.motors[0].rotor_r_pu==s.induction.motors[0].rotor_r_pu
    s.induction.motors[0].model='single_cage';s.induction.motors[0].rotor2_r_pu=None;s.induction.motors[0].rotor2_x_pu=None
    with pytest.raises(ValueError):build_parameter_points(s,p)


@pytest.mark.parametrize('clock',['Dyn11','YNd1','Dyn5'])
def test_two_cage_jacobian_preserves_transformer_clock_and_voltage_bases(clock):
    from test_multivoltage_rms import example as multi
    s=example();s.network=multi(clock).network;removed={g.terminal_id for g in s.network.generators};s.network.generators=[];s.network.terminals=[t for t in s.network.terminals if t.id not in removed];s.network.loads[0].model='constant_z'
    s.induction.events=[];s.induction.operating_point=None;s.induction.motors[0].node_id='load'
    high=s.induction.motors[0].model_copy(deep=True);high.id='high';high.node_id='hv';high.rated_ll_v=110000.;s.induction.motors.append(high)
    model=InductionNetwork(s.network,s.induction);state=np.array([.2,.1,.8,.3,-.1,.9,.1,.2,-.1,.3]);expected=[]
    for i in range(len(state)):
        a=state.copy();b=state.copy();a[i]+=1e-7;b[i]-=1e-7;expected.append((model.rhs(0,a)-model.rhs(0,b))/2e-7)
    assert np.max(abs(model.jacobian(0,state)-np.array(expected).T))<3e-6


def test_external_mapped_equivalence_and_limits_do_not_equate_literal_labels(tmp_path):
    from benchmarks.double_cage_andes_limits import check
    r=check(tmp_path/'external');assert r['passed'],r
    assert r['identical_cages_Motor3_limit']['passed_limit'] and r['small_first_resistance_Motor5_limit']['passed_limit']
    assert r['mapped_physical_cages_Motor5']['passed_limit'] and r['mapped_mutual_60Hz_Motor5']['passed_limit']
    assert max(x['maximum_transfer_residual_pu'] for x in r['analytic_parameter_mappings'])<1e-10
    assert r['ordinary_state_spectra_differ'] and not r['ordinary_general_equivalence_claim']
    assert not r['field_validated']


@pytest.mark.parametrize('name',['pss_coupled_fault','generator_open_genrou','coupled_line_fault_open'])
def test_coupled_disturbances_preserve_extra_motor_state_layout_and_balances(name):
    s=StudySpec.model_validate_json(Path('cases/studies/'+name+'.json').read_text());m=s.coupled.motors[0];m.model='double_cage';m.rotor2_r_pu=.06;m.rotor2_x_pu=.2
    r=run_study(s);assert r['completed'],r
    assert r['maximum_kcl_residual_pu']<1e-8 and r['maximum_power_balance_residual_pu']<1e-8
    c=r['channels'];assert np.isfinite(c[m.id+'.cage1_e_real_pu']).all()
    assert max(abs(np.array(c[m.id+'.e_real_pu'])-np.array(c[m.id+'.cage1_e_real_pu'])-np.array(c[m.id+'.cage2_e_real_pu'])))<1e-14
    assert min(c[m.id+'.rotor1_copper_loss_w'])>=0 and min(c[m.id+'.rotor2_copper_loss_w'])>=0
