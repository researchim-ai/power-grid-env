import copy
import numpy as np
import pytest
from scipy.linalg import expm
from power_grid_env.switching_drive import SwitchingDrive,SwitchingDriveStudy,run_switching_drive
from power_grid_env.switching_winding_thermal import winding_thermal_step
from benchmarks.switching_thermal_cases import document
from benchmarks.switching_stage_reference import solve_stage


def machine(name='cold_winding'):return SwitchingDrive(SwitchingDriveStudy.model_validate(document(name)))


def test_resistance_law_uses_actual_winding_temperatures_and_every_cage():
    m=machine('saturated_four_thermal');rs,rr=m.magnetics.resistances([100.,80.]);h=m.p.motor.thermal
    assert rs==pytest.approx(m.p.motor.stator_r_ohm*(1+h.alpha_per_c*80))
    np.testing.assert_allclose(rr,[c.resistance_ohm*(1+h.alpha_per_c*60) for c in m.p.motor.cages])
    assert len(rr)==4


@pytest.mark.parametrize('name',['cold_winding','warm_winding','saturated_four_thermal'])
def test_coupled_electrothermal_step_matches_independent_mixed_oracle(name):
    m=machine(name);gates=np.array([[1,0],[0,1],[0,1]],bool);q=m.candidate(.01,1e-5,gates)
    state=dict(flux=m.flux.tolist(),line=m.line.tolist(),voltage=m.voltage.tolist(),speed=m.speed,temperature=m.temperature.tolist(),winding_temperature=m.winding_temperature.tolist())
    ref=solve_stage(m.p.model_dump(mode='json'),state,.01,1e-5,gates,False)
    np.testing.assert_allclose(q['flux_mid'],ref['flux_mid'],atol=2e-9)
    np.testing.assert_allclose(q['winding_temperature'],2*np.array(ref['winding_temperature_mid_c'])-m.winding_temperature,atol=1e-8)


@pytest.mark.parametrize('name',['cold_winding','warm_winding','saturated_four_thermal'])
def test_motor_heat_is_stored_once_and_total_energy_balances(name):
    m=machine(name);m.flux[:2]=[.3,.1];m.speed=20.;before=m.stored();heat_before=np.array(m.winding_temperature)
    h,q=m.step(.01,1e-5,np.array([[1,0],[0,1],[0,1]],bool))
    assert m.stored()-before==pytest.approx(m.work-m.external_loss-m.mechanical_work,abs=1e-7)
    thermal=m.p.motor.thermal;capacities=np.array([thermal.stator_capacity_j_per_k,thermal.rotor_capacity_j_per_k])
    stored_winding=capacities@(m.winding_temperature-heat_before)
    assert stored_winding==pytest.approx(h*(q['motor_stator_copper_w']+q['motor_core_loss_w']+q['motor_rotor_copper_w'])-sum(q['winding_ambient_j']),abs=1e-9)


def test_cross_heat_redistributes_energy_without_creating_or_destroying_it():
    h=machine().p.motor.thermal.model_copy(update=dict(stator_ambient_w_per_k=0.,rotor_ambient_w_per_k=0.,cross_w_per_k=5.))
    before=np.array([100.,40.]);after,ambient=winding_thermal_step(h,before,[0.,0.],.01)
    assert after[0]<before[0] and after[1]>before[1] and sum(ambient)==0
    assert np.array([h.stator_capacity_j_per_k,h.rotor_capacity_j_per_k])@(after-before)==pytest.approx(0.,abs=1e-10)


def test_two_lump_cooling_has_second_order_convergence_to_exact_exponential():
    h=machine('winding_cooling').p.motor.thermal;C=np.diag([h.stator_capacity_j_per_k,h.rotor_capacity_j_per_k]);H=np.diag([h.stator_ambient_w_per_k,h.rotor_ambient_w_per_k])+h.cross_w_per_k*np.array([[1.,-1.],[-1.,1.]])
    start=np.array([90.,60.]);exact=h.ambient_c+expm(-np.linalg.solve(C,H)*.12)@(start-h.ambient_c);errors=[]
    for count in [6,12,24]:
        t=start.copy()
        for _ in range(count):t,_=winding_thermal_step(h,t,[0.,0.],.12/count)
        errors.append(max(abs(t-exact)))
    assert errors[0]/errors[1]>3.9 and errors[1]/errors[2]>3.9


def test_temperature_feedback_changes_flux_and_dc_solution_not_only_channels():
    a=machine('warm_winding');d=document('warm_winding');d['motor']['thermal']['alpha_per_c']=0.;b=SwitchingDrive(SwitchingDriveStudy.model_validate(d));g=np.array([[1,0],[0,1],[0,1]],bool)
    qa=a.candidate(.01,1e-5,g);qb=b.candidate(.01,1e-5,g)
    assert qa['motor_stator_resistance_ohm']>qb['motor_stator_resistance_ohm']
    assert np.linalg.norm(qa['flux_mid']-qb['flux_mid'])>1e-8 and np.linalg.norm(qa['voltage'][3:5]-qb['voltage'][3:5])>1e-7


def test_trial_step_preserves_temperature_resistance_and_relay_memory():
    m=machine('warm_winding');before=m.winding_temperature.copy();flags=m.motor_latched.copy();rs=m.p.motor.stator_r_ohm
    m.candidate(.01,1e-5,np.array([[1,0],[0,1],[0,1]],bool))
    np.testing.assert_array_equal(before,m.winding_temperature);np.testing.assert_array_equal(flags,m.motor_latched);assert m.p.motor.stator_r_ohm==rs


def test_winding_overrange_rejection_does_not_partially_commit_physics():
    m=machine('saturated_four_thermal');m.p.motor_temperature_trip_c=None;m.motor_latched[:]=False;m.p.motor.thermal.maximum_c=20.000000001
    state=[m.flux.copy(),m.line.copy(),m.voltage.copy(),m.temperature.copy(),m.winding_temperature.copy()];speed=m.speed;work=m.work
    with pytest.raises(ValueError,match='maximum_c'):m.step(.01,1e-5,np.array([[1,0],[0,1],[0,1]],bool))
    for a,b in zip(state,[m.flux,m.line,m.voltage,m.temperature,m.winding_temperature]):np.testing.assert_array_equal(a,b)
    assert m.speed==speed and m.work==work


def test_winding_relay_localization_keeps_current_and_flux_continuous():
    m=machine();m.flux[:2]=[.3,.1];m.p.motor_temperature_trip_c=(20.000001,140.);before=m.flux.copy()
    h,q=m.step(.01,1e-5,np.array([[1,0],[0,1],[0,1]],bool))
    assert h<1e-5 and m.motor_latched[0] and m.trip_active()
    assert m.winding_temperature[0]==pytest.approx(20.000001,abs=1e-8)
    assert m.events[-1]['component']=='motor_stator' and np.linalg.norm(m.flux)>0
    assert np.linalg.norm(m.flux-before)<.01


def test_initial_trip_inhibits_gates_even_after_cooling_below_threshold():
    d=document('initial_trip_cooling');d['duration_s']=.06;d['gate_block_windows']=[(0.,.06)]
    r=run_switching_drive(d);c=r['channels'];assert r['event_log'][0]['cause']=='initial_temperature'
    assert c['motor_stator_temperature_c'][-1]<85. and all(v==0 for v in c['gates_enabled']) and all(v==1 for v in c['motor_stator_thermal_trip'])
    assert r['initial_winding_thermal_trips']==[True,False] and r['metrics']['gate_edges']==0


@pytest.mark.parametrize('field,value',[('motor_initial_temperature_c',[10.,20.]),('motor_initial_temperature_c',[160.,20.]),('motor_temperature_trip_c',[20.,100.]),('motor_temperature_trip_c',[170.,100.])])
def test_invalid_initial_temperature_or_trip_range_rejected(field,value):
    d=document('cold_winding');d[field]=value
    with pytest.raises(ValueError):SwitchingDriveStudy.model_validate(d)


def test_winding_options_require_thermal_passport_and_nominal_observer():
    from benchmarks.switching_extended_cases import document as nonthermal
    d=nonthermal('core_single');d['motor_initial_temperature_c']=[20.,20.]
    with pytest.raises(ValueError,match='require motor.thermal'):SwitchingDriveStudy.model_validate(d)
    d=document('cold_winding');d.pop('observer_motor')
    with pytest.raises(ValueError,match='explicit nominal'):SwitchingDriveStudy.model_validate(d)


def test_unresolved_winding_thermal_step_is_rejected():
    d=document('cold_winding');d['motor']['thermal']['cross_w_per_k']=1e10
    with pytest.raises(ValueError,match='winding RC midpoint'):SwitchingDriveStudy.model_validate(d)


def test_zero_alpha_matches_isothermal_electrical_trajectory():
    a=document('cold_winding');a['motor']['thermal']['alpha_per_c']=0.;a['duration_s']=.004
    b=copy.deepcopy(a);b['motor']['thermal']=None;b.pop('motor_temperature_trip_c',None)
    ra=run_switching_drive(a);rb=run_switching_drive(b)
    for key in ['dc_voltage_v','motor_stator_flux_alpha_vs','motor_current_alpha_a','speed_rad_s']:
        np.testing.assert_allclose(ra['channels'][key],rb['channels'][key],atol=2e-7,rtol=1e-7)


def test_actual_temperature_affects_instantaneous_core_current_at_fixed_flux():
    m=machine();m.flux[:2]=[.3,.1];m.previous_gates=np.array([[1,0],[0,1],[0,1]],bool);before=m.currents();flux=m.flux.copy();m.winding_temperature[:]=100.
    after=m.currents();np.testing.assert_array_equal(m.flux,flux)
    assert np.linalg.norm(after[:2]-before[:2])>1e-4


def test_eddy_current_heat_is_deposited_in_stator_lump():
    m=machine();h=1e-5;q=m.candidate(.01,h,np.array([[1,0],[0,1],[0,1]],bool));p=m.p.motor.thermal;old=m.winding_temperature;new=q['winding_temperature'];mid=(old+new)/2
    inferred=p.stator_capacity_j_per_k*(new[0]-old[0])/h+p.stator_ambient_w_per_k*(mid[0]-p.ambient_c)+p.cross_w_per_k*(mid[0]-mid[1])
    assert q['motor_core_loss_w']>1.
    assert inferred==pytest.approx(q['motor_stator_copper_w']+q['motor_core_loss_w'],abs=1e-6)


def test_simultaneous_winding_trips_have_deterministic_order():
    m=machine();m.flux[:2]=[.3,.1];g=np.array([[1,0],[0,1],[0,1]],bool);trial=m.candidate(.01,1e-5,g);m.p.motor_temperature_trip_c=tuple(trial['winding_temperature'])
    m.step(.01,1e-5,g)
    assert m.motor_latched.tolist()==[True,True]
    assert [e['component'] for e in m.events]==['motor_stator','motor_rotor']
    assert m.events[0]['time_s']==m.events[1]['time_s']
