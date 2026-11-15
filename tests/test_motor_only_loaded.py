import copy
from pathlib import Path
import numpy as np
import pytest
from power_grid_env.study_artifacts import read_json
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.coupled_rms import CoupledNetwork
from power_grid_env.coupled_initialization import initialize_coupled
from benchmarks.running_fault_cartesian import CartesianProtocol,conditions,condition_jobs

ROOT=Path(__file__).resolve().parents[1]

def example(method='radau',thermal='equilibrium'):
    p=CartesianProtocol.model_validate(read_json(ROOT/'cases/running-fault-cartesian/protocol.json'))
    _,condition=next(conditions(p))
    job=next(j for j in condition_jobs(p,p.envelope.base,condition) if j['kind']=='normal' and j['scenario']=='start' and j['method']==method and j['grid']==0)
    s=job['study'];s['coupled'].update(duration_s=.3,operating_point=dict(thermal_state=thermal),events=[])
    return StudySpec.model_validate(s)


@pytest.mark.parametrize('method',['radau','network_dae'])
def test_loaded_hold_is_stationary_with_no_generator(method):
    study=example(method);report=run_study(study);assert report['completed'],report
    op=report['operating_point'];assert op['mode']=='motor_only_loaded_equilibrium'
    assert op['generator_constraints']==0 and report['generator_mapping']=={}
    assert op['network_kcl_residual_pu']<1e-10 and op['checked_dynamic_residual_per_s']<1e-8
    assert op['connected_machine_spectral_abscissa_per_s']<-1e-7
    for name in ['e_real_pu','e_imag_pu','speed_pu','current_pu','thermal_level']:
        values=report['channels']['m.'+name];assert max(abs(np.array(values)-values[0]))<1e-7
    assert report['channels']['m.speed_pu'][0]>.95
    assert op['initial_active_automation_relays']==[] and op['initial_automation_derivative_max_per_s']==0


@pytest.mark.parametrize('method',['radau','network_dae'])
def test_passport_heat_is_not_mislabeled_as_thermal_equilibrium(method):
    study=example(method,'passport');report=run_study(study);assert report['completed']
    heat=np.array(report['channels']['m.thermal_level']);times=np.array(report['time_s'])
    current=report['channels']['m.current_pu'][0]
    assert heat[0]==0 and heat[-1]>0
    assert heat==pytest.approx(current**2*(1-np.exp(-times/5)),abs=1e-8)
    assert report['operating_point']['thermal_residual_per_s']['m']>0


def test_direct_state_satisfies_original_network_and_ode():
    study=example();model=CoupledNetwork(study.network,study.coupled);state,op=initialize_coupled(model)
    assert state is not None
    voltage=model.electrical(state)[0]
    assert np.max(abs(model.matrix@voltage-model.network_injection(state)))<1e-10
    assert np.max(abs(model.physical_rhs(0.,state)))<1e-8
    assert len(op['connected_motor_state_names'])==3


@pytest.mark.parametrize('defect',['no_connected','overloaded','speed_bound','thermal_limit'])
def test_invalid_equilibrium_does_not_integrate(monkeypatch,defect):
    import power_grid_env.coupled_rms as module
    monkeypatch.setattr(module,'integrate_induction_model',lambda *a,**k:pytest.fail('invalid initialization must not integrate'))
    s=example()
    if defect=='no_connected':s.coupled.motors[0].connected=False
    elif defect=='overloaded':s.coupled.motors[0].fan_torque_pu=1000
    elif defect=='speed_bound':s.coupled.operating_point.minimum_speed_pu=.999
    else:s.coupled.motors[0].thermal.trip_level=.01
    r=run_study(s);assert not r['completed'] and r['status']=='initialization_failed'
    if defect=='no_connected':assert r['reason']=='no_connected_motor_for_operating_point'
    elif defect=='thermal_limit':assert r['reason']=='initial_thermal_limit'
    else:assert r['reason']=='no_qualified_coupled_operating_point'


def test_zero_load_boundary_root_at_synchronous_speed():
    s=example();s.coupled.motors[0].fan_torque_pu=0
    r=run_study(s);assert r['completed'],r
    assert r['channels']['m.speed_pu']==pytest.approx(np.ones(len(r['time_s'])),abs=1e-8)


def test_active_relay_timer_is_explicit_not_global_stability_claim():
    s=example();s.coupled.duration_s=.05
    relay=s.coupled.network_automation.relays[0];relay.pickup=1;relay.time_multiplier_s=10
    r=run_study(s);assert r['completed']
    op=r['operating_point'];assert relay.id in op['initial_active_automation_relays']
    assert op['initial_automation_derivative_max_per_s']>0 and not op['network_automation_stationary_initialization']
    assert op['checked_dynamic_residual_per_s']<1e-8


@pytest.mark.parametrize('count',[2,4])
def test_group_loaded_network_equilibrium(count):
    s=example();s.coupled.duration_s=.1
    first=s.coupled.motors[0];first.rated_va/=count
    s.coupled.motors=[first.model_copy(deep=True) for _ in range(count)]
    for i,m in enumerate(s.coupled.motors):m.id='group'+str(i)
    r=run_study(s);assert r['completed'],r
    for m in s.coupled.motors:
        v=r['channels'][m.id+'.speed_pu'];assert max(abs(np.array(v)-v[0]))<1e-7
    assert len(r['operating_point']['connected_motor_eigenvalues_per_s'])==3*count


@pytest.mark.parametrize('method',['radau','network_dae'])
def test_double_cage_loaded_state_is_stationary(method):
    s=example(method);m=s.coupled.motors[0]
    m.model='double_cage';m.rotor2_r_pu=.06;m.rotor2_x_pu=.2
    r=run_study(s);assert r['completed'],r
    for key in ['e_real_pu','e_imag_pu','speed_pu','cage1_e_real_pu','cage1_e_imag_pu','rotor1_current_pu','rotor2_current_pu']:
        v=np.asarray(r['channels']['m.'+key]);assert max(abs(v-v[0]))<1e-7
    assert len(r['operating_point']['connected_motor_state_names'])==5


def test_disconnected_group_member_excluded_from_stability_certificate():
    s=example();off=s.coupled.motors[0].model_copy(deep=True);off.id='off';off.connected=False
    s.coupled.motors.append(off);r=run_study(s);assert r['completed'],r
    assert all(not name.startswith('off.') for name in r['operating_point']['connected_motor_state_names'])
    assert r['channels']['off.current_pu']==pytest.approx(np.zeros(len(r['time_s'])))


def test_nonlinear_mechanical_load_law_equilibrates():
    from power_grid_env.induction import MotorMechanicalLoad
    payload=example().model_dump(mode='json');m=payload['coupled']['motors'][0];m.pop('fan_torque_pu')
    m['mechanical_load']=MotorMechanicalLoad(viscous_torque_pu=.1,quadratic_torque_pu=.4,friction_torque_pu=.1,smoothing_speed_pu=.01).model_dump(mode='json')
    s=StudySpec.model_validate(payload)
    r=run_study(s);assert r['completed'],r
    assert r['operating_point']['checked_dynamic_residual_per_s']<1e-8
    v=np.array(r['channels']['m.speed_pu']);assert max(abs(v-v[0]))<1e-7
