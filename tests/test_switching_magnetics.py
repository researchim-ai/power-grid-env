import copy
import numpy as np
import pytest
from benchmarks.switching_extended_cases import document
from benchmarks.switching_stage_reference import solve_stage
from power_grid_env.switching_magnetics import SwitchingMagnetics
from power_grid_env.switching_drive import SwitchingDriveStudy,SwitchingDrive,run_switching_drive


@pytest.mark.parametrize('exponent',[1,2,3,7,20])
@pytest.mark.parametrize('old,new',[(np.array([.5,.2]),np.array([.50000000001,.2])),(np.zeros(2),np.array([.4,.1])),(np.array([.4,.1]),np.zeros(2)),(np.array([.4,.1]),np.array([-.4,-.1]))])
def test_radial_discrete_gradient_exact_storage_identity(exponent,old,new):
    p=SwitchingDriveStudy.model_validate(document('saturated_core')).motor;p.saturation_exponent=exponent;m=SwitchingMagnetics(p)
    gradient,_=m.radial_gradient(old,(old+new)/2);before=np.tile(old,m.n);after=np.tile(new,m.n)
    assert 1.5*gradient@(new-old)==pytest.approx(m.energy(after)-m.energy(before),rel=2e-5,abs=2e-14)


def test_discrete_gradient_jacobian_matches_directional_derivative():
    p=SwitchingDriveStudy.model_validate(document('saturated_core')).motor;m=SwitchingMagnetics(p);old=np.array([.5,.2]);mid=np.array([.49,.25]);direction=np.array([.4,-.7]);h=1e-6
    _,jac=m.radial_gradient(old,mid);numeric=(m.radial_gradient(old,mid+h*direction)[0]-m.radial_gradient(old,mid-h*direction)[0])/(2*h)
    np.testing.assert_allclose(jac@direction,numeric,rtol=1e-8,atol=1e-8)


@pytest.mark.parametrize('name',['core_single','double_cage','four_cage_core','saturated_core','saturated_block','saturated_four_cage'])
def test_generalized_stage_matches_independent_mixed_equations(name):
    d=document(name);m=SwitchingDrive(SwitchingDriveStudy.model_validate(d));m.speed=35.;m.line=np.array([1.,-2.,1.]);gates=np.array([[1,0],[0,0],[0,1]],bool)
    state=dict(flux=m.flux.tolist(),line=m.line.tolist(),voltage=m.voltage.tolist(),speed=m.speed,temperature=m.temperature.tolist());q=m.candidate(.01,5e-6,gates)
    ref=solve_stage(d,state,.01,5e-6,gates,False)
    np.testing.assert_allclose(q['flux_mid'],ref['flux_mid'],atol=3e-9,rtol=1e-8)
    np.testing.assert_allclose(q['line_mid'],ref['line_mid'],atol=1e-6)
    assert q['speed_mid']==pytest.approx(ref['speed_mid'],abs=1e-7)
    assert q['motor_core_loss_w']>=0 and q['motor_constitutive_residual_v']<1e-8


@pytest.mark.parametrize('name',['core_single','double_cage','four_cage_core','saturated_core','saturated_block','saturated_four_cage'])
def test_extended_whole_step_energy_with_exact_nonlinear_storage(name):
    d=document(name);m=SwitchingDrive(SwitchingDriveStudy.model_validate(d));m.speed=35.;m.initial_energy=m.stored();before=m.stored();h,q=m.step(.01,5e-6,np.array([[1,0],[0,1],[0,1]],bool))
    assert m.stored()-before==pytest.approx(m.work-m.external_loss-m.mechanical_work,abs=5e-8)


def test_core_current_changes_at_fixed_flux_when_gates_change():
    d=document('saturated_core');m=SwitchingDrive(SwitchingDriveStudy.model_validate(d));flux=m.flux.copy();before=m.currents()
    m.previous_gates=np.array([[1,0],[0,1],[0,1]],bool);after=m.currents()
    np.testing.assert_array_equal(m.flux,flux)
    assert np.linalg.norm(after[:2]-before[:2])>.1


def test_core_switching_heat_uses_post_gate_on_current_without_flux_reset():
    d=document('core_single');m=SwitchingDrive(SwitchingDriveStudy.model_validate(d));before=m.stored();flux=m.flux.copy()
    m.switch(0.,np.array([[1,0],[0,1],[0,1]],bool))
    assert m.switch_heat>0 and m.stored()==pytest.approx(before,abs=1e-9)
    np.testing.assert_array_equal(m.flux,flux)


def test_each_cage_adds_independent_state_and_declared_flux_inventory():
    d=document('four_cage_core');d['motor_initial_fluxes_ab_vs']=[[.2,.1]]*5;m=SwitchingDrive(SwitchingDriveStudy.model_validate(d));assert len(m.flux)==10
    d['motor_initial_fluxes_ab_vs']=[[.2,.1]]*2
    with pytest.raises(ValueError,match='inventory'):SwitchingDriveStudy.model_validate(d)


def test_nominal_observer_is_required_and_distinct_from_multicage_plant():
    d=document('four_cage_core');p=SwitchingDriveStudy.model_validate(d);m=SwitchingDrive(p)
    assert len(m.controller.machine.cages)==1 and len(m.p.motor.cages)==4
    d.pop('observer_motor')
    with pytest.raises(ValueError,match='explicit nominal'):SwitchingDriveStudy.model_validate(d)


def test_nominal_observer_cannot_silently_use_saturation_or_different_poles():
    for field,value in [('saturation_beta_per_vs',1.),('pole_pairs',4)]:
        d=document('saturated_core');d['observer_motor'][field]=value
        with pytest.raises(ValueError,match='observer_motor'):SwitchingDriveStudy.model_validate(d)


def test_short_saturated_pwm_has_core_heat_and_cage_outputs():
    d=document('saturated_block');d['duration_s']=.006;d['gate_block_windows']=[];r=run_switching_drive(d)
    assert max(r['channels']['last_midpoint_motor_core_loss_w'])>1.
    assert 'rotor_cage_2_flux_alpha_vs' in r['channels'] and r['metrics']['final_energy_relative_error']<1e-7


def test_cage_order_permutation_preserves_network_solution_and_energy():
    d=document('four_cage_core');d['motor_initial_fluxes_ab_vs']=[[.3,.1],[.28,.09],[.27,.08],[.29,.11],[.26,.07]]
    perm=[2,0,3,1];other=copy.deepcopy(d);other['motor']['cages']=[d['motor']['cages'][k] for k in perm];other['motor_initial_fluxes_ab_vs']=[d['motor_initial_fluxes_ab_vs'][0]]+[d['motor_initial_fluxes_ab_vs'][1+k] for k in perm]
    a=SwitchingDrive(SwitchingDriveStudy.model_validate(d));b=SwitchingDrive(SwitchingDriveStudy.model_validate(other));g=np.array([[1,0],[0,1],[0,1]],bool);qa=a.candidate(.01,5e-6,g);qb=b.candidate(.01,5e-6,g)
    np.testing.assert_allclose(qa['voltage'],qb['voltage'],atol=1e-7)
    assert qa['torque_nm']==pytest.approx(qb['torque_nm'],abs=1e-9) and a.stored()==pytest.approx(b.stored(),abs=1e-10)


def test_core_loss_modifies_machine_and_dc_dynamics_not_only_output_channels():
    d=document('saturated_core');a=SwitchingDrive(SwitchingDriveStudy.model_validate(d));d['motor']['core_loss_s']=0.;b=SwitchingDrive(SwitchingDriveStudy.model_validate(d));g=np.array([[1,0],[0,1],[0,1]],bool)
    qa=a.candidate(.01,1e-5,g);qb=b.candidate(.01,1e-5,g)
    assert qa['motor_core_loss_w']>10 and qb['motor_core_loss_w']==0
    assert np.linalg.norm(qa['flux_mid']-qb['flux_mid'])>1e-7 and np.linalg.norm(qa['voltage'][3:5]-qb['voltage'][3:5])>1e-5


def test_zero_core_limit_converges_in_switched_trajectory():
    a=document('core_single');b=copy.deepcopy(a);a['motor']['core_loss_s']=0.;b['motor']['core_loss_s']=1e-10
    for d in [a,b]:d['duration_s']=.004
    ra=run_switching_drive(a);rb=run_switching_drive(b)
    for key in ['dc_voltage_v','motor_current_alpha_a','motor_stator_flux_alpha_vs']:
        np.testing.assert_allclose(ra['channels'][key],rb['channels'][key],atol=2e-6,rtol=1e-7)


def test_saturation_zero_limit_converges_in_coupled_stage():
    d=document('saturated_core');d['motor']['saturation_beta_per_vs']=0.;a=SwitchingDrive(SwitchingDriveStudy.model_validate(d));d['motor']['saturation_beta_per_vs']=1e-10;b=SwitchingDrive(SwitchingDriveStudy.model_validate(d));g=np.array([[1,0],[0,1],[0,1]],bool)
    qa=a.candidate(.01,1e-5,g);qb=b.candidate(.01,1e-5,g)
    np.testing.assert_allclose(qa['flux_mid'],qb['flux_mid'],atol=1e-12,rtol=1e-12)
