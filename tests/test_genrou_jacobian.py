"""Full analytic network/machine/controller derivatives within declared modes."""
from pathlib import Path
import numpy as np
import pytest
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.coupled_rms import CoupledNetwork
from power_grid_env.coupled_initialization import initialize_coupled
from power_grid_env.genrou_jacobian import jacobian,KIND,saturation_slope


def example(name='hold',classical=False):
    return StudySpec.model_validate_json(Path('cases/studies/'+('regulated' if classical else 'genrou')+'_coupled_'+name+'.json').read_text())


def ready(spec):
    m=CoupledNetwork(spec.network,spec.coupled);x,meta=initialize_coupled(m)
    assert x is not None,meta
    return m,x


def difference(m,x,h=2e-7):
    cols=[]
    for i,v in enumerate(x):
        step=h*max(1.,abs(v));a=x.copy();b=x.copy();a[i]+=step;b[i]-=step
        cols.append((m.rhs(0.,a)-m.rhs(0.,b))/(2*step))
    return np.column_stack(cols)


@pytest.mark.parametrize('saturated',[False,True])
@pytest.mark.parametrize('classical',[False,True])
@pytest.mark.parametrize('outage',['none','motor','generator','fault'])
def test_each_state_derivative_in_joint_network(saturated,classical,outage):
    s=example('saturated_hold' if saturated and not classical else 'hold',classical)
    m,x=ready(s);x[0:3]=[.23,-.12,.81];x[m.gen_offset]+=.17;x[m.gen_offset+1]=1.007
    if outage=='motor':m.connected[:]=False
    if outage=='generator':m.gen_connected[:]=False
    if outage=='fault':m.fault_y[m.gen_buses[0]]=1.-2.j
    m.factorize()
    a=jacobian(m,x);b=difference(m,x)
    assert np.max(abs(a-b))<6e-6
    assert m.integration_jacobian().jacobian_kind==KIND


@pytest.mark.parametrize('target',['TGOV1','SEXS'])
@pytest.mark.parametrize('bound',['lower','upper'])
@pytest.mark.parametrize('mode',['held','outward','inward'])
def test_controller_bound_modes_are_differentiated_explicitly(target,bound,mode):
    m,x=ready(example());i=0
    if target=='TGOV1':
        address=m.controllers.gov[i][0];p=m.gen_passports[i].governor;low,high=p.VMIN,p.VMAX
        if mode=='held':m.controller_boundaries.active[address]=bound
        # Outside corners has a true local derivative, unlike central differences
        # through clipping. Held mode additionally projects this state's row.
        x[address]=(low-.05) if bound=='lower' else (high+.05)
        demand=x[address]+(-.1 if bound=='lower' else .1)*(1 if mode!='inward' else -1)
        m.controllers.pref[i]=demand+(x[m.gen_offset+1]-1)/p.R
    else:
        f,address=m.controllers.avr[i];p=m.gen_passports[i].exciter;low,high=p.EMIN,p.EMAX
        if mode=='held':m.controller_boundaries.active[address]=bound
        x[address]=low-.05 if bound=='lower' else high+.05
        terminal=abs(m.generator_terminal_voltage(x,m.electrical(x)[0])[i])
        desired=(-.1 if bound=='lower' else .1)*(1 if mode!='inward' else -1)
        # Force a signed nonzero free rate while preserving the actual TATB.
        if p.TATB:
            m.controllers.vref[i]=terminal+((x[address]+p.TE*desired)/p.K-(1-p.TATB)*x[f])/p.TATB
        else:x[f]=(x[address]+p.TE*desired)/p.K
    a=jacobian(m,x);b=difference(m,x)
    assert np.max(abs(a-b))<8e-6
    if mode=='held':assert not a[address].any()


@pytest.mark.parametrize('bound',['lower','upper'])
def test_exact_clip_corner_uses_outward_generalized_column(bound):
    m,x=ready(example());_,address=m.controllers.avr[0];p=m.gen_passports[0].exciter
    x[address]=p.EMIN if bound=='lower' else p.EMAX;m.controller_boundaries.active[address]=bound
    j=jacobian(m,x);h=(-1 if bound=='lower' else 1)*1e-7;y=x.copy();y[address]+=h
    expected=(m.rhs(0.,y)-m.rhs(0.,x))/h
    assert np.max(abs(j[:,address]-expected))<8e-6


@pytest.mark.parametrize('name',['hold','motor_load','voltage_sag','setpoint_steps','limits_recovery','thermal_trip','saturated_hold','saturated_sag'])
def test_controlled_round_rotor_matches_independent_physical_winding_reference(name):
    from benchmarks.genrou_coupled_reference import reference
    s=example(name);a=run_study(s);assert a['completed'],a
    assert a['integration']['jacobian']==KIND
    b=reference(s,a)
    for k,v in b['channels'].items():
        limit=100. if k.endswith(('_w','_var')) else 1e-4
        assert np.max(abs(np.asarray(a['channels'][k])-v))<limit,k


def test_response_cache_is_invalidated_but_references_and_modes_are_live():
    m,x=ready(example());jacobian(m,x);cache=m._round_voltage_response
    m.controllers.pref[0]+=.1;m.controllers.vref[0]+=.05
    assert np.max(abs(jacobian(m,x)-difference(m,x)))<8e-6
    assert m._round_voltage_response is cache
    m.gen_connected[:]=False;m.factorize()
    assert np.max(abs(jacobian(m,x)-difference(m,x)))<8e-6
    assert m._round_voltage_response is not cache


def test_analytic_callback_never_calls_RHS_or_mutates_state(monkeypatch):
    m,x=ready(example('saturated_hold'));before=x.copy()
    monkeypatch.setattr(CoupledNetwork,'rhs',lambda *a:pytest.fail('finite differences hidden inside analytic callback'))
    assert np.isfinite(jacobian(m,x)).all();assert np.array_equal(x,before)


@pytest.mark.parametrize('name',['generator_oel2c_hold','generator_oel2c_loaded_hold'])
def test_OEL2C_delegates_to_extended_profile(name):
    s=StudySpec.model_validate_json(Path('cases/studies/'+name+'.json').read_text());m=CoupledNetwork(s.network,s.coupled)
    assert m.integration_jacobian().jacobian_kind=='piecewise_analytic_OEL2C_network_reduction'
    with pytest.raises(ValueError,match='excludes'):jacobian(m,m.initial_state())


@pytest.mark.parametrize('outage',['none','motor','generator','fault'])
def test_mixed_four_machine_and_double_cage_cross_derivatives(outage):
    from benchmarks.ex03_cases import scale_case
    from power_grid_env.rms_study import ClassicalMachine
    from power_grid_env.generator_thermal import GeneratorThermalProtection
    from power_grid_env.motor_thermal import MotorThermalProtection
    s=scale_case(512,32,4);s.coupled.motors=s.coupled.motors[:4]
    old=s.coupled.machines[1]
    s.coupled.machines[1]=ClassicalMachine(generator_id=old.generator_id,rated_va=old.rated_va,inertia_s=old.inertia_s,x_d_prime_pu=old.x_d_prime_pu,damping_pu=old.damping_pu,governor=old.governor,exciter=old.exciter)
    motor=s.coupled.motors[0];motor.model='double_cage';motor.rotor2_r_pu=.07;motor.rotor2_x_pu=.12
    motor.thermal=MotorThermalProtection(time_constant_s=3.,trip_level=10.)
    s.coupled.generator_thermal=[GeneratorThermalProtection(generator_id='g2',time_constant_s=5.,trip_level=10.)]
    m,x=ready(s);m.connected[:]=True
    for i in range(4):x[3*i:3*i+3]=[.2,-.04*i,.75+.03*i];x[m.gen_offset+2*i]+=.08*i
    x[m.cages.addresses[0]]=[.03,.02]
    if outage=='motor':m.connected[0]=False
    if outage=='generator':m.gen_connected[2]=False
    if outage=='fault':m.fault_y[m.gen_buses[1]]=2.-3.j
    m.factorize()
    assert np.max(abs(jacobian(m,x)-difference(m,x)))<1e-5


@pytest.mark.parametrize('flux',[0.,.2,.8,1.,1.4])
def test_quadratic_saturation_derivative_matches_scalar_definition(flux):
    from power_grid_env.coupled_generators import saturation
    m=example('saturated_hold').coupled.machines[0];h=1e-7
    expected=(saturation(m,flux+h)-saturation(m,flux-h))/(2*h)
    assert saturation_slope(m,flux)==pytest.approx(expected,abs=2e-6)


def test_zero_flux_with_negative_saturation_threshold_is_explicitly_singular():
    m=example('saturated_hold').coupled.machines[0];m.saturation_s10=.3;m.saturation_s12=.31
    with pytest.raises(ValueError,match='zero flux'):saturation_slope(m,0.)


@pytest.mark.parametrize('damage',['shape','nan','complex'])
def test_nonphysical_state_does_not_produce_a_usable_Jacobian(damage):
    m,x=ready(example())
    if damage=='shape':x=x[:-1]
    if damage=='nan':x[0]=np.nan
    if damage=='complex':x=x.astype(complex)+1j
    with pytest.raises(ValueError,match='invalid'):jacobian(m,x)


@pytest.mark.parametrize('field',['controller','bound','port'])
def test_event_comparison_cannot_hide_wrong_controller_or_limit_side(field):
    import copy
    from benchmarks.coupled_jacobian_validation import compare
    a=dict(completed=True,time_s=[0.],sample_side=['initial'],channels={'g.omega':[1.]},maximum_kcl_residual_pu=0.,maximum_power_balance_residual_pu=0.,
           event_log=[dict(time_s=.1,kind='controller_limit_enter',equipment_id='g',controller='SEXS',bound='upper')])
    b=copy.deepcopy(a);b['event_log'][0][field]='different'
    with pytest.raises(ValueError,match='sequence'):compare(a,b)


def test_zero_terminal_voltage_is_not_silently_regularized():
    m,x=ready(example());m.gen_connected[:]=False
    x[m.round_rotors.addresses[0]]=0.;m.factorize()
    with pytest.raises(ValueError,match='zero voltage'):jacobian(m,x)


def test_failed_analytic_evaluation_does_not_fall_back_or_return_partial_trace(monkeypatch):
    def fail(*a):raise ValueError('injected invalid derivative domain')
    monkeypatch.setattr('power_grid_env.genrou_jacobian.jacobian',fail)
    r=run_study(example())
    assert not r['completed'] and r['status']=='numerically_not_converged'
    assert 'channels' not in r and 'injected' in r['solver_message']


@pytest.mark.parametrize('governor,exciter',[(False,False),(True,False),(False,True),(True,True)])
def test_fixed_field_and_optional_controller_combinations(governor,exciter):
    s=example('saturated_hold');p=s.coupled.machines[0]
    if not governor:p.governor=None
    if not exciter:p.exciter=None
    m,x=ready(s);x[0:3]=[.18,-.07,.86];x[m.gen_offset]+=.1
    assert np.max(abs(jacobian(m,x)-difference(m,x)))<8e-6
    assert m.integration_jacobian().jacobian_kind==KIND
