"""Canonical controls and faults against independent ODE and transfer functions."""
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from scipy import signal
from test_canonical_rms import example
from power_grid_env.rms_controls import TGOV1Passport,SEXSPassport,IEEESTPassport
from power_grid_env.rms_study import RMSFault,RMSEvent,RMSSettings
from power_grid_env.study_spec import run_study
from power_grid_env.topology import compile_topology


def controlled():
    s=example();s.rms.duration_s=.8;s.rms.step_s=.001
    m=s.rms.machines[0]
    m.governor=TGOV1Passport(R=.05,T1=.1,T2=.2,T3=2.,VMIN=0.,VMAX=1.2,Dt=0.)
    m.exciter=SEXSPassport(TATB=.4,TB=5.,K=20.,TE=.1,EMIN=0.,EMAX=5.)
    m.stabilizer=IEEESTPassport(A1=.1,A2=.01,A3=.1,A4=.01,A5=.05,A6=.005,
        T1=.1,T2=.2,T3=.1,T4=.2,T5=1.,T6=1.,KS=1.,LSMIN=-.1,LSMAX=.1,VCL=.5,VCU=1.5)
    s.rms.events=[RMSEvent(time_s=.1,kind='governor_step',equipment_id='g',value=.01)]
    return s


def analytic_initial(s):
    # Lossless single-machine fixture: entirely independent analytic PF.
    theta=np.arcsin(.5*.3/1.05)
    v=1.05*np.exp(1j*theta)
    e=v+1j*.3*(v-1)/(1j*.3)
    return abs(e),np.angle(e)


def test_closed_loop_governor_matches_independent_four_state_ode():
    s=controlled();m=s.rms.machines[0];m.exciter=None;m.stabilizer=None
    r=run_study(s);assert r['completed'],r
    e,delta=analytic_initial(s);times=np.array(r['time_s'])
    reference=np.empty((len(times),4));state=[delta,1.,.5,.5]
    for left,right,pref in [(0,.1,.5),(.1,.8,.51)]:
        def rhs(t,x):
            angle,speed,valve,lag=x
            pd=pref-(speed-1)/.05
            pout=.1*valve+.9*lag
            return [2*np.pi*50*(speed-1),(pout-e/.6*np.sin(angle))/8,
                    (pd-valve)/.1,(valve-lag)/2.]
        sol=solve_ivp(rhs,(left,right),state,rtol=1e-11,atol=1e-12,dense_output=True)
        mask=(times>=left)&(times<=right);reference[mask]=sol.sol(times[mask]).T;state=sol.y[:,-1]
    assert max(abs(reference[:,0]-r['channels']['g.delta']))<1e-5
    assert max(abs(reference[:,1]-r['channels']['g.omega']))<1e-6
    expected_pout=.1*reference[:,2]+.9*reference[:,3]
    assert max(abs(expected_pout-r['channels']['g.TGOV1.pout']))<1e-6
    assert max(abs(np.array(r['channels']['g.tm'])-r['channels']['g.TGOV1.pout']))<1e-8


def replay_transfer(num,den,time,data):
    grid=np.linspace(0,time[-1],int(np.ceil(time[-1]/1e-5))+1)
    u=np.interp(grid,time,data)
    _,ref,_=signal.lsim((num,den),u,grid)
    return np.interp(time,grid,ref)


def test_sexs_and_pss_match_transfer_functions_on_measured_inputs():
    s=controlled();r=run_study(s);assert r['completed'],r
    c=r['channels'];t=np.array(r['time_s']);avr=s.rms.machines[0].exciter;p=s.rms.machines[0].stabilizer
    vi=np.array(c['g.SEXS.vi']);field=np.array(c['g.SEXS.vout'])
    reference=replay_transfer([avr.K*avr.TATB*avr.TB,avr.K],
                             np.polymul([avr.TB,1],[avr.TE,1]),t,vi-vi[0])+field[0]
    assert max(abs(field-reference))<1e-4
    num=np.polymul([p.A6,p.A5,1],[p.T1,1]);num=np.polymul(num,[p.T3,1]);num=np.polymul(num,[p.KS*p.T5,0])
    den=np.polymul([p.A2,p.A1,1],[p.A4,p.A3,1]);den=np.polymul(den,[p.T2,1]);den=np.polymul(den,[p.T4,1]);den=np.polymul(den,[p.T6,1])
    expected=replay_transfer(num,den,t,np.array(c['g.IEEEST.sig']))
    assert max(abs(expected-c['g.IEEEST.WO_y']))<1e-6
    assert min(c['g.IEEEST.vsout'])>=p.LSMIN-1e-8 and max(c['g.IEEEST.vsout'])<=p.LSMAX+1e-8
    assert min(field)>avr.EMIN and max(field)<avr.EMAX


def test_fault_and_clearing_match_independent_network_swing_ode():
    errors=[]
    for step in [.004,.002]:
        s=example(step=step);s.rms.events=[];s.rms.faults=[RMSFault(node_id='gen',apply_s=.1,clear_s=.16,r_ohm=0.,x_ohm=24.2)]
        r=run_study(s);assert r['completed'],r
        e,delta=analytic_initial(s);times=np.array(r['time_s']);reference=np.empty((len(times),2));state=[delta,1.]
        for left,right,fault in [(0,.1,False),(.1,.16,True),(.16,.4,False)]:
            def rhs(t,x):
                emf=e*np.exp(1j*x[0]);ys=1/(.3j);yg=1/(.3j);yf=1/(.2j) if fault else 0.
                voltage=(emf*yg+ys)/(yg+ys+yf)
                pe=(emf*np.conj((emf-voltage)*yg)).real
                return [2*np.pi*50*(x[1]-1),(.5-pe)/8]
            sol=solve_ivp(rhs,(left,right),state,rtol=1e-11,atol=1e-12,dense_output=True)
            mask=(times>=left)&(times<=right);reference[mask]=sol.sol(times[mask]).T;state=sol.y[:,-1]
        errors.append(max(abs(reference[:,0]-r['channels']['g.delta'])))
        assert max(abs(reference[:,1]-r['channels']['g.omega']))<1e-5
        bus=compile_topology(s.network).node_to_bus['gen'];u=np.array(r['channels'][bus+'.v'])
        assert min(u[(times>.11)&(times<.15)])<.8
        assert u[-1]>.95
    assert errors[1]<errors[0]<1e-4


def test_regulated_fault_recovery_and_machine_base_invariance():
    s=controlled();s.rms.faults=[RMSFault(node_id='gen',apply_s=.4,clear_s=.46,r_ohm=0.,x_ohm=24.2)]
    a=run_study(s);assert a['completed'],a
    b=s.model_copy(deep=True);b.network.s_base_va=50e6
    r=run_study(b);assert r['completed'],r
    assert max(abs(np.interp(a['time_s'],r['time_s'],r['channels']['g.delta'])-a['channels']['g.delta']))<1e-6
    assert np.ptp(a['channels']['g.SEXS.vout'])>.01


def test_invalid_faults_and_controller_events_are_rejected():
    s=controlled();s.rms.events=[RMSEvent(time_s=.1,kind='mechanical_step',equipment_id='g',value=.01)]
    assert run_study(s)['reason']=='rms_scope'
    s=controlled();s.rms.faults=[RMSFault(node_id='absent',apply_s=.1,clear_s=.2,r_ohm=1.,x_ohm=0.)]
    assert run_study(s)['reason']=='rms_scope'
    with pytest.raises(ValueError):RMSFault(node_id='x',apply_s=.2,clear_s=.1,r_ohm=1.,x_ohm=0.)
    with pytest.raises(ValueError):RMSFault(node_id='x',apply_s=.1,clear_s=.2,r_ohm=0.,x_ohm=0.)
    with pytest.raises(ValueError):RMSSettings(machines=[],duration_s=1.,faults=[
        RMSFault(node_id='x',apply_s=.1,clear_s=.3,r_ohm=1.,x_ohm=0.),
        RMSFault(node_id='y',apply_s=.2,clear_s=.4,r_ohm=1.,x_ohm=0.)])


def test_actuator_saturation_and_recovery_are_reported_without_violating_limits():
    s=controlled();s.rms.duration_s=1.;m=s.rms.machines[0]
    m.governor.VMAX=.55;m.exciter.EMAX=1.5
    m.stabilizer.LSMIN=-1e-5;m.stabilizer.LSMAX=1e-5
    s.rms.events=[RMSEvent(time_s=.1,kind='governor_step',equipment_id='g',value=1.),
                  RMSEvent(time_s=.2,kind='voltage_reference_step',equipment_id='g',value=1.),
                  RMSEvent(time_s=.5,kind='governor_step',equipment_id='g',value=-1.),
                  RMSEvent(time_s=.6,kind='voltage_reference_step',equipment_id='g',value=-1.)]
    r=run_study(s);assert r['completed'],r
    limits=r['controller_limits']
    assert limits['g.TGOV1.LAG_y']['samples_at_upper']>0
    assert limits['g.SEXS.vout']['samples_at_upper']>0
    assert r['channels']['g.TGOV1.LAG_y'][-1]<.55-1e-3
    assert r['channels']['g.SEXS.vout'][-1]<1.5-1e-3
    assert r['control_limit_violations']==[]


def test_generator_trip_with_controllers_does_not_keep_injecting_power():
    s=controlled();s.rms.events=[RMSEvent(time_s=.2,kind='generator_trip',equipment_id='g')]
    r=run_study(s);assert r['completed'],r
    assert abs(r['channels']['g.p_w'][-1])<1.
    assert abs(r['channels']['g.TGOV1.pout'][-1])<1e-8
    assert abs(r['channels']['g.SEXS.vout'][-1])<1e-8


def test_full_regulated_fault_has_independent_coupled_reference():
    from benchmarks.regulated_rms import independent_reference
    s=controlled()
    s.rms.events.append(RMSEvent(time_s=.2,kind='voltage_reference_step',equipment_id='g',value=.01))
    s.rms.faults=[RMSFault(node_id='gen',apply_s=.4,clear_s=.46,r_ohm=0.,x_ohm=24.2)]
    report=run_study(s);assert report['completed'],report
    expected=independent_reference(s,np.array(report['time_s']))
    assert max(abs(expected[:,0]-report['channels']['g.delta']))<1e-4
    assert max(abs(expected[:,1]-report['channels']['g.omega']))<1e-5
    assert max(abs(expected[:,5]-report['channels']['g.SEXS.vout']))<1e-3
    assert max(abs(expected[:,2]-report['channels']['g.TGOV1.LAG_y']))<1e-4
