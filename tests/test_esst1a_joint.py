"""Joint native AVR/UEL feedback: hidden states and independently timed resets."""
from pathlib import Path
from functools import lru_cache
import copy,os,json
import numpy as np
import pytest
from benchmarks.generator_esst1a_joint_cases import NAMES,NEGATIVE,example
from benchmarks.generator_esst1a_joint_validation import compare,significant_resets
from power_grid_env.study_spec import run_study,StudySpec

@pytest.fixture(scope='module')
def joint(tmp_path_factory):
    root=os.environ.get('PGE_OPENMODELICA_ROOT')
    if not root:pytest.skip('optional pinned OpenModelica runtime')
    from benchmarks.esst1a_network import run
    out=tmp_path_factory.mktemp('joint-native')
    @lru_cache(None)
    def result(name):
        s=example(name);a=run_study(s);assert a['completed'],a
        b=run(s,out/name,Path(root),1e-11,native_uels=True,uel_event_policy='broadcast',uel_root_policy='compound_and_atoms')
        return s,a,b
    return result

@pytest.mark.parametrize('name',NAMES)
def test_two_native_regulators_all_states_node_voltages_and_reset_sides(name,joint):
    s,a,b=joint(name);c=compare(s,a,b)
    if name in NEGATIVE:
        assert not c['passed'] and c['reason']=='native_UEL_reset_sides_differ' and c['event_time_error_s']>1e-6
        return
    assert c['passed'],c
    assert b['native_ESST1A_in_feedback'] and b['native_MNLEX2_in_feedback']
    assert not b['production_AVR_RHS_used'] and not b['primary_event_clocks_used']
    for gid,initial in b['native_UEL_initialization'].items():
        assert len(initial['native_states'])==2 and initial['initial_residual_per_s']<1e-8
        assert initial['native_algebraic_initial_raw_pu']<0
        assert not initial['reference']['UEL_equations_modified']
        assert {gid+'.UEL.'+k for k in ['raw_lag_state_pu','feedback_memory_pu','output_pu','circle_error_pu2']}<=c['errors'].keys()
    assert a['maximum_kcl_residual_pu']<1e-8 and a['maximum_power_balance_residual_pu']<1e-8
    if name in ('activation_recovery','saturation_recovery','fast_feedback'):
        assert max(a['channels']['g2.UEL.output_pu'])>1e-4
        assert a['channels']['g2.UEL.output_pu'][-1]==0
        assert c['native_UEL_reset_checks']
    if name=='saturation_recovery':assert max(a['channels']['g2.UEL.raw_lag_state_pu'])>s.coupled.underexcitation_limiters[0].MEL_MAX
    json.dumps(c,allow_nan=False)

@pytest.mark.parametrize('drive',[(.1,.1,1.),(.05,.02,.975),(.21,.11,1.03),(-.1,.1,.8),(0.,0.,1.),(.1,.3,1.)])
def test_native_initialization_uses_own_algebra_not_state_injection(drive,tmp_path,monkeypatch):
    root=os.environ.get('PGE_OPENMODELICA_ROOT')
    if not root:pytest.skip('optional pinned OpenModelica runtime')
    import sys
    sys.path.insert(0,str(Path(root)/'fmpy'))
    from fmpy.fmi2 import FMU2Model
    def forbidden(*args,**kwargs):raise AssertionError('continuous state injection during native initialization')
    monkeypatch.setattr(FMU2Model,'setContinuousStates',forbidden)
    from benchmarks.mnlex2_model_exchange import NativeUEL
    from power_grid_env.generator_underexcitation import UnderexcitationLimiter
    p=UnderexcitationLimiter(generator_id='witness',Q_0=.3,Radius=.4)
    b=NativeUEL(p,*drive,tmp_path/'native',Path(root),2.,1e-11)
    try:
        q=b.signals();assert b.initial_residual<1e-8 and q['output_pu']==0
        assert q['raw_lag_state_pu']==pytest.approx(p.K_M*q['circle_error_pu2'],abs=1e-12)
        assert q['feedback_memory_pu']==0
        assert b.nz==b.native_nz+4
    finally:b.close()

@pytest.mark.parametrize('bad',['missing_reset','wrong_reset_time','wrong_reset_bound','missing_internal_state','fake_native_claim'])
def test_incomplete_or_false_joint_evidence_is_rejected(bad,joint):
    s,a,b=joint('activation_recovery');b=copy.deepcopy(b)
    if bad=='missing_reset':
        event=significant_resets(b,True)[0];b['event_log'].remove(event)
    if bad=='wrong_reset_time':significant_resets(b,True)[0]['time_s']+=.001
    if bad=='wrong_reset_bound':significant_resets(b,True)[0]['bound']='upper'
    if bad=='missing_internal_state':del b['channels']['g2.UEL.raw_lag_state_pu']
    if bad=='fake_native_claim':b['native_MNLEX2_in_feedback']=False
    try:c=compare(s,a,b)
    except ValueError:return
    assert not c['passed'],c


def test_event_closure_compares_actual_sides_without_modifying_saved_traces(joint):
    s,a,b=joint('activation_recovery');before=json.dumps([a,b],sort_keys=True);r=compare(s,a,b)
    assert r['passed'] and json.dumps([a,b],sort_keys=True)==before
    assert all(0<=v['sampled_time_s']-v['command_time_s']<=1e-6 for v in r['command_causal_closure'])
    assert max(v['time_error_s'] for v in r['native_UEL_reset_checks'])<1e-6

@pytest.mark.parametrize('change',['startup','active_circle','zero_voltage','nonfinite','small_time','invalid_horizon','invalid_tolerance'])
def test_unsupported_joint_UEL_fails_before_native_build(change,tmp_path):
    from benchmarks.mnlex2_model_exchange import NativeUEL
    from power_grid_env.generator_underexcitation import UnderexcitationLimiter
    p=UnderexcitationLimiter(generator_id='negative',Q_0=.3,Radius=.4);values=[.1,.1,1.];stop=2.;tol=1e-11
    if change=='startup':p.initialization='zero_output_startup'
    if change=='active_circle':values[0]=1.
    if change=='zero_voltage':values[2]=0.
    if change=='nonfinite':values[1]=float('nan')
    if change=='small_time':p.T_M=1e-9
    if change=='invalid_horizon':stop=10.
    if change=='invalid_tolerance':tol=float('nan')
    with pytest.raises(ValueError):NativeUEL(p,*values,tmp_path/'native',tmp_path/'missing-runtime',stop,tol)
    assert not (tmp_path/'native').exists()


def test_native_joint_profile_refuses_missing_UEL_passport(tmp_path):
    from benchmarks.esst1a_network import run
    s=example('hold');s.coupled.underexcitation_limiters=[]
    with pytest.raises(ValueError,match='explicit UEL'):run(s,tmp_path/'out',tmp_path/'missing',native_uels=True)
    assert not (tmp_path/'out').exists()


def test_source_lock_change_is_not_accepted(monkeypatch,tmp_path):
    from benchmarks import mnlex2_model_exchange as module
    lock=json.loads(module.LOCK.read_text());lock['AVR_lock_sha256']='0'*64
    path=tmp_path/'lock.json';path.write_text(json.dumps(lock));monkeypatch.setattr(module,'LOCK',path);monkeypatch.setattr(module,'verify_avr',lambda root:{})
    with pytest.raises(ValueError,match='AVR lock'):module.verify(tmp_path)


def test_registered_inputs_are_replayable_from_JSON():
    for name in NAMES:
        s=example(name);assert StudySpec.model_validate_json(s.model_dump_json()).model_dump()==s.model_dump()


def test_native_driver_does_not_reuse_production_UEL_rates_or_network_RHS(tmp_path,monkeypatch):
    root=os.environ.get('PGE_OPENMODELICA_ROOT')
    if not root:pytest.skip('optional pinned OpenModelica runtime')
    from power_grid_env import generator_underexcitation,coupled_rms
    from benchmarks.esst1a_network import run
    def forbidden(*args,**kwargs):raise AssertionError('production physics entered native reference')
    monkeypatch.setattr(generator_underexcitation,'mnlex2_rates',forbidden)
    monkeypatch.setattr(coupled_rms.CoupledNetwork,'rhs',forbidden)
    s=example('hold');s.coupled.duration_s=.05
    b=run(s,tmp_path/'native',Path(root),1e-11,native_uels=True)
    assert b['completed'] and b['native_MNLEX2_in_feedback']


def test_zero_time_event_batch_preserves_extreme_observed_sides():
    from benchmarks.generator_esst1a_joint_validation import coalesce_zero_time_batches
    r=dict(time_s=[0.,1.,1.,1.+1e-14,1.+1e-14,2.],sample_side=['initial','pre_event','post_event','pre_event','post_event','regular'],channels={'x':[0.,1.,2.,2.,3.,4.]})
    before=copy.deepcopy(r);a=coalesce_zero_time_batches(r)
    assert r==before and a['channels']['x']==[0.,1.,3.,4.]
    assert a['sample_side']==['initial','pre_event','post_event','regular']


def test_negative_witness_cannot_accept_arbitrary_bad_channels():
    from benchmarks.generator_esst1a_joint_validation import expected_rejection
    assert not expected_rejection('two_limiters_sensitive',dict(passed=False,reason='native_UEL_reset_sides_differ',generator_id='g',event_time_error_s=.1,raw_jump_error_pu=0.))


def test_negative_witness_audits_all_resets_and_physics(joint):
    from benchmarks.generator_esst1a_joint_validation import expected_rejection
    s,a,b=joint('two_limiters_sensitive');before=json.dumps([a,b],sort_keys=True)
    c=compare(s,a,b)
    assert expected_rejection('two_limiters_sensitive',c)
    assert c['reset_audit_complete'] and len(c['reset_failures'])==4
    assert all(f['generator_id']=='g' for f in c['reset_failures'])
    assert len(c['native_UEL_reset_checks'])==len(significant_resets(a))
    assert c['negative_witness_physics_diagnostic']['passed']
    assert c['negative_witness_physics_diagnostic']['qualifies_trajectory'] is False
    assert c['event_limit_s']==1e-6 and not c['passed']
    assert json.dumps([a,b],sort_keys=True)==before


@pytest.mark.parametrize('bad',['late_missing_reset','late_wrong_bound','late_time_error','second_generator_reset','bad_field','bad_uel_memory','bad_raw_state','bad_voltage','missing_channel','unknown_reset','incomplete','nonfinite_channel'])
def test_negative_first_failure_does_not_hide_other_corruption(bad,joint):
    from benchmarks.generator_esst1a_joint_validation import expected_rejection
    s,a,b=joint('two_limiters_sensitive');b=copy.deepcopy(b)
    resets=significant_resets(b,True)
    if bad=='late_missing_reset':b['event_log'].remove(resets[-1])
    elif bad=='late_wrong_bound':resets[-1]['bound']='upper'
    elif bad=='late_time_error':resets[-1]['time_s']+=.01
    elif bad=='second_generator_reset':next(e for e in reversed(resets) if e['equipment_id']=='g2')['time_s']+=5e-6
    elif bad=='unknown_reset':b['event_log'].append(dict(resets[-1],equipment_id='ghost'))
    elif bad=='incomplete':b['completed']=False
    elif bad=='missing_channel':del b['channels']['g.UEL.feedback_memory_pu']
    elif bad=='bad_voltage':b['node_voltage'][next(iter(b['node_voltage']))]['real_v'][-1]+=100.
    else:
        key={'bad_field':'g.field_pu','bad_uel_memory':'g.UEL.feedback_memory_pu','bad_raw_state':'g.UEL.raw_lag_state_pu','nonfinite_channel':'g.field_pu'}[bad]
        b['channels'][key][-1]=float('nan') if bad=='nonfinite_channel' else b['channels'][key][-1]+.01
    try:c=compare(s,a,b)
    except ValueError:return
    assert not expected_rejection('two_limiters_sensitive',c),bad


@pytest.mark.parametrize('key',['time_s','raw_state_before_pu','raw_state_after_pu'])
@pytest.mark.parametrize('value',[float('nan'),float('inf'),-float('inf')])
def test_nonfinite_event_evidence_is_rejected_even_below_jump_resolution(key,value):
    event=dict(kind='uel_lag_reset',time_s=.2,raw_state_before_pu=0.,raw_state_after_pu=0.,bound='lower')
    event[key]=value
    with pytest.raises(ValueError,match='nonfinite'):significant_resets(dict(event_log=[event]))
