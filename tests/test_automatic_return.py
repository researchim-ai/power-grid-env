import copy
from types import SimpleNamespace
import numpy as np
import pytest
from benchmarks.automatic_return_cases import example,SCENARIOS
from benchmarks.automatic_return_reference import AutomaticReturnReference
from benchmarks.automatic_return_acceptance import compare,coverage
from power_grid_env.study_spec import StudySpec,run_study


@pytest.fixture(scope='module')
def refs():return {k:AutomaticReturnReference(example(k)).run() for k in SCENARIOS}


@pytest.fixture(scope='module')
def reports():
    rows={}
    for k in SCENARIOS:
        for method in ('radau','network_dae'):
            s=example(k);s['coupled']['integration_method']=method
            rows[k,method]=run_study(StudySpec.model_validate(s))
    return rows


@pytest.mark.parametrize('scenario',SCENARIOS)
@pytest.mark.parametrize('method',['radau','network_dae'])
def test_autonomous_return_matches_independent_SI(scenario,method,refs,reports):
    r=reports[scenario,method];assert r['completed'],r
    assert r['additional_validation']['ok'] and compare(refs[scenario],r)['passed']
    assert coverage(r['event_log'],scenario)==coverage(refs[scenario].events,scenario)


@pytest.mark.parametrize('scenario',['dwell_dip','pt_dwell'])
def test_recovery_requires_fresh_full_delay_after_loss(scenario,refs):
    ref=refs[scenario];pre,post=[ref.sample(.15,side) for side in ('pre_event','post_event')]
    assert pre['ats.return.progress']==pytest.approx(.5,abs=1e-10)
    assert post['ats.return.progress']==0 and post['ats.return.active']==0
    assert ref.sample(.17,'post_event')['ats.return.progress']==0
    start=next(e['time_s'] for e in ref.events if e['kind']=='automatic_return_started')
    assert start==pytest.approx(.19,abs=2e-10)


@pytest.mark.parametrize('scenario,removed',[('cancel_reset',2),('cancel_after_open',1)])
def test_cancel_removes_only_pending_actions_not_physical_opening(scenario,removed,refs):
    ref=refs[scenario];cancel=next(e for e in ref.events if e['kind']=='return_cancel')
    assert cancel['cancelled_actions']==removed
    assert ref.sample(.17,'post_event')['g.connected']==int(scenario=='cancel_reset')
    if scenario=='cancel_reset':
        assert ref.sample(.18)['source.connected']==0 and ref.sample(.18)['g.connected']==1
        assert ref.sample(.24)['source.connected']==1


@pytest.mark.parametrize('method',['radau','network_dae'])
def test_cancel_after_primary_close_request_prevents_ghost_closing(method):
    s=example('cancel_after_open');s['coupled']['integration_method']=method
    next(e for e in s['coupled']['events'] if e['kind']=='return_cancel')['time_s']=.177
    r=run_study(StudySpec.model_validate(s));assert r['completed']
    assert next(e for e in r['event_log'] if e['kind']=='return_cancel')['cancelled_actions']==1
    assert not any(e['kind']=='breaker_close' and e.get('cause','').startswith('automatic_return:') for e in r['event_log'])


def test_cancellation_ownership_does_not_match_transfer_ID_prefix():
    from power_grid_env.automatic_return import remove_pending
    q=[(.2,3,'close','a','automatic_return:ats:1'),(.2,3,'close','b','automatic_return:ats:child:1'),(.3,0,'open','a','transfer:ats')]
    bank=SimpleNamespace(queue=q,keys=set())
    assert remove_pending(bank,'ats')==1
    assert {v[4] for v in bank.queue}=={'automatic_return:ats:child:1','transfer:ats'}
    assert len(bank.keys)==2


@pytest.mark.parametrize('method',['radau','network_dae'])
def test_manual_open_survives_cancellation_of_coincident_return_request(method):
    s=example('success');s['coupled']['integration_method']=method
    s['coupled']['events'] += [dict(time_s=.16,kind='breaker_open',equipment_id='reserve-cb'),dict(time_s=.162,kind='return_cancel',equipment_id='ats')]
    r=run_study(StudySpec.model_validate(s));assert r['completed']
    opened=[e for e in r['event_log'] if e['kind']=='breaker_open' and e['equipment_id']=='reserve-cb' and e.get('outcome')=='applied']
    assert len(opened)==1 and opened[0]['cause']=='manual'
    assert opened[0]['time_s']==pytest.approx(.165,abs=2e-10)
    assert next(e for e in r['event_log'] if e['kind']=='return_cancel')['cancelled_actions']==1
    assert r['channels']['reserve-cb.manual_inhibit'][-1]==1


def test_simultaneous_health_loss_precedes_return_expiry():
    from tests.test_two_cage_winding import initial
    m,x,_=initial(example());bank=m.automation;bank.initialized=True
    bank.set_position(bank.breakers['grid-cb'],False);bank.set_position(bank.breakers['reserve-cb'],True);m.factorize()
    bank.return_active['ats']=True;x[bank.return_entries['ats'][0]]=1.
    m.source_scale=.85/abs(m.source_e);events=[]
    bank.apply({('return_due','ats'),('return_health','ats')},.16,x,events)
    assert not bank.queue and bank.return_generation['ats']==0
    assert x[bank.return_entries['ats'][0]]==0
    assert [e['kind'] for e in events]==['return_recovery_cancelled']


def test_return_clock_analytic_row_and_DAE_mode_token():
    from tests.test_two_cage_winding import initial
    from power_grid_env.dae import mode_token
    m,x,_=initial(example());b=m.automation;b.initialize(x);a=b.return_entries['ats'][0]
    before=mode_token(m);b.return_active['ats']=True
    assert mode_token(m)!=before
    assert b.derivatives(0,x,m.electrical(x)[0])[a-b.offset]==50.
    u=np.zeros((len(x),1));u[a]=1.
    action=m.integration_jacobian().jacobian_actions(0,x,u,m.electrical(x))
    assert np.max(abs(action))==0.


@pytest.mark.parametrize('scenario',['loss_after_open','inflight_drop'])
def test_primary_failure_after_reserve_open_rearms_forward_ATS(scenario,refs):
    ref=refs[scenario];events=ref.events
    assert len([e for e in events if e['kind']=='automatic_return_fallback_armed'])==1
    assert len([e for e in events if e['kind']=='transfer_started'])==2
    for i in range(151):
        q=ref.sample(i*.002);assert q['source.connected']+q['g.connected']<=1
    assert ref.sample(.22)['g.connected']==1


@pytest.mark.parametrize('mutation',['generation','cancel_count','progress','mode','close_reason'])
def test_modified_policy_evidence_rejected(mutation,refs,reports):
    scenario='cancel_reset' if mutation=='cancel_count' else 'loss_after_open' if mutation=='close_reason' else 'success'
    r=copy.deepcopy(reports[scenario,'radau'])
    if mutation=='generation':next(e for e in r['event_log'] if e['kind']=='automatic_return_started')['generation']+=1
    elif mutation=='cancel_count':next(e for e in r['event_log'] if e['kind']=='return_cancel')['cancelled_actions']=0
    elif mutation in ('progress','mode'):r['channels']['ats.return.'+mutation][-1]+=1.
    else:next(e for e in r['event_log'] if e.get('synchronism',{}).get('reason')=='angle_difference')['synchronism']['reason']='synchronized'
    assert not compare(refs[scenario],r)['passed']


def test_policy_uses_no_native_return_or_switching_helpers(monkeypatch):
    import power_grid_env.automatic_return as module
    import power_grid_env.network_automation as auto
    def forbidden(*a,**k):raise AssertionError('native policy helper called')
    for name in ('configure','refresh','guards','apply','service','gate','command','derivatives'):monkeypatch.setattr(module,name,forbidden)
    for name in ('service','refresh','command','check_close'):monkeypatch.setattr(auto.NetworkAutomationBank,name,forbidden)
    for scenario in ('success','cancel_reset','loss_after_open'):
        ref=AutomaticReturnReference(example(scenario)).run();assert coverage(ref.events,scenario)


def test_disabled_policy_preserves_legacy_serialization_and_dimension():
    from benchmarks.measurement_transfer_cases import example as old
    from tests.test_two_cage_winding import initial
    s=old('ats_transfer','passport');owner=StudySpec.model_validate(s)
    assert 'return_policy' not in owner.coupled.network_automation.transfers[0].model_dump(mode='json')
    a,x,_=initial(s);b,y,_=initial(example())
    assert len(y)==len(x)+1 and not a.automation.return_entries


@pytest.mark.parametrize('feature',['overlap','invalid_hysteresis','command_disabled'])
def test_invalid_return_configuration_rejected(feature):
    s=example();a=s['coupled']['network_automation']
    if feature=='overlap':
        other=copy.deepcopy(a['transfers'][0]);other['id']='ats:child';a['transfers'].append(other)
    elif feature=='invalid_hysteresis':a['transfers'][0]['return_policy']['dropout_pu']=.95
    else:
        a['transfers'][0].pop('return_policy');s['coupled']['events'].append(dict(time_s=.2,kind='return_cancel',equipment_id='ats'))
        r=run_study(StudySpec.model_validate(s));assert not r['completed'];return
    with pytest.raises(ValueError):StudySpec.model_validate(s)
