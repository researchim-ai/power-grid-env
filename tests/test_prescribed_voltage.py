import copy
import numpy as np
import pytest
from benchmarks.switching_thermal_cases import document
from power_grid_env.flux_machine import FluxMachinePassport
from power_grid_env.prescribed_voltage import voltage_step
from power_grid_env.switching_magnetics import SwitchingMagnetics


def passport(cages=4,beta=2.,core=.006,thermal=False):
    m=FluxMachinePassport.model_validate(document('saturated_four_thermal')['motor'])
    return m.model_copy(update=dict(cages=m.cages[:cages],saturation_beta_per_vs=beta,core_loss_s=core,thermal=m.thermal if thermal else None))


def dense_step(p,old,speed,u,h,temperature=None):
    port=SwitchingMagnetics(p);wmid=speed;guess=old.copy()
    for _ in range(32):
        a=port.affine(old,h,wmid,guess,temperature);delta=a['offset_delta']+a['gain_flux']@np.array([u.real,u.imag]);mid=old+delta
        q=port.stage(old,mid,h,[u.real,u.imag],delta,temperature)
        load=p.fan_nm_per_rad_s2*wmid*abs(wmid)+p.viscous_nm_per_rad_s*wmid
        next_mid=speed if p.locked_rotor else speed+h/(2*p.inertia_kg_m2)*(q['torque_nm']-load)
        if abs(next_mid-wmid)<1e-11 and q['constitutive_residual_v']<1e-9:break
        wmid=next_mid;guess=mid
    else:raise RuntimeError('dense control did not converge')
    return old+2*delta,2*next_mid-speed,q


@pytest.mark.parametrize('cages',[1,2,3,4])
@pytest.mark.parametrize('beta',[0.,2.])
@pytest.mark.parametrize('core',[0.,.006])
@pytest.mark.parametrize('speed',[-80.,0.,80.])
def test_condensed_voltage_step_matches_full_port_and_conserves_energy(cages,beta,core,speed):
    p=passport(cages,beta,core);port=SwitchingMagnetics(p);old=np.tile([.6,.1],cages+1);old[2:]*=.95
    h=2e-5;u=230+40j;r=voltage_step(p,old,speed,u,h);flux,w,q=dense_step(p,old,speed,u,h)
    np.testing.assert_allclose(r['flux'],flux,rtol=1e-10,atol=2e-11)
    assert r['speed_rad_s']==pytest.approx(w,abs=1e-10)
    assert r['torque_nm']==pytest.approx(q['torque_nm'],abs=1e-8)
    before=port.energy(old)+.5*p.inertia_kg_m2*speed**2;after=port.energy(r['flux'])+.5*p.inertia_kg_m2*r['speed_rad_s']**2
    net=h*(r['source_w']-r['stator_copper_w']-r['rotor_copper_w']-r['core_loss_w']-r['load_nm']*r['speed_mid_rad_s'])
    assert after-before==pytest.approx(net,abs=1e-8)


def test_thermal_midpoint_is_explicit_and_retains_each_rotor_resistance():
    p=passport(thermal=True);old=np.tile([.5,.1],5);r=voltage_step(p,old,20.,180+60j,1e-5,temperature=[90.,70.]);flux,w,q=dense_step(p,old,20.,180+60j,1e-5,[90.,70.])
    np.testing.assert_allclose(r['flux'],flux,atol=1e-10)
    assert r['rotor_copper_w']==pytest.approx(q['rotor_copper_w'],abs=1e-8)
    with pytest.raises(ValueError,match='explicit imposed'):voltage_step(p,old,20.,180j,1e-5)


def test_locked_shaft_and_rotation_equivariance():
    p=passport();p=p.model_copy(update={'locked_rotor':True});old=np.tile([.5,.1],5);u=200+70j;rotation=np.exp(.7j)
    r=voltage_step(p,old,0.,u,1e-5);z=old.reshape(-1,2);rotated=(z[:,0]+1j*z[:,1])*rotation;rotated=np.column_stack([rotated.real,rotated.imag]).ravel()
    s=voltage_step(p,rotated,0.,u*rotation,1e-5);expected=r['flux'].reshape(-1,2);expected=(expected[:,0]+1j*expected[:,1])*rotation
    np.testing.assert_allclose(s['flux'].reshape(-1,2),np.column_stack([expected.real,expected.imag]),atol=1e-11)
    assert s['speed_rad_s']==0 and s['torque_nm']==pytest.approx(r['torque_nm'],abs=1e-9)
    with pytest.raises(ValueError):voltage_step(p,old,1.,u,1e-5)


def test_permuting_rotor_cages_preserves_port_and_torque():
    p=passport();old=np.array([[.6,.1],[.5,.08],[.52,.07],[.55,.09],[.57,.1]])
    q=p.model_copy(update={'cages':p.cages[::-1]});permuted=np.vstack([old[0],old[:0:-1]])
    a=voltage_step(p,old.ravel(),20.,180+30j,1e-5);b=voltage_step(q,permuted.ravel(),20.,180+30j,1e-5)
    expected=a['flux'].reshape(-1,2);np.testing.assert_allclose(b['flux'].reshape(-1,2),np.vstack([expected[0],expected[:0:-1]]),atol=1e-11)
    assert a['torque_nm']==pytest.approx(b['torque_nm'],abs=1e-9)


@pytest.mark.parametrize('field,value',[('elapsed',0.),('elapsed',-1.),('elapsed',float('nan')),('speed',float('nan')),('voltage',complex(float('inf'),0.))])
def test_invalid_input_does_not_change_state_or_passport(field,value):
    p=passport();old=np.tile([.4,.1],5);before=old.copy();params=copy.deepcopy(p.model_dump());args=dict(passport=p,old=old,speed=0.,voltage=200j,elapsed=1e-5);args[field]=value
    with pytest.raises(ValueError):voltage_step(**args)
    np.testing.assert_array_equal(old,before);assert p.model_dump()==params


def test_malformed_temperature_and_flux_inventory_are_rejected():
    p=passport(thermal=True);old=np.tile([.4,.1],5)
    for temperature in [[20.],[20.,float('nan')],[2000.,20.],[-10000.,20.]]:
        with pytest.raises(ValueError):voltage_step(p,old,0.,200j,1e-5,temperature=temperature)
    with pytest.raises(ValueError):voltage_step(p,old[:-1],0.,200j,1e-5,temperature=[20.,20.])
    with pytest.raises(ValueError):voltage_step(passport(),old,0.,200j,1e-5,temperature=[20.,20.])


def test_preserved_dense_trajectory_driver_has_same_inventory_and_physics():
    from benchmarks.prescribed_voltage_acceptance import dense_native
    from benchmarks.pwm_motor_comparison import native,protocol
    p=protocol('saturated_pwm');p['duration_s']=.002
    a=native(p,1e-5);b=dense_native(p,1e-5)
    assert a['row_channels']==b['row_channels'] and a['time_s']==b['time_s'] and a['steps']==b['steps']
    np.testing.assert_allclose(a['rows'],b['rows'],rtol=1e-10,atol=1e-10)


def test_equivalence_artifact_serializes_acceptance_and_is_sealed(tmp_path,monkeypatch):
    import benchmarks.prescribed_voltage_acceptance as acceptance
    from power_grid_env.study_artifacts import read_json,verify
    original=acceptance.protocol
    def short(name):
        p=original(name);p['duration_s']=.002;p['zero_voltage_windows']=[];return p
    monkeypatch.setattr(acceptance,'protocol',short)
    out=tmp_path/'equivalence';acceptance.run(out)
    assert read_json(out/'summary.json')['passed'] and verify(out)



def test_constant_mechanical_load_changes_speed_and_retains_energy_balance():
    p=passport(1,0.,0.);port=SwitchingMagnetics(p);old=np.array([.4,.1,.38,.09]);speed=20.;h=2e-5
    a=voltage_step(p,old,speed,220+30j,h);b=voltage_step(p,old,speed,220+30j,h,load_torque_nm=5.)
    assert b['speed_rad_s']<a['speed_rad_s']
    expected=h*(b['source_w']-b['stator_copper_w']-b['rotor_copper_w']-b['core_loss_w']-b['load_nm']*b['speed_mid_rad_s'])
    actual=port.energy(b['flux'])+.5*p.inertia_kg_m2*b['speed_rad_s']**2-port.energy(old)-.5*p.inertia_kg_m2*speed**2
    assert actual==pytest.approx(expected,abs=1e-8)
