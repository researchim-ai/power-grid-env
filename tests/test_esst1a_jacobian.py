"""ESST1A selector/moving-rectifier derivatives and PI back-calculation."""
import numpy as np
import pytest
from power_grid_env.study_spec import run_study
from power_grid_env.coupled_rms import CoupledNetwork
from power_grid_env.coupled_initialization import initialize_coupled
from power_grid_env.esst1a_jacobian import callback,KIND
from benchmarks.generator_esst1a_cases import example


def ready(s):
    m=CoupledNetwork(s.network,s.coupled);x,init=initialize_coupled(m);assert x is not None,init
    return m,x


def difference(m,x,h=1e-7):
    cols=[]
    for j,v in enumerate(x):
        step=h*max(1.,abs(v));a=x.copy();b=x.copy();a[j]+=step;b[j]-=step
        cols.append((m.rhs(0.,a)-m.rhs(0.,b))/(2*step))
    return np.column_stack(cols)


@pytest.mark.parametrize('branch',['interior','regulator_lower','regulator_upper','current_limit','error_lower','error_upper','uel_error','uel_field','oel','rectifier_lower','rectifier_upper'])
@pytest.mark.parametrize('outage',['none','generator','fault'])
def test_all_ESST1A_branches_match_full_coordinate_derivatives(branch,outage):
    m,x=ready(example('hold'));i=1;a,p=m.esst1a.entries[i]
    if outage=='generator':m.gen_connected[i]=False
    if outage=='fault':m.fault_y[m.gen_buses[i]]=.1-.2j
    m.factorize();ports=m.esst1a.ports[i]
    if branch=='regulator_lower':x[a+3]=p.V_AMIN-.2
    if branch=='regulator_upper':x[a+3]=p.V_AMAX+.2
    if branch=='current_limit':p.I_LR=.1
    if branch=='error_lower':x[a]=4.
    if branch=='error_upper':x[a]=-.5
    if branch=='uel_error':ports['uel_error_floor_pu']=.5
    if branch=='uel_field':ports['uel_field_floor_pu']=2.5
    if branch=='oel':ports['oel_ceiling_pu']=.2
    if branch=='rectifier_lower':p.V_RMIN=1.5;p.V_RMAX=4.;ports['oel_ceiling_pu']=.1
    if branch=='rectifier_upper':p.V_RMAX=1.;ports['uel_field_floor_pu']=3.
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<2e-5
    assert m.integration_jacobian().jacobian_kind==KIND


@pytest.mark.parametrize('branch',['lower','interior','upper'])
@pytest.mark.parametrize('selector',['external','PI'])
def test_absolute_PI_sensor_back_calculation_and_effective_cap(branch,selector):
    from benchmarks.ex02_cases import modal
    s=modal('avr_pss_oel');m,x=ready(s);i=1;a,p=m.takeover_oel.entries[i]
    target=p.minimum_field_pu-.3 if branch=='lower' else (p.minimum_field_pu+p.maximum_field_pu)/2 if branch=='interior' else p.maximum_field_pu+.3
    x[a+1]=target-p.proportional_gain*(p.current_limit_pu-x[a])
    m.esst1a.ports[i]['oel_ceiling_pu']=.1 if selector=='external' else 100.
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<3e-5


def test_raw_regulator_windout_row_is_not_frozen_by_region_memory():
    m,x=ready(example('hold'));a,p=m.esst1a.entries[1];x[a+3]=p.V_AMAX+.3
    before=callback(m)(0.,x)
    m.esst1a.regions[1]=1
    after=callback(m)(0.,x)
    assert np.array_equal(before,after) and np.linalg.norm(after[a+3])>0.
    assert np.max(abs(after-difference(m,x)))<2e-5


def test_ESST1A_callback_does_not_call_RHS_or_mutate_state(monkeypatch):
    m,x=ready(example('hold'));saved=x.copy()
    monkeypatch.setattr(CoupledNetwork,'rhs',lambda *a:pytest.fail('hidden finite differences'))
    assert np.isfinite(callback(m)(0.,x)).all() and np.array_equal(x,saved)


@pytest.mark.parametrize('name',['field_trip','uel_summation'])
def test_protections_delegate_to_extended_jacobian(name):
    s=example(name);m=CoupledNetwork(s.network,s.coupled)
    from power_grid_env.protection_jacobian import KIND as protection_kind
    assert callback(m) is None and m.integration_jacobian().jacobian_kind==protection_kind


@pytest.mark.parametrize('outage',['none','motor','generator','fault'])
def test_many_ESST1A_machines_PSS_PI_and_double_cage_network(outage):
    from benchmarks.esst1a_jacobian_validation import case
    from power_grid_env.motor_thermal import MotorThermalProtection
    from power_grid_env.generator_thermal import GeneratorThermalProtection
    s=case('scale_512_32_4');s.coupled.motors=s.coupled.motors[:4]
    motor=s.coupled.motors[0];motor.model='double_cage';motor.rotor2_r_pu=.07;motor.rotor2_x_pu=.12
    motor.thermal=MotorThermalProtection(time_constant_s=3.,trip_level=10.)
    s.coupled.generator_thermal=[GeneratorThermalProtection(generator_id='g2',time_constant_s=5.,trip_level=10.)]
    m,x=ready(s);m.connected[:]=True
    for i in range(4):x[3*i:3*i+3]=[.2,.02*i,.8]
    x[m.cages.addresses[0]]=[.03,.02]
    if outage=='motor':m.connected[0]=False
    if outage=='generator':m.gen_connected[0]=False
    if outage=='fault':m.fault_y[m.gen_buses[1]]=.1-.2j
    m.factorize()
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<3e-5


@pytest.mark.parametrize('target',['lower','upper'])
def test_PI_clip_corner_uses_outward_generalized_column(target):
    from benchmarks.ex02_cases import modal
    m,x=ready(modal('avr_pss_oel'));a,p=m.takeover_oel.entries[1]
    p.proportional_gain=2.;p.minimum_field_pu=0.;p.maximum_field_pu=4.;p.current_limit_pu=2.
    x[a]=2.;x[a+1]=0. if target=='lower' else 4.
    # Avoid an unrelated exact tie between the static and dynamic cap.
    m.esst1a.ports[1]['oel_ceiling_pu']=100.
    j=callback(m)(0.,x);h=-1e-7 if target=='lower' else 1e-7;y=x.copy();y[a+1]+=h
    assert np.max(abs(j[:,a+1]-(m.rhs(0.,y)-m.rhs(0.,x))/h))<3e-5


@pytest.mark.parametrize('damage',['shape','nan','complex'])
def test_bad_state_is_not_an_analytic_result(damage):
    m,x=ready(example('hold'))
    if damage=='shape':x=x[:-1]
    if damage=='nan':x[0]=np.nan
    if damage=='complex':x=x.astype(complex)+1j
    with pytest.raises(ValueError,match='invalid'):callback(m)(0.,x)


def test_failed_ESST1A_derivative_does_not_silently_retry_on_FD(monkeypatch):
    def fail(*a,**k):raise ValueError('injected ESST1A derivative failure')
    monkeypatch.setattr('power_grid_env.esst1a_jacobian.assemble',fail)
    r=run_study(example('hold'))
    assert not r['completed'] and r['status']=='numerically_not_converged' and 'channels' not in r


@pytest.mark.parametrize('field',['gate','active'])
def test_event_comparison_cannot_hide_wrong_ESST1A_gate(field):
    import copy
    from benchmarks.coupled_jacobian_validation import compare
    a=dict(completed=True,time_s=[0.],sample_side=['initial'],channels={'g.omega':[1.]},maximum_kcl_residual_pu=0.,maximum_power_balance_residual_pu=0.,
           event_log=[dict(time_s=.1,kind='esst1a_gate_transition',equipment_id='g',gate='oel',active=True)])
    b=copy.deepcopy(a);b['event_log'][0][field]='wrong' if field=='gate' else False
    with pytest.raises(ValueError,match='sequence'):compare(a,b)


@pytest.mark.parametrize('sign',['positive','zero','negative'])
def test_PI_measurement_and_rectifier_use_signed_field_reaction(sign):
    from benchmarks.ex02_cases import modal
    m,x=ready(modal('avr_pss_oel'));i=1;m.gen_connected[i]=False;m.factorize()
    a=m.round_rotors.addresses[i]
    x[a]=[.3,.2,.2,.2] if sign=='positive' else [0.,.2,0.,.2] if sign=='zero' else [-.3,.2,-.2,.2]
    current=m.esst1a.measurements(x)[i][1]
    assert current==0 if sign=='zero' else current>0 if sign=='positive' else current<0
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<3e-5


def test_inverted_moving_rectifier_bounds_never_become_a_Jacobian():
    m,x=ready(example('hold'));_,p=m.esst1a.entries[1];p.K_C=1e6
    with pytest.raises(ValueError,match='rectifier limits inverted'):callback(m)(0.,x)
