"""Field physics, bounded OEL feedback, recovery and independent winding checks."""
import copy
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError
from scipy.integrate import solve_ivp

from power_grid_env.generator_excitation import OverexcitationLimiter,ExcitationLimiterBank
from power_grid_env.coupled_rms import CoupledNetwork,validate_coupled
from power_grid_env.coupled_initialization import initialize_coupled
from power_grid_env.study_spec import run_study,StudySpec
from benchmarks.generator_excitation_cases import example,NAMES
from benchmarks.multibus_operating_point import independent_circuit
from benchmarks.generator_thermal_validation import full_compare,physical_contract


@pytest.fixture(scope='module')
def pair():
    @lru_cache(None)
    def calculate(name):
        s=example(name);r=run_study(s,isolate=True,wall_timeout_s=90.)
        assert r['completed'],r
        return s,r,independent_circuit(s).run(r)
    return calculate


@pytest.mark.parametrize('name',NAMES)
def test_complete_closed_loop_matches_separate_SI_reference(name,pair):
    s,r,i=pair(name)
    assert full_compare(s,r,i)['passed']
    assert physical_contract(s,r)['passed'] and physical_contract(s,i)['passed']
    assert r['additional_validation']['overexcitation_limits']['g2']['within_limits']


def test_current_is_rotor_reaction_not_exciter_voltage(pair):
    s,r,i=pair('voltage_step')
    reaction=np.array(r['channels']['g2.OEL.field_reaction_pu'])
    voltage=np.array(r['channels']['g2.SEXS.field_pu'])
    assert abs(reaction[0]-voltage[0])<1e-10
    assert max(abs(reaction-voltage))>.1
    assert max(r['channels']['g2.OEL.bias_pu'])>.01


def test_reaction_identity_in_original_rotor_ode():
    s=example('saturated');m=CoupledNetwork(s.network,s.coupled);x,init=initialize_coupled(m)
    assert x is not None,init
    x[m.round_rotors.addresses[1].start]+=.1
    v=m.electrical(x)[0];current=m.generator_currents(x,v)
    reaction=m.excitation_limiters.reaction(x,current);dx=m.rhs(0,x)
    field=m.controllers.field(x,m.gen_voltage)
    for i,at in m.round_rotors.addresses.items():
        assert field[i]-s.coupled.machines[i].td_prime_s*dx[at.start]==pytest.approx(reaction[i],abs=1e-12)


def test_feedback_reduces_current_without_hard_clipping(pair):
    s,r,i=pair('voltage_step');base=s.model_copy(deep=True);base.coupled.overexcitation_limiters=[]
    unlimited=run_study(base)
    assert unlimited['completed']
    from benchmarks.generator_excitation_validation import reaction_from_trace
    baseline_current=reaction_from_trace(base,unlimited,'g2')
    field=np.array(unlimited['channels']['g2.SEXS.field_pu'])
    assert max(r['channels']['g2.OEL.field_current_pu'])<max(baseline_current)-.2
    assert max(r['channels']['g2.OEL.field_current_pu'])>s.coupled.overexcitation_limiters[0].current_limit_pu
    assert max(r['channels']['g2.SEXS.field_pu'])<max(field)


def test_recovery_removes_feedback_bias(pair):
    s,r,i=pair('recovery');bias=r['channels']['g2.OEL.bias_pu']
    assert max(bias)>.001 and bias[-1]<max(bias)*.01


def test_limited_authority_does_not_claim_current_constraint(pair):
    s,r,i=pair('limited_authority');p=s.coupled.overexcitation_limiters[0]
    assert max(r['channels']['g2.OEL.bias_pu'])<=p.maximum_voltage_bias_pu
    assert r['channels']['g2.OEL.bias_pu'][-1]>.99*p.maximum_voltage_bias_pu
    assert r['channels']['g2.OEL.field_current_pu'][-1]>p.current_limit_pu


def test_open_generator_still_has_field_current_and_no_stator_current(pair):
    s,r,i=pair('breaker_open')
    assert r['channels']['g2.current_pu'][-1]==0
    assert r['channels']['g2.OEL.field_current_pu'][-1]>.1
    assert r['channels']['g2.delta'][-1]!=r['channels']['g2.delta'][0]


def test_hold_is_inactive_and_passport_order_does_not_select_another_machine(pair):
    s,r,i=pair('hold');assert max(r['channels']['g2.OEL.bias_pu'])==0
    other=s.model_copy(deep=True);other.coupled.machines.reverse()
    result=run_study(other)
    assert result['completed'] and result['channels']==r['channels']


@pytest.mark.parametrize('field',['rated_field_reaction_pu','current_limit_pu','measurement_time_s','feedback_gain_pu','bias_time_s','maximum_voltage_bias_pu'])
@pytest.mark.parametrize('value',[0.,-1.,float('nan'),float('inf')])
def test_invalid_physical_passport_rejected(field,value):
    p=example('hold').coupled.overexcitation_limiters[0].model_dump();p[field]=value
    with pytest.raises(ValidationError):OverexcitationLimiter.model_validate(p)


@pytest.mark.parametrize('defect',['unknown','duplicate','classical','missing_exciter'])
def test_unsupported_mapping_rejected_before_execution(defect):
    s=example('hold')
    if defect=='unknown':s.coupled.overexcitation_limiters[0].generator_id='absent'
    elif defect=='duplicate':s.coupled.overexcitation_limiters*=2
    elif defect=='missing_exciter':s.coupled.machines[1].exciter=None
    else:
        from power_grid_env.rms_study import ClassicalMachine
        s.coupled.machines[1]=ClassicalMachine(generator_id='g2',rated_va=1e6,inertia_s=1.,x_d_prime_pu=.2)
    assert not validate_coupled(s.network,s.coupled)['ok']


def test_initial_overcurrent_and_modal_inference_rejected():
    s=example('hold');s.coupled.overexcitation_limiters[0].current_limit_pu=.1
    r=run_study(s);assert not r['completed'] and r['reason']=='initial_overexcitation_limit_or_boundary'
    s=example('hold');s.study='coupled_small_signal'
    r=run_study(s);assert not r['completed'] and r['reason']=='overexcitation_limiter_modal_analysis_unsupported'


def test_native_scope_rejects_before_starting_a_worker(monkeypatch):
    import benchmarks.andes_multibus_motor as native
    monkeypatch.setattr(native.subprocess,'Popen',lambda *a,**k:pytest.fail('must reject before worker'))
    with pytest.raises(ValueError,match='OEL'):native.native_reference(example('hold'),.00025)


def test_two_lag_component_matches_closed_form_and_is_bounded():
    p=OverexcitationLimiter(generator_id='g',rated_field_reaction_pu=2.,current_limit_pu=1.,measurement_time_s=.2,feedback_gain_pu=.3,bias_time_s=.4,maximum_voltage_bias_pu=2.)
    model=SimpleNamespace(generators=[SimpleNamespace(id='g')],settings=SimpleNamespace(overexcitation_limiters=[p]))
    bank=ExcitationLimiterBank(model,0);bank.reaction=lambda x,current:np.array([4.])
    times=np.linspace(0,2,201)
    result=solve_ivp(lambda t,x:bank.derivatives(x,None),(0,2),[1.,0.],t_eval=times,rtol=1e-11,atol=1e-13)
    expected=.3*(1-(.4*np.exp(-times/.4)-.2*np.exp(-times/.2))/(.4-.2))
    assert result.y[1]==pytest.approx(expected,abs=1e-11)
    assert min(result.y[1])>=0 and max(result.y[1])<2.


def test_bias_saturation_and_recovery_have_no_hidden_windup():
    p=example('limited_authority').coupled.overexcitation_limiters[0]
    model=SimpleNamespace(generators=[SimpleNamespace(id='g2')],settings=SimpleNamespace(overexcitation_limiters=[p]))
    bank=ExcitationLimiterBank(model,0);bank.reaction=lambda x,current:np.array([10.])
    first=solve_ivp(lambda t,x:bank.derivatives(x,None),(0,3),[10.,0.],rtol=1e-10,atol=1e-13)
    assert first.y[1,-1]==pytest.approx(p.maximum_voltage_bias_pu,abs=1e-10)
    bank.reaction=lambda x,current:np.array([0.])
    second=solve_ivp(lambda t,x:bank.derivatives(x,None),(0,3),[0.,first.y[1,-1]],rtol=1e-10,atol=1e-13)
    assert second.y[1,-1]<1e-10


def test_raw_bias_overshoot_is_not_hidden_by_output_projection():
    p=example('hold').coupled.overexcitation_limiters[0]
    model=SimpleNamespace(generators=[SimpleNamespace(id='g2')],settings=SimpleNamespace(overexcitation_limiters=[p]))
    bank=ExcitationLimiterBank(model,0)
    channels={'g2.OEL.bias_state_pu':[0.,p.maximum_voltage_bias_pu+.1],'g2.OEL.bias_pu':[0.,p.maximum_voltage_bias_pu]}
    assert not bank.validate(channels,1e-8,1e-10)['ok']


def test_explicit_field_base_change_preserves_feedback_physics(pair):
    s,r,i=pair('voltage_step');other=s.model_copy(deep=True)
    p=other.coupled.overexcitation_limiters[0]
    p.rated_field_reaction_pu*=2;p.current_limit_pu/=2;p.feedback_gain_pu*=2
    b=run_study(other)
    assert b['completed']
    assert b['channels']['g2.OEL.field_current_pu']==pytest.approx(np.array(r['channels']['g2.OEL.field_current_pu'])/2,abs=1e-7)
    for key in ['g2.OEL.bias_pu','g2.delta','g2.SEXS.field_pu','g2.current_pu']:
        assert b['channels'][key]==pytest.approx(r['channels'][key],abs=1e-7)


@pytest.mark.parametrize('defect',['clock','reaction','bias'])
def test_reaudit_rejects_corrupted_evidence(defect,pair):
    from benchmarks.generator_excitation_validation import compare_protected
    s,r,i=pair('voltage_step');bad=copy.deepcopy(r)
    if defect=='clock':bad['time_s'][-1]+=.01
    elif defect=='reaction':bad['channels']['g2.OEL.field_reaction_pu'][-1]+=.1
    else:bad['channels']['g2.OEL.bias_pu'][-1]=1.
    try:result=compare_protected(s,bad,i)
    except ValueError:return
    assert not result['passed']


def test_protocol_precedes_first_solver(tmp_path,monkeypatch):
    import json
    import benchmarks.generator_excitation_validation as v
    def stop(*a,**kw):
        p=json.loads((tmp_path/'protocol.json').read_text())
        assert p['native_OEL_feedback_runs']==0 and p['limits']['native_field_reaction_pu']==5e-4
        raise RuntimeError('preflight reached')
    monkeypatch.setattr(v,'run_study',stop)
    with pytest.raises(RuntimeError,match='preflight'):v.run(tmp_path)
    assert not json.loads((tmp_path/'summary.json').read_text())['passed']


def test_long_posttrip_governor_bound_is_located_and_held(pair):
    s,r,i=pair('thermal_trip')
    assert r['completed']
    assert r['additional_validation']['controller_limits']['g2.TGOV1.valve_pu']['within_limits']
    entries=[e for e in r['event_log'] if e['kind']=='controller_limit_enter']
    assert any(e['equipment_id']=='g2' and e['controller']=='TGOV1' and e['bound']=='lower' for e in entries)


def test_reference_return_releases_floor_in_the_same_atomic_boundary(pair):
    s,r,i=pair('exciter_floor')
    assert [side for t,side in zip(r['time_s'],r['sample_side']) if t==.8]==['pre_event','post_event']
    released=[e for e in r['event_log'] if e['kind']=='controller_limit_release']
    assert released[0]['time_s']==.8
    assert r['channels']['g2.SEXS.field_pu'][-1]>.2


def test_controller_upper_limit_releases_at_continuous_root(pair):
    s,r,i=pair('exciter_recovery')
    entered=next(e for e in r['event_log'] if e['kind']=='controller_limit_enter')
    released=next(e for e in r['event_log'] if e['kind']=='controller_limit_release')
    assert entered['time_s']<.8<released['time_s']
    assert max(r['channels']['g2.SEXS.field_state_pu'])<=1.5+1e-10


@pytest.mark.parametrize('distance,expected',[(0.,2),(1e-10,1)])
def test_only_roundoff_tied_controller_entries_are_atomic(distance,expected):
    from power_grid_env.controller_boundaries import ControllerBoundaries
    b=ControllerBoundaries.__new__(ControllerBoundaries)
    b.entries={0:(0,'TGOV1',0.,1.),1:(1,'TGOV1',0.,1.)};b.active={}
    b.model=SimpleNamespace(generators=[SimpleNamespace(id='g'),SimpleNamespace(id='g2')])
    b.rates=lambda state:{0:-1.,1:-1.}
    log=[];x=np.array([0.,distance]);b.apply({('controller',0,'lower')},1.,x,log)
    assert len(log)==expected and len(b.active)==expected


def test_neutral_bound_has_no_instantaneous_release_loop():
    from power_grid_env.controller_boundaries import ControllerBoundaries
    b=ControllerBoundaries.__new__(ControllerBoundaries)
    b.entries={0:(0,'TGOV1',0.,1.)};b.active={0:'lower'};b.rates=lambda x:{0:0.}
    keys,guards=b.guards(np.zeros(1))
    assert guards[0](0,np.zeros(1))<0 and guards[0](1,np.zeros(1))<0


def test_mixed_classical_and_round_rotor_only_measures_declared_machine():
    from power_grid_env.rms_study import ClassicalMachine
    s=example('hold');old=s.coupled.machines[0]
    s.coupled.machines[0]=ClassicalMachine(generator_id=old.generator_id,rated_va=old.rated_va,inertia_s=old.inertia_s,damping_pu=old.damping_pu,x_d_prime_pu=old.x_d_prime_pu,governor=old.governor,exciter=old.exciter)
    s.coupled.duration_s=.1
    r=run_study(s)
    assert r['completed'] and np.isfinite(r['channels']['g2.OEL.field_current_pu']).all()
    assert not any(k.startswith('g.OEL.') for k in r['channels'])


def test_four_generators_map_last_target_by_id_and_preserve_hold():
    from benchmarks.multigen_cases import example as multi
    s=multi('hold4');p=example('hold').coupled.overexcitation_limiters[0]
    target=s.network.generators[-1].id;p.generator_id=target;p.current_limit_pu=10.
    s.coupled.overexcitation_limiters=[p];s.coupled.duration_s=.1
    s.coupled.machines.reverse()
    r=run_study(s)
    assert r['completed'] and max(r['channels'][target+'.OEL.bias_pu'])==0.
    assert sum(k.endswith('.OEL.bias_pu') for k in r['channels'])==1
