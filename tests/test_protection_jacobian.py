"""Full-coordinate independent finite differences, including post-trip modes."""
import numpy as np
import pytest
from tests.test_esst1a_jacobian import ready,difference
from power_grid_env.protection_jacobian import callback,KIND
from benchmarks.generator_underexcitation_cases import example,NAMES


@pytest.mark.parametrize('name',NAMES)
@pytest.mark.parametrize('region',['lower','interior','upper'])
def test_MNLEX2_circle_and_lag_all_coordinates(name,region):
    m,x=ready(example(name))
    for i,(a,p) in m.underexcitation_limiters.entries.items():
        x[a]=-.03 if region=='lower' else p.MEL_MAX*.4 if region=='interior' else p.MEL_MAX+.03
    for _,(a,p) in m.excitation_limiters.entries.items():x[a+1]=p.maximum_voltage_bias_pu*.3
    assert m.integration_jacobian().jacobian_kind==KIND
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<3e-5


@pytest.mark.parametrize('name',['field_trip','uel_summation'])
@pytest.mark.parametrize('outage',['none','generator','fault','trip'])
def test_ESST_with_UEL_or_rotor_thermal_in_each_discrete_mode(name,outage):
    from benchmarks.generator_esst1a_cases import example as esst
    m,x=ready(esst(name));i=1
    if outage=='generator':m.gen_connected[i]=False
    if outage=='fault':m.fault_y[m.gen_buses[i]]=.1-.2j
    if outage=='trip':
        m.gen_connected[i]=False;m.field_thermal.field_enabled[i]=False
    m.factorize()
    for _,(a,p) in m.underexcitation_limiters.entries.items():x[a]=p.MEL_MAX*.3
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<3e-5


def test_callback_has_no_hidden_RHS_and_does_not_mutate(monkeypatch):
    m,x=ready(example('field_trip'));before=x.copy()
    monkeypatch.setattr(m,'rhs',lambda *a:pytest.fail('hidden RHS'))
    assert np.isfinite(callback(m)(0.,x)).all();assert np.array_equal(x,before)


def test_OEL2C_remains_explicit_fallback():
    from power_grid_env.coupled_rms import CoupledNetwork
    from power_grid_env.generator_oel2c import OEL2CPassport
    s=example('hold');s.coupled.oel2c_limiters=[OEL2CPassport(generator_id='g2',rated_field_reaction_pu=1.)]
    m=CoupledNetwork(s.network,s.coupled);assert callback(m) is None;assert m.integration_jacobian() is None


@pytest.mark.parametrize('name',['hold','overload_recovery','field_trip','generator_open','pss_limited','pss_voltage_gate'])
@pytest.mark.parametrize('mode',['connected','open_zero_field'])
def test_joint_ESST_PI_PSS_UEL_full_coordinate_coupling(name,mode):
    from benchmarks.ex02_cases import example as ex02
    m,x=ready(ex02(name));i=1
    for _,(a,p) in m.underexcitation_limiters.entries.items():x[a]=p.MEL_MAX*.4
    for _,(a,p) in m.takeover_oel.entries.items():
        x[a+1]=(p.minimum_field_pu+p.maximum_field_pu)/2-p.proportional_gain*(p.current_limit_pu-x[a])
    if mode=='open_zero_field':
        m.gen_connected[i]=False;m.field_thermal.field_enabled[i]=False;m.factorize()
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<4e-5


@pytest.mark.parametrize('bound',['lower','upper'])
def test_MNLEX2_exact_clip_corner_uses_outward_slope(bound):
    m,x=ready(example('hold'));a,p=next(iter(m.underexcitation_limiters.entries.values()))
    x[a]=0. if bound=='lower' else p.MEL_MAX
    h=-1e-7 if bound=='lower' else 1e-7;y=x.copy();y[a]+=h
    assert np.max(abs(callback(m)(0.,x)[:,a]-(m.rhs(0.,y)-m.rhs(0.,x))/h))<3e-5


@pytest.mark.parametrize('damage',['shape','nan','complex'])
def test_invalid_state_is_rejected(damage):
    m,x=ready(example('hold'))
    if damage=='shape':x=x[:-1]
    if damage=='nan':x[0]=np.nan
    if damage=='complex':x=x.astype(complex)+1j
    with pytest.raises(ValueError,match='invalid'):callback(m)(0.,x)


def test_field_trip_applies_real_latch_and_preserves_continuous_memory():
    m,x=ready(example('field_trip'));i=1;a,p=m.field_thermal.entries[i];x[a]=p.trip_level
    _,current,_,_=m.electrical(x);v=m.electrical(x)[0];ig=m.generator_currents(x,v)
    before=x.copy();events=[];reaction=m.field_thermal.reaction(x,ig)
    m.field_thermal.apply({('field',i)},.2,x,events,reaction);m.factorize()
    assert events[0]['kind']=='generator_field_thermal_trip'
    assert not m.gen_connected[i] and not m.field_thermal.field_enabled[i]
    assert np.array_equal(x,before)
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<3e-5


@pytest.mark.parametrize('reaction_target',[-.5,0.,.7])
def test_rotor_I2_has_signed_and_zero_current_gradient(reaction_target):
    m,x=ready(example('field_trip'));i=1;a,p=m.field_thermal.entries[i]
    # Field reaction is affine in Eq' for an isolated, unsaturated machine.
    m.gen_connected[i]=False;m.factorize();machine=m.gen_passports[i];machine.saturation='disabled'
    address=m.round_rotors.addresses[i];x[address]=0.;x[address.start+1]=.2
    v=m.electrical(x)[0];ig=m.generator_currents(x,v);base=m.field_thermal.reaction(x,ig)[i]
    y=x.copy();y[address.start]+=1.;v=m.electrical(y)[0];gain=m.field_thermal.reaction(y,m.generator_currents(y,v))[i]-base
    x[address.start]=(reaction_target-base)/gain
    assert np.max(abs(callback(m)(0.,x)[a]-difference(m,x)[a]))<3e-5


def test_raw_MNLEX2_region_memory_does_not_freeze_free_lag():
    m,x=ready(example('hold'));i,(a,p)=next(iter(m.underexcitation_limiters.entries.items()));x[a]=-.2
    j=callback(m)(0.,x);m.underexcitation_limiters.regions[i]=1
    assert np.array_equal(j,callback(m)(0.,x));assert np.linalg.norm(j[a])>0.


@pytest.mark.parametrize('outage',['none','generator','motor','fault'])
def test_four_machine_network_with_two_rotor_thermal_states(outage):
    from benchmarks.esst1a_jacobian_validation import case
    from power_grid_env.generator_field_thermal import GeneratorFieldThermalProtection
    from power_grid_env.generator_underexcitation import UnderexcitationLimiter
    s=case('scale_512_32_4');s.coupled.motors=s.coupled.motors[:3]
    s.coupled.generator_field_thermal=[GeneratorFieldThermalProtection(generator_id=g.generator_id,rated_field_reaction_pu=1.,time_constant_s=3.,trip_level=100.) for g in s.coupled.machines[:2]]
    s.coupled.underexcitation_limiters=[UnderexcitationLimiter(generator_id=g.generator_id,Q_0=2.5,Radius=4.) for g in s.coupled.machines]
    m,x=ready(s)
    for i,(a,p) in m.underexcitation_limiters.entries.items():x[a]=p.MEL_MAX*.3
    for i in range(3):x[i*3:i*3+3]=[.2,.02,.8]
    if outage=='generator':m.gen_connected[0]=False;m.field_thermal.field_enabled[0]=False
    if outage=='motor':m.connected[0]=False
    if outage=='fault':m.fault_y[m.gen_buses[0]]=.1-.2j
    m.factorize()
    assert np.max(abs(callback(m)(0.,x)-difference(m,x)))<4e-5
