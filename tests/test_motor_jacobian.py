"""Analytic linear-network/motor Jacobian and segment cache correctness."""
from pathlib import Path
import numpy as np
import pytest
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.induction_network import InductionNetwork
from power_grid_env.motor_operating_point import initialize_motor_state


def spec(name='operating_mixed_sag'):
    return StudySpec.model_validate_json(Path('cases/studies/'+name+'.json').read_text())


def finite_difference(model,state):
    columns=[]
    for i,value in enumerate(state):
        h=1e-7*max(1.,abs(value));plus=state.copy();minus=state.copy();plus[i]+=h;minus[i]-=h
        columns.append((model.rhs(0.,plus)-model.rhs(0.,minus))/(2*h))
    return np.column_stack(columns)


@pytest.mark.parametrize('name',['induction_start','operating_mixed_sag','thermal_cold_trip','operating_group_sag'])
def test_analytic_jacobian_matches_original_RHS_central_differences(name):
    s=spec(name);model=InductionNetwork(s.network,s.induction);state,_=initialize_motor_state(model);assert state is not None
    for i in range(len(s.induction.motors)):state[3*i:3*i+3]=[.2,.1,.85 if i%2 else -.2]
    if model.thermal_indices:
        for address in model.thermal_indices.values():state[address]=.4
    actual=model.jacobian(0.,state);expected=finite_difference(model,state)
    assert np.max(abs(actual-expected))<3e-6


def test_contactor_refactor_invalidates_response_and_off_motor_flux_still_decays():
    s=spec('operating_group_sag');model=InductionNetwork(s.network,s.induction);state,_=initialize_motor_state(model);assert state is not None
    model.jacobian(0.,state);cached=model._motor_current_response
    model.connected[0]=False;model.factorize();assert model._motor_current_response is None
    actual=model.jacobian(0.,state);assert model._motor_current_response is not cached
    assert np.max(abs(actual-finite_difference(model,state)))<3e-6
    assert np.linalg.norm(model.rhs(0.,state)[:2])>0


def test_analytic_integration_matches_finite_difference_solver_including_trip(monkeypatch):
    for name in ['induction_start','thermal_cold_trip','operating_mixed_sag']:
        s=spec(name);a=run_study(s)
        with monkeypatch.context() as patch:
            patch.setattr(InductionNetwork,'integration_jacobian',lambda self:None);b=run_study(s)
        assert a['completed'] and b['completed'],(a,b)
        assert a['integration']['jacobian']=='analytic_motor_network_reduction'
        assert b['integration']['jacobian']=='scipy_finite_difference'
        assert a['sample_side']==b['sample_side'] and len(a['time_s'])==len(b['time_s'])
        assert max(abs(np.asarray(a['time_s'])-b['time_s']))<1e-7
        for key in a['channels']:
            tolerance=1. if key.endswith(('.p_w','.q_var')) else 2e-6
            assert max(abs(np.asarray(a['channels'][key])-b['channels'][key]))<tolerance,key


def test_joint_generator_profile_keeps_full_finite_difference_jacobian():
    s=StudySpec.model_validate_json(Path('cases/studies/pss_coupled_fault.json').read_text());r=run_study(s)
    assert r['completed'] and r['integration']['jacobian']=='scipy_finite_difference'


@pytest.mark.parametrize('group',['Dyn11','YNd1','Dyn5'])
def test_analytic_jacobian_respects_transformer_phase_and_two_motor_voltage_bases(group):
    from test_multivoltage_rms import example as multi
    s=spec('induction_start');s.network=multi(group).network
    removed={g.terminal_id for g in s.network.generators};s.network.generators=[];s.network.terminals=[t for t in s.network.terminals if t.id not in removed]
    s.network.loads[0].model='constant_z';s.induction.operating_point=None;s.induction.events=[]
    m=s.induction.motors[0];m.node_id='load'
    high=m.model_copy(deep=True);high.id='high';high.node_id='hv';high.rated_ll_v=110000.;s.induction.motors.append(high)
    model=InductionNetwork(s.network,s.induction);state=np.array([.2,.1,.7,.4,-.1,.9])
    assert np.max(abs(model.jacobian(0.,state)-finite_difference(model,state)))<3e-6


def test_distributed_motor_group_matches_fd_with_fewer_actual_RHS_calls():
    from benchmarks.motor_scale import make_study,measure,compare_samples
    s=make_study(16,4,True,duration=.2)
    analytic=measure(s,'analytic');finite=measure(s,'finite_difference')
    assert compare_samples(analytic,finite)['passed']
    assert analytic['rhs_calls']<finite['rhs_calls']
