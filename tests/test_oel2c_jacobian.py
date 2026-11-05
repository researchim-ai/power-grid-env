"""Continuous fixed-mode OEL2C derivatives, independent coordinate probes."""
import copy
import numpy as np
import pytest
from power_grid_env.oel2c_jacobian import rows,callback,KIND,power_slope
from power_grid_env.generator_oel2c import OEL2CPassport,OEL2CDynamics
from tests.test_esst1a_jacobian import ready,difference


@pytest.mark.parametrize('sw',[False,True])
@pytest.mark.parametrize('bounds',[(0,0),(-1,0),(1,1),(0,-1)])
@pytest.mark.parametrize('branch',['interior','pid_low','pid_high','lead2_low','lead2_high','output_low','output_high','inverse_low','inverse_high','deadband'])
def test_eight_component_rows_and_output(sw,bounds,branch):
    p=OEL2CPassport(generator_id='g',rated_field_reaction_pu=1.,Sw1=sw)
    k=OEL2CDynamics(p);k.initial(.8)
    k.context.update(timer_bound=bounds[0],reference_bound=bounds[1],thermal_low=False,thermal_high=branch!='deadband')
    x=np.array([.8,.1,1.,.75,.01,.0,.01,.01]);x[4]=-.08-(p.KpOel+p.KdOel/p.tDOel)*(x[3]-p.KAct*x[0])
    if branch=='pid_low':x[4]=-2.
    if branch=='pid_high':x[4]=2.
    if branch=='lead2_low':x[6]=2.
    if branch=='lead2_high':x[6]=-2.
    if branch=='output_low':x[7]=2.
    if branch=='output_high':x[7]=-2.
    if branch=='inverse_low':x[0]=.1;p.VInvMinPu=-.2
    if branch=='inverse_high':x[0]=2.2
    j,out=rows(k,x,np.eye(8),np.zeros(8));columns=[];outputs=[];saved=copy.deepcopy(k.context)
    for n in range(8):
        a=x.copy();b=x.copy();a[n]+=1e-7;b[n]-=1e-7
        columns.append((k.rhs(a,.9)-k.rhs(b,.9))/2e-7)
        outputs.append((k.quantities(a)['output']-k.quantities(b)['output'])/2e-7)
    assert np.max(abs(j-np.column_stack(columns)))<1e-5
    assert np.max(abs(out-outputs))<1e-6 and k.context==saved


@pytest.mark.parametrize('family',['cold','loaded','active','regulating','selected'])
@pytest.mark.parametrize('outage',['none','generator','fault','zero_field'])
def test_network_rows_all_coordinates(family,outage):
    if family=='cold':from benchmarks.generator_oel2c_cases import example
    if family=='loaded':from benchmarks.generator_oel2c_loaded_cases import example
    if family=='active':from benchmarks.generator_oel2c_active_cases import example
    if family=='regulating':from benchmarks.generator_oel2c_regulating_cases import example
    if family=='selected':from benchmarks.generator_oel2c_selected_cases import example
    m,x=ready(example('inactive' if family=='selected' else 'hold'));i=1
    # Interior output branch, avoiding an unrelated clip corner at zero output.
    for _,(a,p,k) in m.oel2c.entries.items():
        x[a+4]=-.07;x[a+6]=.01;x[a+7]=.01
    if outage in ('generator','zero_field'):m.gen_connected[i]=False
    if outage=='zero_field':m.field_thermal.field_enabled[i]=False
    if outage=='fault':m.fault_y[m.gen_buses[i]]=.1-.2j
    m.factorize();assert m.integration_jacobian().jacobian_kind==KIND
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<4e-5


def test_no_hidden_RHS_or_state_context_mutation(monkeypatch):
    from benchmarks.generator_oel2c_cases import example
    m,x=ready(example('hold'));saved=x.copy();context=copy.deepcopy([k.context for _,_,k in m.oel2c.entries.values()])
    monkeypatch.setattr(m,'rhs',lambda *a:pytest.fail('hidden FD'))
    assert np.isfinite(callback(m)(0.,x)).all();assert np.array_equal(x,saved)
    assert context==[k.context for _,_,k in m.oel2c.entries.values()]


@pytest.mark.parametrize('value,exponent,expected',[(0.,1.,1.),(0.,2.,0.),(2.,2.,4.),(-2.,2.,-4.)])
def test_power_slope(value,exponent,expected):assert power_slope(value,exponent)==expected


@pytest.mark.parametrize('value,exponent',[(0.,.5),(-1.,.5)])
def test_singular_or_nonreal_power_rejected(value,exponent):
    with pytest.raises(ValueError):power_slope(value,exponent)


@pytest.mark.parametrize('damage',['shape','nan','complex'])
def test_invalid_state_rejected(damage):
    from benchmarks.generator_oel2c_cases import example
    m,x=ready(example('hold'))
    if damage=='shape':x=x[:-1]
    if damage=='nan':x[0]=np.nan
    if damage=='complex':x=x.astype(complex)+1j
    with pytest.raises(ValueError,match='invalid'):callback(m)(0.,x)


@pytest.mark.parametrize('sign',[-1.,0.,1.])
def test_signed_field_measurement_and_zero_generalized_slope(sign):
    from benchmarks.generator_oel2c_cases import example
    m,x=ready(example('hold'));i=1;m.gen_connected[i]=False;m.factorize();machine=m.gen_passports[i];machine.saturation='disabled'
    a=m.round_rotors.addresses[i];x[a]=[0.,.2,0.,0.]
    def reaction(z):
        v=m.electrical(z)[0];return m.field_thermal.reaction(z,m.generator_currents(z,v))[i]
    base=reaction(x);y=x.copy();y[a.start]+=1.;gain=reaction(y)-base;x[a.start]=(sign*.5-base)/gain
    b,p,k=m.oel2c.entries[i];assert np.max(abs(callback(m)(0.,x)[b]-difference(m,x)[b]))<3e-5


@pytest.mark.parametrize('bound',['lower','upper'])
def test_inverse_clip_exact_corner_outward_column(bound):
    p=OEL2CPassport(generator_id='g',rated_field_reaction_pu=1.,VInvMinPu=-.75,VInvMaxPu=3.)
    k=OEL2CDynamics(p);k.initial(.8);k.context.update(timer_bound=0)
    x=np.array([.5 if bound=='lower' else 2.,.1,1.,.75,-.08,0.,.01,.01]);j,_=rows(k,x,np.eye(8),np.zeros(8))
    h=-1e-7 if bound=='lower' else 1e-7;y=x.copy();y[0]+=h
    assert abs(j[1,0]-(k.rhs(y,.9)[1]-k.rhs(x,.9)[1])/h)<1e-5


def test_inactive_fractional_singularity_is_masked_by_strict_inverse_clip():
    p=OEL2CPassport(generator_id='g',rated_field_reaction_pu=1.,C2=.5,VInvMinPu=-.2)
    k=OEL2CDynamics(p);k.initial(.8);x=np.array([0.,.1,1.,.75,-.08,0.,.01,.01])
    j,_=rows(k,x,np.eye(8),np.zeros(8));assert np.isfinite(j).all()


@pytest.mark.parametrize('name',['cold__two_limiters','cold__four_machines','loaded__two_limiters','active__two_limiters','active__mixed_modes','regulating__two_regulators','regulating__mixed_modes','selected__two_selected'])
def test_multiple_OELs_have_exact_continuous_rows_but_keep_FD_routing(name):
    from benchmarks.oel2c_jacobian_validation import case
    from power_grid_env.genrou_jacobian import jacobian
    m,x=ready(case(name))
    for _,(a,p,k) in m.oel2c.entries.items():x[a+4]=-.07;x[a+6]=.01;x[a+7]=.01
    assert callback(m) is None and m.integration_jacobian() is None
    assert np.max(abs(jacobian(m,x,oel=True)-difference(m,x)))<4e-5


def test_held_rows_do_not_evaluate_hidden_fractional_singularities():
    p=OEL2CPassport(generator_id='g',rated_field_reaction_pu=1.,C1=.5,C2=.5,Sw1=True)
    k=OEL2CDynamics(p);k.initial(.8);k.context.update(timer_bound=1,reference_bound=1,thermal_high=True)
    x=np.array([0.,.1,1.,.75,-.08,0.,.01,.01]);j,_=rows(k,x,np.eye(8),np.zeros(8))
    assert np.isfinite(j).all() and not j[1].any() and not j[2].any()


def test_comparison_rejects_changed_OEL_transition_identity():
    from benchmarks.coupled_jacobian_validation import compare
    r=dict(completed=True,time_s=[0.],sample_side=['sample'],channels={'x':[0.]},maximum_kcl_residual_pu=0.,maximum_power_balance_residual_pu=0.,event_log=[dict(kind='oel2c_transition',equipment_id='g',time_s=0.,transition='above',value=True)])
    other=copy.deepcopy(r);other['event_log'][0]['transition']='below'
    with pytest.raises(ValueError,match='event sequence'):compare(r,other)


def test_comparison_rejects_changed_OEL_boolean_branch_value():
    from benchmarks.coupled_jacobian_validation import compare
    r=dict(completed=True,time_s=[0.],sample_side=['sample'],channels={'x':[0.]},maximum_kcl_residual_pu=0.,maximum_power_balance_residual_pu=0.,event_log=[dict(kind='oel2c_transition',equipment_id='g',time_s=0.,transition='above',value=True)])
    other=copy.deepcopy(r);other['event_log'][0]['value']=False
    with pytest.raises(ValueError,match='event sequence'):compare(r,other)


@pytest.mark.parametrize('outage',['none','generator','fault'])
def test_OEL_on_SEXS_machine_with_ESST_PI_UEL_on_other_machine(outage):
    from benchmarks.generator_esst1a_cases import example
    from power_grid_env.excitation_takeover import TakeoverOELPassport
    from power_grid_env.generator_underexcitation import UnderexcitationLimiter
    from power_grid_env.rms_controls import IEEESTPassport
    s=example('hold');s.coupled.oel2c_limiters=[OEL2CPassport(generator_id='g',rated_field_reaction_pu=1.5,initialization='inactive_equilibrium')]
    s.coupled.takeover_oels=[TakeoverOELPassport(generator_id='g2',current_limit_pu=2.)]
    s.coupled.underexcitation_limiters=[UnderexcitationLimiter(generator_id='g2',Q_0=2.5,Radius=4.)]
    for m in s.coupled.machines:
        m.stabilizer=IEEESTPassport(A1=.2,A2=.02,A3=.3,A4=.03,A5=.05,A6=.001,T1=.1,T2=.05,T3=.1,T4=.05,T5=1.,T6=1.,KS=2.,LSMIN=-.1,LSMAX=.1,VCL=.8,VCU=1.2);m.stabilizer_connection='external_ESST1A' if m.generator_id=='g2' else 'SEXS'
    m,x=ready(s)
    for _,(a,p,k) in m.oel2c.entries.items():x[a+4]=-.07;x[a+6]=.01;x[a+7]=.01
    for _,(a,p) in m.underexcitation_limiters.entries.items():x[a]=p.MEL_MAX*.3
    if outage=='generator':m.gen_connected[0]=False
    if outage=='fault':m.fault_y[m.gen_buses[0]]=.1-.2j
    m.factorize();assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<4e-5
