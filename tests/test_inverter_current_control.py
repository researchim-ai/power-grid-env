"""Current-loop algebraic closure, analytical dynamics and drive energy."""
from types import SimpleNamespace
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from power_grid_env.inverter_current_control import current_control
from power_grid_env.phase_network import PhaseCase
from power_grid_env.vf_drive import VfDrive
from benchmarks.diode_drive_cases import document

P=SimpleNamespace(direct_peak_a=4.,quadrature_peak_a=1.,ramp_s=.01,kp_ohm=20.,ki_ohm_per_s=200.,antiwindup_per_s=100.)


@pytest.mark.parametrize('conductance',[0.,.006,.3])
@pytest.mark.parametrize('limit',[0.,2.,1000.])
def test_closed_algebraic_pi_with_core_feedthrough(conductance,limit):
    offset=2.+3j;integrator=10.-4j;angle=.3
    applied,rate,q=current_control(P,.02,angle,50.,integrator,offset,conductance,limit)
    target=complex(P.direct_peak_a,P.quadrature_peak_a)*np.exp(1j*angle);current=offset+conductance*applied
    command=P.kp_ohm*(target-current)+integrator
    projected=command*min(1.,limit/max(abs(command),1e-300))
    assert applied==pytest.approx(projected,abs=1e-12)
    assert abs(applied)<=limit+1e-12
    assert q['current_tracking_error_a']==pytest.approx(abs(target-current))
    assert rate==pytest.approx(50j*integrator+P.ki_ohm_per_s*(target-current)+P.antiwindup_per_s*(applied-command))


def test_unsaturated_controller_matches_closed_form_linear_ode():
    offset=1.+.5j;g=.2;target=4.+1j;steady=(target-offset)/g;z0=.3-.2j
    def rhs(t,x):
        _,dz,_=current_control(P,1.,0.,0.,complex(*x),offset,g,10000.)
        return [dz.real,dz.imag]
    s=solve_ivp(rhs,(0.,.05),[z0.real,z0.imag],rtol=1e-11,atol=1e-12)
    expected=steady+(z0-steady)*np.exp(-P.ki_ohm_per_s*g/(1+P.kp_ohm*g)*.05)
    assert complex(*s.y[:,-1])==pytest.approx(expected,abs=1e-9)


def test_rotation_covariance_of_stationary_integrator():
    rotation=np.exp(.7j);a,da,_=current_control(P,.02,.2,100.,3.+2j,1.+.5j,.03,10.)
    b,db,_=current_control(P,.02,.9,100.,(3.+2j)*rotation,(1.+.5j)*rotation,.03,10.)
    assert b==pytest.approx(a*rotation) and db==pytest.approx(da*rotation)


def test_zero_voltage_antiwindup_unwinds_large_integrator():
    _,dz,q=current_control(P,.02,0.,0.,1000.+0j,0j,.006,0.)
    assert dz.real<0 and q['current_control_limited']


@pytest.mark.parametrize('gc',[0.,.006])
def test_current_loop_changes_applied_voltage_and_preserves_drive_energy(gc):
    d=document('current_control');d['case']['drives'][0]['motor']['core_loss_s']=gc
    m=VfDrive(PhaseCase.model_validate(d['case']).drives[0]);x=m.initial();assert m.motor_at==9
    x[4]=400.;x[7:9]=[10.,20.];x[m.motor_at:m.speed_at]=[.3,.2,.25,.1];x[m.speed_at]=30.
    dx,q=m.rates(.08,300.+20j,x,True,True);h=1e-7
    energy=lambda z:m.rates(.08,300.+20j,z,True,True)[1]['stored_j']
    rate=(energy(x+h*dx)-energy(x-h*dx))/(2*h)
    assert rate==pytest.approx(q['grid_power_w']-q['loss_w']-q['mechanical_power_w'],abs=3e-4,rel=1e-7)
    assert abs(complex(q['inverter_voltage_alpha_v'],q['inverter_voltage_beta_v']))<=200.+1e-10
    assert np.any(dx[7:9]!=0)


def test_active_front_end_current_control_matches_independent_dq_reference():
    from benchmarks.vf_core_reference import run_reference
    from benchmarks.vf_core_acceptance import comparison
    from power_grid_env.phase_network import run_emt
    d=document('current_control');p=d['case']['drives'][0];p.pop('diode_front_end');p['initial_dc_v']=700.;p['rectifier_efficiency']=.97
    d['case']['events']=[];d['emt'].update(duration_s=.012,sample_s=.0005)
    r=run_emt(PhaseCase.model_validate(d['case']),**d['emt']);ref=run_reference(d['case'],r['time_s'],r['sample_side'])
    assert comparison(d,r,ref)['passed']
