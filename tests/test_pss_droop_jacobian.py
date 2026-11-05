"""Continuous IEEEST/droop-OEL derivatives, frozen gates and explicit exclusions."""
from pathlib import Path
import numpy as np
import pytest
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.coupled_rms import CoupledNetwork
from power_grid_env.coupled_initialization import initialize_coupled
from power_grid_env.pss_droop_jacobian import callback,KIND,pss_rows


def pss_case(name='fault'):
    return StudySpec.model_validate_json(Path('cases/studies/pss_coupled_'+name+'.json').read_text())


def ready(s):
    m=CoupledNetwork(s.network,s.coupled);x,init=initialize_coupled(m);assert x is not None,init
    return m,x


def difference(m,x,h=1e-7):
    cols=[]
    for j,v in enumerate(x):
        step=h*max(1.,abs(v));a=x.copy();b=x.copy();a[j]+=step;b[j]-=step
        cols.append((m.rhs(0.,a)-m.rhs(0.,b))/(2*step))
    return np.column_stack(cols)


@pytest.mark.parametrize('gate',['on','below','above'])
@pytest.mark.parametrize('limited',['interior','lower','upper'])
@pytest.mark.parametrize('name',['fault','saturated_fault','classical_fault'])
def test_PSS_rows_and_closed_feedback_all_coordinates(gate,limited,name):
    m,x=ready(pss_case(name));i=0;p=m.gen_passports[i].stabilizer;a=m.controllers.pss_addresses[i]
    x[m.gen_offset+1]=1.003;x[a]=[.001,-.002,.003,.001,-.001,.002,.001]
    terminal=m.generator_terminal_voltage(x,m.electrical(x)[0]);v=abs(terminal[i])
    if gate=='below':p.VCL=v+.2;p.VCU=v+.4
    elif gate=='above':p.VCL=.1*v;p.VCU=.5*v
    else:p.VCL=.5*v;p.VCU=1.5*v
    raw=m.controllers.pss.evaluate(x,x[m.gen_offset:m.rotor_end].reshape(-1,2)[:,1],terminal)[2]['g.IEEEST.raw_pu']
    target=0. if limited=='interior' else p.LSMIN-.02 if limited=='lower' else p.LSMAX+.02
    x[a.stop-1]+=(raw-target)*p.T6/p.T5
    actual=callback(m)(0.,x);expected=difference(m,x)
    assert np.max(abs(actual-expected))<1e-5
    assert m.integration_jacobian().jacobian_kind==KIND


@pytest.mark.parametrize('bias_branch',['below','interior','above'])
@pytest.mark.parametrize('target_branch',['inactive','rising','capped'])
@pytest.mark.parametrize('outage',['none','generator'])
def test_droop_OEL_all_coordinates_on_each_limiter_branch(bias_branch,target_branch,outage):
    from benchmarks.generator_excitation_cases import example
    m,x=ready(example('saturated'));i=1;a,p=m.excitation_limiters.entries[i]
    x[a]=p.current_limit_pu+({'inactive':-.1,'rising':.25*p.maximum_voltage_bias_pu/p.feedback_gain_pu,'capped':2*p.maximum_voltage_bias_pu/p.feedback_gain_pu}[target_branch])
    x[a+1]={'below':-.1,'interior':p.maximum_voltage_bias_pu*.4,'above':p.maximum_voltage_bias_pu+.1}[bias_branch]
    x[m.round_rotors.addresses[i].start]+=.12
    if outage=='generator':m.gen_connected[i]=False;m.factorize()
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<2e-5


def test_seven_state_rows_match_direct_component_finite_difference():
    m,x=ready(pss_case());p=m.gen_passports[0].stabilizer;a=m.controllers.pss_addresses[0];omega=m.gen_offset+1
    x[a]=np.arange(7)*.0001;x[omega]=1.002
    rows,raw=pss_rows(p,np.eye(len(x)),a,omega)
    for j in [*range(a.start,a.stop),omega]:
        h=1e-7;u=x.copy();v=x.copy();u[j]+=h;v[j]-=h
        def component(z):return m.controllers.pss.evaluate(z,np.array([z[omega]]),np.array([1.]))
        left,right=component(u),component(v)
        assert np.max(abs(rows[:,j]-(left[0][0]-right[0][0])/(2*h)))<1e-6
        assert raw[j]==pytest.approx((left[2]['g.IEEEST.raw_pu']-right[2]['g.IEEEST.raw_pu'])/(2*h),abs=1e-6)


def test_callback_does_not_call_RHS_or_change_state(monkeypatch):
    m,x=ready(pss_case());saved=x.copy()
    monkeypatch.setattr(CoupledNetwork,'rhs',lambda *a:pytest.fail('hidden finite differences'))
    assert np.isfinite(callback(m)(0.,x)).all() and np.array_equal(saved,x)


@pytest.mark.parametrize('name',['generator_oel2c_hold','generator_oel2c_loaded_hold'])
def test_OEL2C_delegates_from_PSS_droop_profile(name):
    path=Path('cases/studies')/(name+'.json')
    s=StudySpec.model_validate_json(path.read_text());m=CoupledNetwork(s.network,s.coupled)
    assert callback(m) is None and m.integration_jacobian().jacobian_kind=='piecewise_analytic_OEL2C_network_reduction'


@pytest.mark.parametrize('name',['joint_hold','joint_fault','joint_saturated_fault','joint_gate'])
def test_joint_feedback_matches_independent_SI_and_polynomial_PSS(name):
    from benchmarks.pss_droop_jacobian_validation import case
    from benchmarks.coupled_pss_reference import reference
    s=case(name);a=run_study(s);assert a['completed'],a
    b=reference(s,a,allow_droop=True)
    assert b['independent_physical_SI'] and b['droop_OEL_in_closed_feedback']
    for key,values in b['channels'].items():
        limit=100. if key.endswith(('_w','_var')) else 1e-4
        assert np.max(abs(np.asarray(a['channels'][key])-values))<limit,key
    if name!='joint_hold':assert max(a['channels']['g.OEL.bias_pu'])>.0001


def test_oracle_never_silently_removes_joint_OEL():
    from benchmarks.pss_droop_jacobian_validation import case
    from benchmarks.coupled_pss_reference import reference
    with pytest.raises(ValueError,match='opt-in'):reference(case('joint_hold'),{})
    with pytest.raises(ValueError,match='opt-in'):reference(case('joint_hold'),linearize=True,allow_droop=True)


@pytest.mark.parametrize('bound',['lower','upper'])
def test_PSS_exact_output_corner_has_outward_generalized_column(bound):
    m,x=ready(pss_case('limits'));a=m.controllers.pss_addresses[0];p=m.gen_passports[0].stabilizer
    unit=np.eye(len(x));_,draw=pss_rows(p,unit,a,m.gen_offset+1)
    # Set a single washout state so the production expression lands on its
    # exact clipping constant; use a power-of-two T5/T6 ratio for exactness.
    p.T5=p.T6;limit=p.LSMIN if bound=='lower' else p.LSMAX
    x[a]=0.;x[a.stop-1]=-limit
    j=callback(m)(0.,x);h=1e-8*(1 if bound=='lower' else -1);y=x.copy();y[a.stop-1]+=h
    expected=(m.rhs(0.,y)-m.rhs(0.,x))/h
    assert np.max(abs(j[:,a.stop-1]-expected))<2e-5


@pytest.mark.parametrize('bound',['lower','upper'])
def test_strict_PSS_gate_boundary_is_differentiated_with_frozen_off_gate(bound):
    m,x=ready(pss_case());p=m.gen_passports[0].stabilizer;a=m.controllers.pss_addresses[0]
    x[a.stop-1]=.001
    v=abs(m.generator_terminal_voltage(x,m.electrical(x)[0])[0])
    if bound=='lower':p.VCL=v;p.VCU=v+.3
    else:p.VCL=v*.5;p.VCU=v
    j=callback(m)(0.,x)
    # A reference with gate forced off on a neighbourhood has the derivative
    # of this frozen branch. This is not differentiation through a jump.
    p.VCL=v+.2;p.VCU=v+.4
    assert np.max(abs(j-difference(m,x)))<1e-5


def test_topology_and_live_gate_changes_do_not_reuse_state_sensitivities():
    m,x=ready(pss_case());callback(m)(0.,x);cache=m._round_voltage_response
    p=m.gen_passports[0].stabilizer;p.VCL=2.;p.VCU=3.
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<1e-5
    assert m._round_voltage_response is cache
    m.gen_connected[:]=False;m.factorize()
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<1e-5
    assert m._round_voltage_response is not cache


@pytest.mark.parametrize('sign',['positive','zero','negative'])
def test_signed_field_reaction_and_zero_generalized_abs_slope(sign):
    from benchmarks.generator_excitation_cases import example
    m,x=ready(example('hold'));i=1;m.gen_connected[i]=False;m.factorize()
    a=m.round_rotors.addresses[i]
    oel_address,_=m.excitation_limiters.entries[i];x[oel_address+1]=.02
    x[a]=[.3,.2,.2,.2] if sign=='positive' else [0.,.2,0.,.2] if sign=='zero' else [-.3,.2,-.2,.2]
    v=m.electrical(x)[0];reaction=m.excitation_limiters.reaction(x,m.generator_currents(x,v))[i]
    assert (reaction==0 if sign=='zero' else reaction>0 if sign=='positive' else reaction<0)
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<2e-5


@pytest.mark.parametrize('outage',['none','motor','generator','fault'])
def test_joint_multimachine_PSS_droop_double_cage_and_thermal_derivatives(outage):
    from benchmarks.pss_droop_jacobian_validation import case
    from power_grid_env.motor_thermal import MotorThermalProtection
    from power_grid_env.generator_thermal import GeneratorThermalProtection
    s=case('scale_512_32_4');s.coupled.motors=s.coupled.motors[:4]
    motor=s.coupled.motors[0];motor.model='double_cage';motor.rotor2_r_pu=.07;motor.rotor2_x_pu=.12
    motor.thermal=MotorThermalProtection(time_constant_s=3.,trip_level=10.)
    s.coupled.generator_thermal=[GeneratorThermalProtection(generator_id='g2',time_constant_s=5.,trip_level=10.)]
    m,x=ready(s);m.connected[:]=True
    for i in range(4):
        x[3*i:3*i+3]=[.2,.02*i,.8]
        x[m.controllers.pss_addresses[i]]=[.0001,-.0002,.0003,0.,0.,.0001,0.]
    x[m.cages.addresses[0]]=[.03,.02]
    for i,(a,p) in m.excitation_limiters.entries.items():x[a:a+2]=[p.current_limit_pu+.1,.01]
    if outage=='motor':m.connected[0]=False
    if outage=='generator':m.gen_connected[0]=False
    if outage=='fault':m.fault_y[m.gen_buses[1]]=1.-2.j
    m.factorize()
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<3e-5


def test_failed_extended_Jacobian_does_not_return_a_partial_success(monkeypatch):
    def fail(*a,**k):raise ValueError('injected extended derivative error')
    monkeypatch.setattr('power_grid_env.genrou_jacobian.jacobian',fail)
    r=run_study(pss_case())
    assert not r['completed'] and r['status']=='numerically_not_converged' and 'channels' not in r


def test_joint_SI_oracle_does_not_use_primary_windings_or_RHS(monkeypatch):
    from benchmarks.pss_droop_jacobian_validation import case
    from benchmarks.coupled_pss_reference import reference
    s=case('joint_hold');s.coupled.duration_s=.03;a=run_study(s);assert a['completed']
    def forbidden(*a,**k):raise AssertionError('primary physics called from independent oracle')
    monkeypatch.setattr(CoupledNetwork,'rhs',forbidden)
    monkeypatch.setattr('power_grid_env.coupled_generators.RoundRotorBank.field_reaction',forbidden)
    monkeypatch.setattr('power_grid_env.induction.InductionMotor.constants',forbidden)
    assert reference(s,a,allow_droop=True)['droop_OEL_in_closed_feedback']


def test_joint_breaker_case_retains_field_feedback_but_oracle_rejects_unsupported_topology():
    from benchmarks.pss_droop_jacobian_validation import case
    from benchmarks.coupled_pss_reference import reference
    s=case('joint_breaker_open');r=run_study(s);assert r['completed'],r
    assert r['channels']['g.current_pu'][-1]==0. and r['channels']['g.OEL.field_current_pu'][-1]>.01
    assert r['integration']['jacobian']==KIND
    with pytest.raises(ValueError,match='unsupported event'):reference(s,r,allow_droop=True)


@pytest.mark.parametrize('corner',['bias_zero','bias_max','target_zero','target_max'])
def test_droop_exact_corners_use_explicit_outward_generalized_derivative(corner):
    from benchmarks.generator_excitation_cases import example
    m,x=ready(example('hold'));a,p=m.excitation_limiters.entries[1]
    p.current_limit_pu=2.;p.feedback_gain_pu=.5;p.maximum_voltage_bias_pu=.25
    x[a]=2.25;x[a+1]=.1
    if corner.startswith('bias'):
        address=a+1;x[address]=0. if corner=='bias_zero' else .25
        h=-1e-7 if corner=='bias_zero' else 1e-7
    else:
        address=a;x[address]=2. if corner=='target_zero' else 2.5
        h=-1e-7 if corner=='target_zero' else 1e-7
    j=callback(m)(0.,x);y=x.copy();y[address]+=h
    assert np.max(abs(j[:,address]-(m.rhs(0.,y)-m.rhs(0.,x))/h))<2e-5
