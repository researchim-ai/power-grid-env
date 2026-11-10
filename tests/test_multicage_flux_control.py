import copy
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from scipy.optimize import root
from benchmarks.multicage_observer_cases import document, NAMES
from power_grid_env.switching_drive import SwitchingDriveStudy, SwitchingDrive, run_switching_drive
from power_grid_env.multicage_flux_control import MulticageFluxController
from power_grid_env.rotor_flux_control import RotorFluxController


def observer(name='four_cage_saturated'):
    return SwitchingDrive(SwitchingDriveStudy.model_validate(document(name))).controller


from benchmarks.multicage_observer_reference import independent_rhs


@pytest.mark.parametrize('name',['double_cage_matched','four_cage_saturated'])
def test_observer_second_order_against_independent_continuous_equations(name):
    o=observer(name);current=complex(8.,-3.);speed=-25.;duration=.006
    reference=solve_ivp(independent_rhs(o.machine,current,speed),(0,duration),np.zeros(o.rotor_flux.size),method='DOP853',rtol=1e-11,atol=1e-13,max_step=2e-5)
    assert reference.success
    errors=[]
    for count in [30,60,120]:
        q=observer(name)
        for _ in range(count):q.observe(current,speed,duration/count)
        errors.append(max(abs(q.rotor_flux.ravel()-reference.y[:,-1])))
    assert errors[0]/errors[1]>3.8 and errors[1]/errors[2]>3.8
    assert errors[-1]<2e-7


def test_linear_single_cage_limit_matches_exact_existing_observer():
    o=observer('double_cage_matched');m=o.machine.model_copy(update={'cages':o.machine.cages[:1]})
    a=MulticageFluxController(o.p,m);b=RotorFluxController(o.p,m)
    for _ in range(1000):a.observe(5+2j,35.,1e-5);b.observe(5+2j,35.,1e-5)
    assert abs(a.flux-b.flux)<1e-8
    assert a.lm==pytest.approx(b.lm) and a.lsigma==pytest.approx(b.lsigma)


def test_nonlinear_reconstruction_recovers_all_winding_currents():
    o=observer();rotor=np.array([[.1,.02],[.08,-.03],[.12,.03],[.07,.01]])
    current=12+6j;s,_,_=o.reconstruct(rotor,current);p=o.machine
    recovered=s*(1+(p.saturation_beta_per_vs*np.linalg.norm(s))**p.saturation_exponent)/p.unsaturated_h+np.sum((s-rotor)/o.leakage[:,None],axis=0)
    np.testing.assert_allclose(recovered,[12,6],atol=1e-11)


def test_saturation_changes_observed_flux_and_all_cages_are_retained():
    a=observer();b=observer();b.machine=b.machine.model_copy(update={'saturation_beta_per_vs':0.})
    for _ in range(200):a.observe(30+15j,20,.0001);b.observe(30+15j,20,.0001)
    assert a.rotor_flux.shape==(4,2) and abs(a.flux-b.flux)>.001
    assert len(set(np.round(np.linalg.norm(a.rotor_flux,axis=1),8)))==4


def test_orientation_frequency_is_derivative_of_weighted_cage_angle():
    o=observer()
    for _ in range(50):o.observe(8+4j,40,.0001)
    current=6-2j;speed=40.;o.angle=np.angle(o.flux);measured=current/np.exp(1j*o.angle)
    omega=o.orientation_frequency(speed,measured,abs(o.flux));eps=1e-8
    b=np.sum(o.rotor_flux/o.leakage[:,None],axis=0);after=np.sum((o.rotor_flux+eps*o.derivative(o.rotor_flux,current,speed))/o.leakage[:,None],axis=0)
    numerical=np.angle(complex(*after)/complex(*b))/eps
    assert omega==pytest.approx(numerical,rel=1e-5,abs=1e-5)


@pytest.mark.parametrize('field,value',[('core_loss_s',.001),('thermal',{'ambient_c':20.,'stator_capacity_j_per_k':100.,'rotor_capacity_j_per_k':80.,'stator_ambient_w_per_k':1.,'rotor_ambient_w_per_k':1.}),('pole_pairs',3),('locked_rotor',True)])
def test_unsupported_observer_physics_rejected(field,value):
    d=document('four_cage_saturated');d['observer_motor'][field]=value
    d['observer_motor']['provenance'].update(thermal='authored',core_loss='authored')
    with pytest.raises(ValueError):SwitchingDriveStudy.model_validate(d)


def test_explicit_observer_required_and_legacy_mode_still_strict():
    d=document('four_cage_saturated');d.pop('observer_motor')
    with pytest.raises(ValueError):SwitchingDriveStudy.model_validate(d)
    d=document('four_cage_saturated');d.pop('observer_mode')
    with pytest.raises(ValueError,match='linear single-cage'):SwitchingDriveStudy.model_validate(d)


@pytest.mark.parametrize('current,speed,elapsed',[(complex(np.nan,0),0,.001),(0j,np.inf,.001),(0j,0,-.1),(0j,0,np.nan)])
def test_bad_observer_input_is_atomic(current,speed,elapsed):
    o=observer();before=o.rotor_flux.copy()
    with pytest.raises(ValueError):o.observe(current,speed,elapsed)
    np.testing.assert_array_equal(before,o.rotor_flux)


def test_failed_nonlinear_solve_does_not_commit(monkeypatch):
    import power_grid_env.multicage_flux_control as module
    from types import SimpleNamespace
    o=observer();before=o.rotor_flux.copy()
    monkeypatch.setattr(module,'root',lambda *a,**kw:SimpleNamespace(x=np.ones(before.size)*100))
    with pytest.raises(RuntimeError):o.observe(1+2j,0,.001)
    np.testing.assert_array_equal(before,o.rotor_flux)


@pytest.mark.parametrize('name',NAMES)
def test_closed_pwm_drive_has_complete_observer_inventory_and_conserves_energy(name):
    d=document(name);d['duration_s']=.004;d['gate_block_windows']=[]
    result=run_switching_drive(d)
    assert result['completed'] and result['observer_mode']=='multicage_current'
    assert result['metrics']['final_energy_relative_error']<1e-7
    assert result['metrics']['kcl_max_a']<5e-5
    channels=result['channels']
    for k in range(1,len(d['observer_motor']['cages'])+1):
        assert len(channels[f'observer_cage_{k}_flux_alpha_vs'])==len(result['time_s'])
    assert np.all(np.isfinite(channels['flux_estimate_vs']))


def test_observer_does_not_consume_true_initial_flux():
    d=document('four_cage_saturated');d['motor_initial_fluxes_ab_vs']=[[.3,.1]]*5
    m=SwitchingDrive(SwitchingDriveStudy.model_validate(d))
    assert np.linalg.norm(m.flux)>0
    np.testing.assert_array_equal(m.controller.rotor_flux,np.zeros((4,2)))
    assert m.controller.flux==0j


def test_analytic_observer_jacobian_matches_cartesian_finite_difference():
    o=observer();state=np.array([[.2,.1],[.18,-.02],[.21,.08],[.15,.04]])
    current=20+8j;speed=-60.;eps=1e-7
    finite=np.column_stack([(o.derivative((state.ravel()+eps*np.eye(state.size)[k]).reshape(state.shape),current,speed).ravel()-o.derivative((state.ravel()-eps*np.eye(state.size)[k]).reshape(state.shape),current,speed).ravel())/(2*eps) for k in range(state.size)])
    np.testing.assert_allclose(o.derivative_jacobian(state,current,speed),finite,rtol=1e-7,atol=1e-6)


def test_cage_permutation_preserves_equivalent_flux_and_voltage_command():
    a=observer();b=MulticageFluxController(a.p,a.machine.model_copy(update={'cages':list(reversed(a.machine.cages))}))
    for _ in range(100):a.observe(8+3j,-15.,.0001);b.observe(8+3j,-15.,.0001)
    np.testing.assert_allclose(a.rotor_flux,b.rotor_flux[::-1],atol=1e-12)
    assert a.flux==pytest.approx(b.flux,abs=1e-12)
    ua,_=a.update(.1,8+3j,-15.,540.,.0004,True);ub,_=b.update(.1,8+3j,-15.,540.,.0004,True)
    assert ua==pytest.approx(ub,abs=1e-9)


def test_zero_input_decay_and_rotational_equivariance():
    a=observer();b=observer();rotation=np.exp(.7j)
    for _ in range(100):a.observe(8+3j,30.,.0001);b.observe((8+3j)*rotation,30.,.0001)
    assert b.flux==pytest.approx(a.flux*rotation,abs=1e-11)
    old=abs(a.flux)
    for _ in range(100):a.observe(0j,0.,.0001)
    assert abs(a.flux)<old


def test_nominal_mode_is_omitted_from_legacy_canonical_study():
    from benchmarks.switching_drive_cases import document as legacy
    d=legacy('foc_start');assert 'observer_mode' not in d
    d['observer_mode']='nominal_single_cage'
    assert 'observer_mode' not in SwitchingDriveStudy.model_validate(d).model_dump(mode='json')


def test_thermal_plant_does_not_initialize_observer_from_true_temperature():
    from benchmarks.switching_thermal_cases import document as thermal
    d=thermal('warm_winding');d['observer_mode']='multicage_current'
    d['observer_motor']['saturation_beta_per_vs']=1.5
    a=SwitchingDrive(SwitchingDriveStudy.model_validate(d))
    d['motor_initial_temperature_c']=[30.,30.]
    b=SwitchingDrive(SwitchingDriveStudy.model_validate(d))
    assert not np.array_equal(a.winding_temperature,b.winding_temperature)
    for _ in range(20):a.controller.observe(4+3j,10.,.0001);b.controller.observe(4+3j,10.,.0001)
    np.testing.assert_array_equal(a.controller.rotor_flux,b.controller.rotor_flux)
