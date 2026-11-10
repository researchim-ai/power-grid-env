import copy
import math
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from benchmarks.switching_drive_cases import document
from benchmarks.switching_stage_reference import solve_stage
from power_grid_env.switching_drive import SwitchingDriveStudy,SwitchingDrive,run_switching_drive,run_switching_artifact
from power_grid_env.pwm_bridge import gates_at,pwm_plan,thermal_step,T
from power_grid_env.rotor_flux_control import RotorFluxController


def model(name='foc_start'):return SwitchingDrive(SwitchingDriveStudy.model_validate(document(name)))


@pytest.mark.parametrize('angle',np.linspace(0,2*math.pi,7))
def test_pwm_has_no_shoot_through_and_resolves_dead_time(angle):
    period=.0004;dead=2e-6;duty,edges=pwm_plan(250*np.exp(1j*angle),540.,period,dead)
    assert edges==sorted(set(edges)) and edges[0]==0 and edges[-1]==period
    for a,b in zip(edges,edges[1:]):
        g=gates_at((a+b)/2,duty,period,dead)
        assert not np.any(g[:,0]&g[:,1])
    for i,d in enumerate(duty):
        on=(1-d)*period/2;off=(1+d)*period/2
        assert not any(gates_at(on+dead/2,duty,period,dead)[i])
        assert not any(gates_at(off+dead/2,duty,period,dead)[i])
        assert gates_at((on+dead+off)/2,duty,period,dead)[i,0]


def test_pwm_blocking_opens_channels_at_any_phase():
    for time in [0.,.0001,.0002,.0003]:assert not np.any(gates_at(time,[.2,.5,.8],.0004,2e-6,False))


@pytest.mark.parametrize('bad',[0.,-1e-6,.00004])
def test_invalid_dead_time_rejected(bad):
    with pytest.raises(ValueError):pwm_plan(10.,500.,.0004,bad)


@pytest.mark.parametrize('gates',[np.array([[1,0],[0,1],[0,1]],bool),np.array([[0,0],[0,1],[1,0]],bool),np.array([[0,0],[0,0],[0,0]],bool)])
def test_coupled_midpoint_agrees_with_independent_mixed_unknown_oracle(gates):
    m=model();m.flux=np.array([.15,.08,.12,.06]);m.speed=15.
    state=dict(flux=m.flux.tolist(),line=m.line.tolist(),voltage=m.voltage.tolist(),speed=m.speed,temperature=m.temperature.tolist())
    q=m.candidate(.01,4e-6,gates)
    ref=solve_stage(m.p.model_dump(mode='json'),state,.01,4e-6,gates,m.bypassed)
    np.testing.assert_allclose(q['line_mid'],ref['line_mid'],atol=3e-7)
    np.testing.assert_allclose(q['flux_mid'],ref['flux_mid'],atol=3e-9)
    assert q['speed_mid']==pytest.approx(ref['speed_mid'],abs=1e-8)
    # Motor common-mode gauge is irrelevant; line-to-line voltages must agree.
    np.testing.assert_allclose(np.diff(q['voltage'][5:]),np.diff(ref['voltage'][5:]),atol=2e-4)


def test_one_coupled_stage_conserves_all_stored_energy():
    m=model();m.flux=np.array([.15,.08,.12,.06]);m.speed=15.;m.initial_energy=m.stored()
    initial=m.stored();h,q=m.step(.01,5e-6,np.array([[1,0],[0,1],[0,1]],bool))
    assert m.stored()-initial==pytest.approx(m.work-m.external_loss-m.mechanical_work,abs=2e-8)
    assert q['kcl_a']<1e-6 and min(q['heat'])>=0


def test_switching_energy_is_transferred_from_capacitor_to_heat():
    m=model();m.flux=np.array([.15,.08,.12,.06]);before=m.stored();vdc=m.voltage[3]-m.voltage[4];oldtemp=m.temperature[1]
    m.switch(.01,np.array([[1,0],[0,1],[0,1]],bool))
    assert m.switch_heat>0 and m.temperature[1]>oldtemp and m.voltage[3]-m.voltage[4]<vdc
    assert m.stored()==pytest.approx(before,abs=1e-9)


def test_body_diodes_continue_conducting_after_all_gates_blocked():
    m=model();m.flux=np.array([.15,.08,.12,.06]);before=m.flux.copy();q=m.candidate(.01,2e-6,np.zeros((3,2),bool))
    assert q['body_diode_power_w']>0
    np.testing.assert_array_equal(m.flux,before)
    h,q=m.step(.01,2e-6,np.zeros((3,2),bool));assert np.linalg.norm(m.flux)>0 and np.linalg.norm(m.line)>0


def test_shoot_through_command_rejected():
    with pytest.raises(ValueError,match='shoot-through'):model().switch(0.,np.ones((3,2),bool))


def test_flux_observer_matches_independent_constant_current_solution():
    m=model();c=m.controller;current=3.+2j;speed=50.;elapsed=.003
    c.flux=.2-.1j;start=c.flux
    def rhs(t,y):
        flux=complex(*y);d=c.rr*(current-flux/c.lm)+1j*m.p.motor.pole_pairs*speed*flux
        return [d.real,d.imag]
    ref=solve_ivp(rhs,(0,elapsed),[start.real,start.imag],rtol=1e-12,atol=1e-13)
    c.observe(current,speed,elapsed)
    assert c.flux==pytest.approx(complex(*ref.y[:,-1]),abs=1e-11)


def test_foc_observer_does_not_read_simulated_rotor_flux():
    a=model();b=model();b.flux=np.array([.1,.2,.3,.4])
    for c in [a.controller,b.controller]:c.observe(2.+1j,15.,.002)
    ua,qa=a.controller.update(.02,2.+1j,15.,500.,.0004,True);ub,qb=b.controller.update(.02,2.+1j,15.,500.,.0004,True)
    assert ua==ub and qa==qb


def test_foc_limits_reference_and_voltage_without_clipping_machine_state():
    m=model();c=m.controller;c.flux=.5+0j;c.speed_integral=1000.
    u,q=c.update(.2,30.+20j,-100.,100.,.0004,True)
    assert q['current_reference_peak_a']<=m.p.control.current_limit_peak_a+1e-12
    assert abs(u)<=100/math.sqrt(3)*m.p.control.voltage_utilization+1e-12
    assert c.speed_integral==1000.
    c.update(.2,30.+20j,100.,100.,.0004,True)
    assert c.speed_integral<1000.


def test_thermal_midpoint_matches_analytic_rc_with_step_refinement():
    C=np.array([2.,3.,4.]);G=np.array([.5,1.,2.]);power=np.array([10.,20.,30.]);initial=np.array([25.,26.,27.]);ambient=25.;end=.2
    exact=ambient+power/G+(initial-ambient-power/G)*np.exp(-G/C*end);errors=[]
    for count in [10,20,40]:
        value=initial.copy()
        for _ in range(count):value,_=thermal_step(value,power,end/count,C,G,ambient)
        errors.append(np.max(abs(value-exact)))
    assert errors[0]/errors[1]>3.9 and errors[1]/errors[2]>3.9


def test_thermal_first_threshold_localized_and_latched_without_current_reset():
    m=model();m.line[0]=10.;m.p.thermal.trip_c=(25.00001,150.,150.);m.flux=np.array([.15,.08,.12,.06]);before=m.flux.copy()
    h,q=m.step(.01,1e-5,np.array([[1,0],[0,1],[0,1]],bool))
    assert h<1e-5 and m.latched[0] and m.events[-1]['component']=='rectifier'
    assert m.temperature[0]==pytest.approx(25.00001,abs=1e-6)
    assert np.linalg.norm(m.flux-before)<.01 and np.linalg.norm(m.flux)>0


@pytest.mark.parametrize('field,value',[('source_inductance_h',0.),('dead_time_s',.0001),('max_step_s',.0001),('schema_version','wrong'),('rail_earth_capacitance_f',0.),('disable_dc_v',500.)])
def test_invalid_switching_profile_rejected(field,value):
    p=document('foc_start');p[field]=value
    with pytest.raises(ValueError):SwitchingDriveStudy.model_validate(p)


@pytest.mark.parametrize('field,value',[('core_loss_s',.006),('saturation_beta_per_vs',.84),('locked_rotor',True)])
def test_unsupported_machine_physics_is_explicitly_rejected(field,value):
    p=document('foc_start');p['motor'][field]=value
    with pytest.raises(ValueError):SwitchingDriveStudy.model_validate(p)


def test_short_switching_workflow_has_equal_channels_and_sealed_artifact(tmp_path):
    from power_grid_env.study_artifacts import verify,export_zip,extract_zip
    p=document('foc_start');p.update(duration_s=.008,sample_s=.0001)
    out=tmp_path/'run';r=run_switching_artifact(p,out)
    assert r['completed'] and len(r['time_s'])>70 and all(len(x)==len(r['time_s']) for x in r['channels'].values())
    assert r['time_s'][-1]==pytest.approx(.008) and all(a<b for a,b in zip(r['time_s'],r['time_s'][1:]))
    assert r['metrics']['final_energy_relative_error']<1e-8
    verify(out);export_zip(out,tmp_path/'run.zip');extract_zip(tmp_path/'run.zip',tmp_path/'restored');verify(tmp_path/'restored')


def test_switched_network_precharges_from_zero_without_seed_energy():
    p=document('foc_start');p.update(duration_s=.004,initial_dc_v=0.)
    r=run_switching_drive(p)
    assert r['channels']['dc_voltage_v'][0]==0 and r['channels']['dc_voltage_v'][-1]>1.
    assert min(r['channels']['dc_voltage_v'])>=0 and not any(r['channels']['gates_enabled'])
    assert r['metrics']['final_energy_relative_error']<1e-7


def test_negative_speed_reference_requests_braking_torque():
    m=model('regeneration');c=m.controller;c.flux=.4+.1j
    _,q=c.update(.1,3.+2j,180.,550.,.0004,True)
    assert q['torque_reference_nm']<0 and q['current_reference_q_a']<0 and q['torque_enabled']


def test_stage_trials_do_not_change_physical_state_or_controller_memory():
    m=model();state=[m.flux.copy(),m.line.copy(),m.voltage.copy(),m.temperature.copy()];speed=m.speed;integral=m.controller.integral
    m.candidate(.01,1e-5,np.array([[1,0],[0,1],[0,1]],bool))
    for old,new in zip(state,[m.flux,m.line,m.voltage,m.temperature]):np.testing.assert_array_equal(old,new)
    assert m.speed==speed and m.controller.integral==integral and not m.events


def test_independent_stage_oracle_cannot_call_native_solver(monkeypatch):
    m=model();state=dict(flux=[.15,.08,.12,.06],line=[0.,0.,0.],voltage=m.voltage.tolist(),speed=15.,temperature=m.temperature.tolist())
    def forbidden(*a,**kw):raise AssertionError('native solver called by stage oracle')
    monkeypatch.setattr(SwitchingDrive,'_electrical',forbidden);monkeypatch.setattr(SwitchingDrive,'candidate',forbidden)
    r=solve_stage(m.p.model_dump(mode='json'),state,.01,4e-6,np.array([[1,0],[0,1],[0,1]],bool),False)
    assert r['residual_max']<2e-6


def test_cli_writes_reproducible_switching_study(tmp_path,capsys):
    from power_grid_env.cli import main
    from power_grid_env.study_artifacts import encoded
    p=document('foc_start');p.update(duration_s=.003);path=tmp_path/'input.json';path.write_bytes(encoded(p))
    assert main(['switching-drive','run','--study',str(path),'--out',str(tmp_path/'run')])==0
    assert main(['switching-drive','verify',str(tmp_path/'run')])==0
    assert main(['switching-drive','export',str(tmp_path/'run'),'--out',str(tmp_path/'run.zip')])==0
    assert (tmp_path/'run.zip').exists()


def test_negative_switching_loss_temperature_factor_is_rejected():
    p=document('foc_start');p['thermal'].update(ambient_c=-100.,initial_c=[-100.,-100.,-100.]);p['switching_temperature_per_k']=.02
    with pytest.raises(ValueError,match='switching loss temperature factor'):SwitchingDriveStudy.model_validate(p)


def test_unresolved_thermal_relaxation_is_rejected_instead_of_negative_cooling():
    p=document('foc_start');p['thermal']['conductance_w_per_k']=[1e10,1e10,1e10]
    with pytest.raises(ValueError,match='thermal RC midpoint'):SwitchingDriveStudy.model_validate(p)


def test_rectifier_trip_fixture_avoids_unqualified_extreme_temperature():
    r=run_switching_drive(document('rectifier_thermal_trip'))
    assert any(e['kind']=='thermal_trip' and e['component']=='rectifier' for e in r['event_log'])
    assert max(r['channels']['rectifier_temperature_c'])<100.
    assert not any(r['channels']['precharge_bypassed']) and r['metrics']['final_energy_relative_error']<1e-7
