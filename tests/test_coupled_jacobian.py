"""GENCLS analytic reduction, topology invalidation and explicit fallback scope."""
from pathlib import Path
import numpy as np
import pytest
from power_grid_env.study_spec import StudySpec, run_study
from power_grid_env.coupled_rms import CoupledNetwork
from power_grid_env.coupled_jacobian import jacobian, KIND
from power_grid_env.generator_thermal import GeneratorThermalProtection


def example(name='cold_start'):
    return StudySpec.model_validate_json(Path('cases/studies/coupled_'+name+'.json').read_text())


def difference(model,state,h=2e-7):
    columns=[]
    for j,v in enumerate(state):
        step=h*max(1.,abs(v));a=state.copy();b=state.copy();a[j]+=step;b[j]-=step
        columns.append((model.rhs(0.,a)-model.rhs(0.,b))/(2*step))
    return np.column_stack(columns)


@pytest.mark.parametrize('cage',['single_cage','double_cage'])
@pytest.mark.parametrize('opened',['none','motor','generator','both'])
@pytest.mark.parametrize('speed',[-.2,0.,.83,1.15])
def test_full_jacobian_matches_every_RHS_coordinate(cage,opened,speed):
    s=example();m=s.coupled.motors[0]
    if cage=='double_cage':m.model=cage;m.rotor2_r_pu=.07;m.rotor2_x_pu=.12
    from power_grid_env.motor_thermal import MotorThermalProtection
    m.thermal=MotorThermalProtection(time_constant_s=2.,trip_level=5.)
    s.coupled.generator_thermal=[GeneratorThermalProtection(generator_id='g',time_constant_s=3.,trip_level=5.)]
    model=CoupledNetwork(s.network,s.coupled)
    if opened in ('motor','both'):model.connected[:]=False
    if opened in ('generator','both'):model.gen_connected[:]=False
    model.factorize();state=model.initial_state();state[:3]=[.2,-.1,speed]
    state[model.gen_offset:model.rotor_end]=[.31,1.02];model.gen_voltage[:]=1.13
    if cage=='double_cage':state[model.cages.addresses[0]]=[.03,-.06]
    actual=jacobian(model,state);expected=difference(model,state)
    assert np.max(abs(actual-expected))<4e-6
    assert np.isfinite(actual).all()


def test_factorization_invalidates_both_responses_and_parameter_changes_stay_live():
    s=example();model=CoupledNetwork(s.network,s.coupled);x=model.initial_state();x[:3]=[.2,.1,.7]
    jacobian(model,x);cached=model._classical_voltage_response
    model.gen_voltage[:]=1.2;model.source_scale=.8;model.load_scale[:]=.7
    assert np.max(abs(jacobian(model,x)-difference(model,x)))<4e-6
    assert model._classical_voltage_response is cached
    model.gen_connected[:]=False;model.factorize()
    assert np.max(abs(jacobian(model,x)-difference(model,x)))<4e-6
    assert model._classical_voltage_response is not cached


@pytest.mark.parametrize('name',['hold','motor_load_step','generator_step','voltage_sag','cold_start','thermal_trip'])
def test_analytic_path_agrees_with_independent_SI_reference(name):
    from benchmarks.coupled_rms_reference import reference
    s=example(name);a=run_study(s);assert a['completed'],a
    assert a['integration']['jacobian']==KIND
    b=reference(s,a)
    for key,values in b['channels'].items():
        limit=100. if key.endswith(('.p_w','.q_var')) else 1e-4
        assert max(abs(np.array(a['channels'][key])-values))<limit,key


@pytest.mark.parametrize('name',['generator_oel2c_hold'])
def test_OEL2C_delegates_from_classical_profile(name):
    s=StudySpec.model_validate_json(Path('cases/studies/'+name+'.json').read_text())
    model=CoupledNetwork(s.network,s.coupled)
    assert model.integration_jacobian().jacobian_kind=='piecewise_analytic_OEL2C_network_reduction'
    with pytest.raises(ValueError,match='GENCLS'):jacobian(model,model.initial_state())


@pytest.mark.parametrize('damage',['shape','nan','inf'])
def test_invalid_state_never_produces_usable_derivatives(damage):
    s=example();model=CoupledNetwork(s.network,s.coupled);x=model.initial_state()
    if damage=='shape':x=x[:-1]
    if damage=='nan':x[0]=np.nan
    if damage=='inf':x[0]=np.inf
    with pytest.raises(ValueError):jacobian(model,x)


@pytest.mark.parametrize('outage',['none','generator','motor','fault'])
def test_many_machine_mutual_derivatives_and_fault_refactor(outage):
    from benchmarks.coupled_jacobian_validation import case
    s=case('scale_512_32_4');s.coupled.motors=s.coupled.motors[:4]
    model=CoupledNetwork(s.network,s.coupled);model.connected[:]=True
    if outage=='generator':model.gen_connected[1]=False
    if outage=='motor':model.connected[2]=False
    if outage=='fault':model.fault_y[model.gen_buses[1]]=2.-3.j
    model.factorize();x=model.initial_state()
    for i in range(4):
        x[3*i:3*i+3]=[.1+.02*i,-.03*i,.8+.01*i]
        x[model.gen_offset+2*i:model.gen_offset+2*i+2]=[.15*i,1.+.002*i]
    model.gen_voltage[:]=[1.,1.05,1.1,.97]
    assert np.max(abs(jacobian(model,x)-difference(model,x)))<5e-6


def test_jacobian_does_not_evaluate_or_perturb_RHS(monkeypatch):
    s=example();model=CoupledNetwork(s.network,s.coupled);x=model.initial_state();original=x.copy()
    monkeypatch.setattr(CoupledNetwork,'rhs',lambda *a:pytest.fail('finite differencing inside claimed analytic Jacobian'))
    j=jacobian(model,x)
    assert np.isfinite(j).all() and np.array_equal(x,original)


def test_invalid_and_incomplete_comparison_cannot_qualify():
    from benchmarks.coupled_jacobian_validation import compare
    with pytest.raises(ValueError):compare({'completed':False},{'completed':True})


@pytest.mark.parametrize('channel,delta,passes',[('line.loss_p_w',.001,True),('line.from_p_w',101.,False),('line.net_q_var',99.,True),('m.speed_pu',.001,False)])
def test_comparison_limits_follow_channel_units_not_separator(channel,delta,passes):
    import copy
    from benchmarks.coupled_jacobian_validation import compare
    a=dict(completed=True,sample_side=['initial'],time_s=[0.],channels={channel:[1.]},event_log=[],maximum_kcl_residual_pu=0.,maximum_power_balance_residual_pu=0.)
    b=copy.deepcopy(a);b['channels'][channel][0]+=delta
    assert compare(a,b)['passed'] is passes
