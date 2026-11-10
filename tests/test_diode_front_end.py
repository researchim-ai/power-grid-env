"""Circuit identities, limiting cases and coupled-network regression."""
import copy
import itertools
from types import SimpleNamespace
import numpy as np
import pytest
from power_grid_env.diode_front_end import bridge_port
from power_grid_env.phase_network import PhaseCase,PhaseNetwork,run_emt,_abc,_space
from power_grid_env.vf_drive import VfDrive
from benchmarks.diode_drive_cases import document

P=SimpleNamespace(series_resistance_ohm=2.,precharge_resistance_ohm=60.,diode_drop_v=.9,diode_resistance_ohm=.4)


@pytest.mark.parametrize('bypassed',[False,True])
def test_random_bridge_passivity_three_wire_kcl_and_energy(bypassed):
    rng=np.random.default_rng(1042)
    for _ in range(150):
        voltage=complex(*rng.normal(0,350,2));vdc=float(rng.uniform(0,1000));j,q=bridge_port(voltage,vdc,P,connected=True,bypassed=bypassed)
        assert abs(sum(_abc(j)))<1e-12
        assert -1.5*(voltage*np.conj(j)).real==pytest.approx(q['grid_power_w'],abs=1e-9)
        assert q['grid_power_w']==pytest.approx(q['rectifier_dc_power_w']+q['rectifier_loss_w'],abs=1e-8)
        assert min(q['grid_power_w'],q['rectifier_dc_power_w'],q['rectifier_loss_w'],q['rectifier_current_a'])>=0


def test_tied_phase_commutation_is_continuous_and_permutation_invariant():
    abc=np.array([300.,300.,-600.]);expected=None
    for perm in itertools.permutations(range(3)):
        v=abc[list(perm)];j,q=bridge_port(_space(v),100.,P,connected=True,bypassed=True)
        current=_abc(j)[np.argsort(perm)]
        if expected is None:expected=current
        np.testing.assert_allclose(current,expected,atol=1e-10)
    left=bridge_port(_space(abc+[1e-7,-1e-7,0]),100.,P,connected=True,bypassed=True)[0]
    right=bridge_port(_space(abc+[-1e-7,1e-7,0]),100.,P,connected=True,bypassed=True)[0]
    assert abs(left-right)<1e-5


def test_blocking_and_open_ac_contactor_do_not_allow_regeneration():
    for voltage,vdc,connected in [(300+100j,1000.,True),(300+100j,0.,False),(0j,0.,True)]:
        j,q=bridge_port(voltage,vdc,P,connected=connected,bypassed=True)
        assert j==0 and q['rectifier_current_a']==q['grid_power_w']==q['rectifier_loss_w']==0


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1.])
def test_nonphysical_dc_voltage_rejected(value):
    with pytest.raises(ValueError):bridge_port(300j,value,P,connected=True,bypassed=False)


def test_zero_voltage_precharge_has_positive_voltage_derivative_without_seed_energy():
    d=document('precharge');m=VfDrive(PhaseCase.model_validate(d['case']).drives[0]);x=m.initial();dx,q=m.rates(0.,300.+0j,x,True,False)
    assert x[4]==q['dc_energy_j']==0 and dx[4]>0
    assert q['grid_power_w']==pytest.approx(q['rectifier_loss_w'])
    assert q['rectifier_dc_power_w']==0


@pytest.mark.parametrize('bypassed',[False,True])
def test_coupled_drive_energy_derivative_in_voltage_coordinates(bypassed):
    d=document('precharge');m=VfDrive(PhaseCase.model_validate(d['case']).drives[0]);x=m.initial();x[4]=400.;x[m.motor_at:m.speed_at]=[.3,.2,.25,.1];x[m.speed_at]=30.
    dx,q=m.rates(.08,300.+20j,x,True,bypassed);h=1e-7
    energy=lambda z:m.rates(.08,300.+20j,z,True,bypassed)[1]['stored_j']
    rate=(energy(x+h*dx)-energy(x-h*dx))/(2*h)
    assert rate==pytest.approx(q['grid_power_w']-q['loss_w']-q['mechanical_power_w'],abs=2e-4,rel=1e-7)
    assert m.p.dc_capacitance_f*x[4]*dx[4]==pytest.approx(q['rectifier_dc_power_w']-q['inverter_dc_power_w']-q['bleed_loss_w'])


def test_nonlinear_algebraic_terminal_rejected_instead_of_frozen_voltage():
    c=document('precharge')['case'];c['capacitors']=[]
    with pytest.raises(ValueError,match='nonlinear algebraic bridge'):PhaseNetwork(PhaseCase.model_validate(c))


@pytest.mark.parametrize('field,value',[('diode_resistance_ohm',0.),('series_resistance_ohm',0.),('precharge_resistance_ohm',0.),('provenance',' ')])
def test_invalid_bridge_parameter_inventory_rejected(field,value):
    c=document('precharge')['case'];c['drives'][0]['diode_front_end'][field]=value
    with pytest.raises(ValueError):PhaseCase.model_validate(c)


def test_precharge_action_cannot_target_active_front_end():
    from benchmarks.vf_core_cases import document as active
    c=active('core_runup')['case'];c['events']=[dict(time_s=.01,kind='drive_precharge_bypass',equipment_id='drive',value=1.)]
    with pytest.raises(ValueError,match='precharge bypass'):PhaseCase.model_validate(c)


def test_bypass_event_keeps_dc_voltage_and_flux_and_changes_only_port_law():
    d=document('early_bypass');d['emt']['duration_s']=.02;r=run_emt(PhaseCase.model_validate(d['case']),**d['emt']);i=r['sample_side'].index('pre_event');j=i+1
    assert r['time_s'][i]==r['time_s'][j]==.015
    for key in ['dc_voltage_v','dc_energy_j','motor_stator_flux_alpha_vs','motor_stator_flux_beta_vs']:
        assert r['channels']['drive.'+key][i]==r['channels']['drive.'+key][j]
    assert r['channels']['drive.precharge_bypassed'][i]==0 and r['channels']['drive.precharge_bypassed'][j]==1
    assert r['channels']['drive.rectifier_current_a'][j]>r['channels']['drive.rectifier_current_a'][i]
    assert r['kcl_max_a']<1e-7


def test_separate_diode_reference_does_not_use_production_port(monkeypatch):
    from benchmarks.vf_core_reference import run_reference
    import power_grid_env.diode_front_end as module
    def forbidden(*a,**kw):raise AssertionError('production circuit called')
    monkeypatch.setattr(module,'bridge_port',forbidden);monkeypatch.setattr(VfDrive,'rates',forbidden)
    d=document('precharge');r=run_reference(d['case'],[0.,.001,.002],['initial','regular','regular'])
    assert not r['primary_rhs_used'] and r['records'][-1]['channels']['drive.dc_voltage_v']>0


def test_constant_source_precharge_matches_analytic_rc_solution():
    from scipy.integrate import solve_ivp
    capacitance=.002;bleed=2000.;resistance=P.series_resistance_ohm+P.precharge_resistance_ohm+1.5*P.diode_resistance_ohm
    equilibrium=(450.-2*P.diode_drop_v)/(1+resistance/bleed)
    tau=capacitance/(1/resistance+1/bleed)
    def rhs(t,x):
        _,q=bridge_port(300.+0j,float(x[0]),P,connected=True,bypassed=False)
        return [(q['rectifier_current_a']-x[0]/bleed)/capacitance]
    sol=solve_ivp(rhs,(0.,.1),[0.],rtol=1e-11,atol=1e-12,dense_output=True)
    times=np.linspace(0.,.1,101)
    np.testing.assert_allclose(sol.sol(times)[0],equilibrium*(1-np.exp(-times/tau)),rtol=1e-10,atol=1e-8)


def test_three_wire_diode_bridge_ignores_common_mode_and_allows_resistive_overlap():
    phases=np.array([300.,299.,-599.]);base=_space(phases)
    a,qa=bridge_port(base,100.,P,connected=True,bypassed=True)
    b,qb=bridge_port(_space(phases+1000.),100.,P,connected=True,bypassed=True)
    assert a==pytest.approx(b) and qa['grid_power_w']==pytest.approx(qb['grid_power_w'])
    injection=_abc(a)
    assert injection[0]<0 and injection[1]<0 and injection[2]>0


def test_direct_drive_api_uses_declared_bypass_initial_state():
    d=document('blocked_regeneration');m=VfDrive(PhaseCase.model_validate(d['case']).drives[0]);x=m.initial();x[4]=200.
    _,q=m.rates(.01,300.+0j,x,True)
    assert q['precharge_bypassed'] and q['precharge_loss_w']==0
