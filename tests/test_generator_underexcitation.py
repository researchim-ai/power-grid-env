"""Explicit MNLEX2 equations, raw reset semantics and independently solved feedback."""
from functools import lru_cache
from pathlib import Path
import os
import numpy as np
import pytest
from pydantic import ValidationError
from benchmarks.generator_underexcitation_cases import example,NAMES
from benchmarks.multibus_operating_point import independent_circuit
from benchmarks.generator_thermal_validation import full_compare,physical_contract
from power_grid_env.generator_underexcitation import UnderexcitationLimiter,circle_error,mnlex2_rates
from power_grid_env.study_spec import run_study,StudySpec
from power_grid_env.coupled_rms import CoupledNetwork,validate_coupled
from power_grid_env.coupled_initialization import initialize_coupled


@pytest.fixture(scope='module')
def pair():
    @lru_cache(None)
    def calculate(name):
        s=example(name);r=run_study(s,isolate=True,wall_timeout_s=90.)
        assert r['completed'],r
        return s,r,independent_circuit(s).run(r)
    return calculate


@pytest.mark.parametrize('name',NAMES)
def test_complete_plant_trajectory_matches_independent_winding_reference(name,pair):
    s,r,i=pair(name);assert full_compare(s,r,i)['passed']
    assert physical_contract(s,r)['passed'] and physical_contract(s,i)['passed']
    assert len({t for t,side in zip(r['time_s'],r['sample_side']) if side=='pre_event'})==r['sample_side'].count('pre_event')


@pytest.mark.parametrize('name',['K_F2','T_F2','K_M','T_M','MEL_MAX','Radius'])
@pytest.mark.parametrize('bad',[0.,float('nan')])
def test_degenerate_constants_cannot_silently_select_different_native_blocks(name,bad):
    with pytest.raises(ValidationError):UnderexcitationLimiter(generator_id='g',**{name:bad})


def test_circle_uses_terminal_power_and_voltage_fourth_power():
    p=UnderexcitationLimiter(generator_id='g',Q_0=.3,Radius=.4)
    assert circle_error(p,.4,-.2,.7)==pytest.approx(.4**2+(-.2-.3*.7**2)**2-(.4*.7**2)**2)
    assert circle_error(p,.1,.1,1.)<0
    # The published whole-circle model also responds above its upper Q arc.
    assert circle_error(p,.1,2.,1.)>0


def test_lag_raw_state_is_not_a_projected_derivative():
    p=UnderexcitationLimiter(generator_id='g');dx=mnlex2_rates(p,[-.2,0.],-2.)
    assert dx[0]<0 and dx[1]==0
    dx=mnlex2_rates(p,[.3,.02],2.)
    output=.1;feedback=.1*(output-.02)
    assert dx==pytest.approx([(.3*(2.-feedback)-.3)/.5,(output-.02)])


def test_zero_output_startup_exposes_raw_startup_without_spurious_events(pair):
    s,r,i=pair('zero_output_startup');assert max(r['channels']['g2.UEL.output_pu'])==0
    assert min(r['channels']['g2.UEL.raw_lag_state_pu'])<-.001 and r['event_log']==[]
    raw=np.asarray(r['channels']['g2.UEL.raw_lag_state_pu']);e=r['channels']['g2.UEL.circle_error_pu2'][0];p=s.coupled.underexcitation_limiters[0]
    assert max(abs(raw-p.K_M*e*(1-np.exp(-np.array(r['time_s'])/p.T_M))))<1e-8


def test_lower_resets_are_single_atomic_pairs_and_memory_stays_continuous(pair):
    s,r,i=pair('activation');resets=[e for e in r['event_log'] if e['kind']=='uel_lag_reset'];assert resets
    for e in resets:
        indices=[j for j,t in enumerate(r['time_s']) if t==e['time_s']]
        assert len(indices)==2
        before,after=indices;assert r['sample_side'][before:after+1]==['pre_event','post_event']
        assert r['channels']['g2.UEL.raw_lag_state_pu'][before]==pytest.approx(e['raw_state_before_pu'])
        assert r['channels']['g2.UEL.raw_lag_state_pu'][after]==pytest.approx(e['raw_state_after_pu'])
        assert r['channels']['g2.UEL.feedback_memory_pu'][before]==r['channels']['g2.UEL.feedback_memory_pu'][after]
        for key in ['g2.e1q','g2.e2d','g2.delta','g2.SEXS.field_state_pu']:
            assert r['channels'][key][before]==r['channels'][key][after]


def test_closed_loop_raises_reactive_output_without_editing_rotor_states(pair):
    s,r,i=pair('activation');base=s.model_copy(deep=True);base.coupled.underexcitation_limiters=[];u=run_study(base);assert u['completed']
    assert max(r['channels']['g2.UEL.output_pu'])>.01
    assert r['channels']['g2.q_var'][-1]>u['channels']['g2.q_var'][-1]+10000
    assert r['channels']['g2.SEXS.field_pu'][-1]>u['channels']['g2.SEXS.field_pu'][-1]


def test_limited_authority_does_not_claim_circle_constraint_satisfied(pair):
    s,r,i=pair('limited_authority');assert max(r['channels']['g2.UEL.output_pu'])==.001
    assert r['channels']['g2.UEL.circle_error_pu2'][-1]>0
    assert max(r['channels']['g2.UEL.raw_lag_state_pu'])>.001


@pytest.mark.parametrize('factor',[.1,7.])
def test_network_power_base_does_not_change_limiter_machine_base(factor,pair):
    s,r,i=pair('activation');s=s.model_copy(deep=True);s.network.s_base_va*=factor
    q=run_study(s);assert q['completed'];assert q['channels']['g2.UEL.p_pu']==pytest.approx(r['channels']['g2.UEL.p_pu'],abs=1e-7)
    assert q['channels']['g2.UEL.output_pu']==pytest.approx(r['channels']['g2.UEL.output_pu'],abs=1e-7)


def test_addressing_is_by_generator_not_passport_order(pair):
    s,r,i=pair('two_limiters');s=s.model_copy(deep=True);s.coupled.machines.reverse();s.coupled.underexcitation_limiters.reverse()
    q=run_study(s);assert q['completed']
    for gid in ['g','g2']:assert q['channels'][gid+'.UEL.output_pu']==pytest.approx(r['channels'][gid+'.UEL.output_pu'],abs=1e-7)


def test_open_stator_measures_zero_port_power_but_retains_memory(pair):
    s,r,i=pair('breaker_open');ch=r['channels'];assert ch['g2.UEL.p_pu'][-1]==0 and ch['g2.UEL.q_pu'][-1]==0
    assert ch['g2.UEL.raw_lag_state_pu'][-1]!=ch['g2.UEL.raw_lag_state_pu'][0]
    assert ch['g2.delta'][-1]!=ch['g2.delta'][0]


@pytest.mark.parametrize('fault',['unknown','duplicate','classical','no_exciter','initial_boundary','initial_active'])
def test_unsupported_or_already_limited_initial_profile_is_explicitly_rejected(fault):
    s=example('hold')
    if fault=='unknown':s.coupled.underexcitation_limiters[0].generator_id='missing'
    elif fault=='duplicate':s.coupled.underexcitation_limiters*=2
    elif fault=='classical':s.coupled.machines[1].model='GENCLS'
    elif fault=='no_exciter':s.coupled.machines[1].exciter=None
    else:
        m=CoupledNetwork(s.network,s.coupled);x,_=initialize_coupled(m);assert x is not None
        i,(a,p)=next(iter(m.underexcitation_limiters.entries.items()));active,q,v=m.underexcitation_limiters.measurements(x)[i]
        p.Q_0=q/v**2;p.Radius=abs(active)/v**2*(1. if fault=='initial_boundary' else .5)
        s.coupled.underexcitation_limiters=[p]
    r=run_study(s);assert not r['completed'] and 'channels' not in r


def test_all_native_adapters_reject_before_worker(monkeypatch):
    from benchmarks import andes_group_motor_reference,andes_joint_motor_reference,andes_multibus_motor
    s=example('hold')
    for module,args in [(andes_group_motor_reference,(s,)),(andes_joint_motor_reference,(s,)),(andes_multibus_motor,(s,.0001))]:
        with pytest.raises(ValueError,match='UEL|MNLEX2'):module.scope(*args)


def test_small_signal_is_not_silently_linearized_on_reset_branch():
    from power_grid_env.coupled_small_signal import run_coupled_small_signal
    s=example('hold');result=run_coupled_small_signal(s.network,s.coupled)
    assert not result['completed'] and result['reason']=='underexcitation_limiter_modal_analysis_unsupported'


def test_loaded_initialization_has_zero_raw_lag_derivative(pair):
    s,r,i=pair('hold');raw=r['channels']['g2.UEL.raw_lag_state_pu']
    assert max(raw)-min(raw)<1e-9 and raw[0]<0
    m=CoupledNetwork(s.network,s.coupled);x,info=initialize_coupled(m);assert x is not None
    assert max(abs(m.underexcitation_limiters.derivatives(x)))<1e-10
    s.coupled.underexcitation_limiters[0].initialization='zero_output_startup'
    assert not validate_coupled(s.network,s.coupled)['ok']


def test_OEL_and_UEL_enter_AVR_with_opposite_signs(pair):
    s,r,i=pair('oel_coordination');ch=r['channels']
    assert max(ch['g2.UEL.output_pu'])>.001 and max(ch['g2.OEL.bias_pu'])>.001
    expected=np.array(ch['g2.SEXS.reference_pu'])-ch['g2.terminal_voltage_pu']+np.array(ch['g2.UEL.output_pu'])-ch['g2.OEL.bias_pu']
    assert ch['g2.SEXS.error_pu']==pytest.approx(expected,abs=1e-12)


def test_neutral_roundoff_after_raw_reset_cannot_rearm_an_instantaneous_loop(monkeypatch):
    s=example('hold');m=CoupledNetwork(s.network,s.coupled);x,_=initialize_coupled(m);assert x is not None
    bank=m.underexcitation_limiters;i,(a,p)=next(iter(bank.entries.items()));x[a]=-1e-18
    monkeypatch.setattr(bank,'free_rate',lambda state,j:1e-15)
    log=[];bank.apply({('uel',i,'reset')},.1,x,log)
    assert bank.regions[i]==0 and x[a]==0.
    assert ('uel',i,'reset') not in bank.guards(x)[0]
