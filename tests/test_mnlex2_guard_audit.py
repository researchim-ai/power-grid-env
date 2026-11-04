"""Reject malformed reset certificates independently of trajectory agreement."""
import copy,math
import pytest
from benchmarks.generator_esst1a_refinement import case
from benchmarks.mnlex2_guard_audit import audit


def snap(p,raw,target,inputs=(.1,.2,1.)):
    residual=target-raw
    return dict(raw_state_pu=raw,native_lag_input_pu=target/p.K_M,target_pu=target,source_guard_numerator_pu=residual,
        native_ode_rate_pu_per_s=residual/p.T_M,strict_lower_condition=raw<0 and residual>0,
        strict_upper_condition=raw>p.MEL_MAX and residual<0,source_guard_reconstructed_from_native_operands=True,native_inputs=list(inputs))


@pytest.fixture
def evidence():
    s=case('two_active_limiters_stress');p=next(p for p in s.coupled.underexcitation_limiters if p.generator_id=='g')
    before=snap(p,-.02,-.01);after=snap(p,0.,-.01)
    e=dict(kind='native_mnlex2_lag_reset',equipment_id='g',time_s=.5,bound='lower',raw_state_before_pu=-.02,raw_state_after_pu=0.,
        guard_before_commands=copy.deepcopy(before),guard_before_update=before,guard_after_update=after,event_iteration_reasons=['own_state_event'],trigger=dict(native_root=True))
    return s,dict(completed=True,native_UEL_event_policy='causal',native_MNLEX2_in_feedback=True,event_log=[e])


def test_valid_native_condition_and_exact_sides_are_audited(evidence):
    s,b=evidence;before=copy.deepcopy(b);r=audit(s,b)
    assert r['passed'] and r['resets']==1 and b==before
    assert r['event_reasons']=={'own_state_event':1}
    assert not r['event_times_certified_by_this_audit'] and not r['industrial_profile_qualified']


@pytest.mark.parametrize('bad',['policy','feedback_claim','incomplete','missing_snapshot','missing_operand','target','residual','condition','condition_type','rate','raw_before','raw_after','side','reason','duplicate_reason','empty_reason','trigger','input_reason','after_input','after_feedback','unknown_generator','late','negative_time','nan','infinite','boolean_number','huge_integer','voltage','missing_inputs','untyped_event','primary_event'])
def test_any_corrupt_guard_evidence_fails_closed(evidence,bad):
    s,b=evidence;e=b['event_log'][0];g=e['guard_before_update']
    if bad=='policy':b['native_UEL_event_policy']='broadcast'
    elif bad=='feedback_claim':b['native_MNLEX2_in_feedback']=False
    elif bad=='incomplete':b['completed']=False
    elif bad=='missing_snapshot':del e['guard_before_update']
    elif bad=='missing_operand':del g['target_pu']
    elif bad=='target':g['target_pu']+=.001
    elif bad=='residual':g['source_guard_numerator_pu']+=.001
    elif bad=='condition':g['strict_lower_condition']=False
    elif bad=='condition_type':g['strict_lower_condition']=1
    elif bad=='rate':g['native_ode_rate_pu_per_s']+=.001
    elif bad=='raw_before':e['raw_state_before_pu']+=.001
    elif bad=='raw_after':e['raw_state_after_pu']=.001
    elif bad=='side':e['bound']='upper'
    elif bad=='reason':e['event_iteration_reasons']=['legacy_broadcast']
    elif bad=='duplicate_reason':e['event_iteration_reasons']*=2
    elif bad=='empty_reason':e['event_iteration_reasons']=[]
    elif bad=='trigger':e['trigger']['native_root']=False
    elif bad=='input_reason':e['event_iteration_reasons']=['input_discontinuity']
    elif bad=='after_input':e['guard_after_update']['native_inputs'][0]+=.01
    elif bad=='after_feedback':
        p=next(p for p in s.coupled.underexcitation_limiters if p.generator_id=='g');e['guard_after_update']=snap(p,0.,-.02)
    elif bad=='unknown_generator':e['equipment_id']='ghost'
    elif bad=='late':e['time_s']=s.coupled.duration_s+.1
    elif bad=='negative_time':e['time_s']=-.1
    elif bad=='nan':g['native_lag_input_pu']=float('nan')
    elif bad=='infinite':g['raw_state_pu']=float('inf')
    elif bad=='boolean_number':g['native_ode_rate_pu_per_s']=True
    elif bad=='huge_integer':g['native_ode_rate_pu_per_s']=10**1000
    elif bad=='voltage':g['native_inputs'][2]=-1.
    elif bad=='missing_inputs':g['native_inputs']=[]
    elif bad=='untyped_event':b['event_log']=[{}]
    elif bad=='primary_event':e['kind']='uel_lag_reset'
    with pytest.raises(ValueError):audit(s,b)


def test_upper_bound_and_input_discontinuity_are_supported(evidence):
    s,b=evidence;p=next(p for p in s.coupled.underexcitation_limiters if p.generator_id=='g');e=b['event_log'][0]
    e.update(bound='upper',raw_state_before_pu=.08,raw_state_after_pu=p.MEL_MAX,
        guard_before_commands=snap(p,.08,.07),guard_before_update=snap(p,.08,.07,inputs=(.1,.21,1.)),
        guard_after_update=snap(p,p.MEL_MAX,.07,inputs=(.1,.21,1.)),event_iteration_reasons=['input_discontinuity'],trigger={})
    assert audit(s,b)['passed']


def test_local_ulp_proximity_never_becomes_a_timestamp_certificate(evidence):
    s,b=evidence;p=next(p for p in s.coupled.underexcitation_limiters if p.generator_id=='g');e=b['event_log'][0]
    raw=-.48;target=math.nextafter(raw,math.inf)
    e.update(raw_state_before_pu=raw,guard_before_commands=snap(p,raw,target),guard_before_update=snap(p,raw,target),guard_after_update=snap(p,0.,target))
    r=audit(s,b)
    assert r['passed'] and r['low_local_separation_resets']==1
    assert r['rows'][0]['local_operand_separation_ulps']==.5
    assert r['conditioning_is_diagnostic_only'] and not r['event_times_certified_by_this_audit']


def test_rounding_of_expanded_ODE_is_allowed_but_not_arbitrary_errors(evidence):
    s,b=evidence;e=b['event_log'][0]
    g=e['guard_before_update'];g['native_ode_rate_pu_per_s']=math.nextafter(g['native_ode_rate_pu_per_s'],math.inf)
    assert audit(s,b)['passed']


def test_unordered_reset_clock_is_rejected(evidence):
    s,b=evidence;e=copy.deepcopy(b['event_log'][0]);e['time_s']=.4;b['event_log'].append(e)
    with pytest.raises(ValueError,match='ordered'):audit(s,b)


def test_guard_failure_is_enforced_before_trajectory_comparison(tmp_path,monkeypatch,evidence):
    from benchmarks import generator_esst1a_causal_validation as m,mnlex2_model_exchange
    s,b=evidence;b['native_UEL_root_policy']='continuous_atoms';del b['event_log'][0]['guard_before_update']
    monkeypatch.setattr(mnlex2_model_exchange,'verify',lambda root:{})
    monkeypatch.setattr(m,'run_study',lambda s:dict(completed=True))
    def native(s,out,*args,**kwargs):out.mkdir();return b
    def forbidden(*args):raise AssertionError('trajectory comparison must not accept corrupt guard evidence')
    monkeypatch.setattr(m,'run',native);monkeypatch.setattr(m,'compare',forbidden)
    with pytest.raises(ValueError,match='incomplete'):m.qualify(tmp_path/'out',tmp_path/'root',names=('two_active_limiters_stress',))


@pytest.mark.parametrize('bad',[None,'missing','index','name','cause','duplicate'])
def test_continuous_profile_requires_own_named_root_provenance(evidence,bad):
    from benchmarks.native_uel_roots import ATOMIC_INDICATORS
    s,b=evidence;b['native_UEL_root_policy']='continuous_atoms';e=b['event_log'][0]
    e['root_indicator_indices']=[2];e['root_indicator_names']=[ATOMIC_INDICATORS[2]]
    if bad=='missing':del e['root_indicator_indices']
    elif bad=='index':e['root_indicator_indices']=[4]
    elif bad=='name':e['root_indicator_names']=['native_compound_0']
    elif bad=='cause':e['root_indicator_indices']=[];e['root_indicator_names']=[]
    elif bad=='duplicate':e['root_indicator_indices']=[2,2];e['root_indicator_names']*=2
    if bad is None:assert audit(s,b)['passed']
    else:
        with pytest.raises(ValueError,match='root'):audit(s,b)
