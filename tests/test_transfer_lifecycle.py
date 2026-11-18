import copy
import numpy as np
import pytest
from benchmarks.transfer_lifecycle_cases import example,SCENARIOS
from benchmarks.transfer_lifecycle_reference import TransferLifecycleReference
from benchmarks.transfer_lifecycle_acceptance import compare,coverage
from power_grid_env.study_spec import StudySpec,run_study


@pytest.fixture(scope='module')
def refs():return {k:TransferLifecycleReference(example(k)).run() for k in SCENARIOS}


@pytest.fixture(scope='module')
def reports():
    result={}
    for k in SCENARIOS:
        for method in ('radau','network_dae'):
            s=example(k);s['coupled']['integration_method']=method
            result[k,method]=run_study(StudySpec.model_validate(s))
    return result


@pytest.mark.parametrize('scenario',SCENARIOS)
@pytest.mark.parametrize('method',['radau','network_dae'])
def test_transfer_lifecycle_matches_independent_equations(scenario,method,refs,reports):
    r=reports[scenario,method]
    assert r['completed'],r
    assert r['additional_validation']['ok'] and compare(refs[scenario],r)['passed']
    assert coverage(r['event_log'],scenario)==coverage(refs[scenario].events,scenario)


def test_primary_PT_loss_resets_detection_memory_without_physical_recovery(refs):
    ref=refs['primary_pt_pause'];a=ref.sample(.055,'post_event')
    assert a['ats.progress']==pytest.approx(.5,abs=1e-10)
    assert a['ats.active']==0 and a['ats.primary_measurement_valid']==0
    assert a['ats.primary_voltage_pu']==0 and a['source.connected']==1
    q=ref.sample(.065)
    assert q['ats.progress']==pytest.approx(.5/np.e,abs=1e-10)
    t=next(e['time_s'] for e in ref.events if e['kind']=='transfer_started')
    assert t==pytest.approx(.075+(1-.5*np.exp(-2))*.01,abs=2e-10)


def test_reserve_PT_rechecked_at_delayed_close_not_only_at_request(refs,reports):
    ref=refs['reserve_pt_inflight'];request=next(e for e in ref.events if e['kind']=='breaker_close_requested')
    close=next(e for e in ref.events if e['kind']=='breaker_close')
    assert request['time_s']==pytest.approx(.075) and close['time_s']==pytest.approx(.08)
    assert close['outcome']=='blocked' and close['synchronism']['reason']=='reserve_measurement_unavailable'
    for r in (reports['reserve_pt_inflight','radau'],reports['reserve_pt_inflight','network_dae']):
        assert max(r['channels']['g.connected'])==0 and r['channels']['reserve-cb.close_attempts'][-1]==1


@pytest.mark.parametrize('scenario',['retransfer','repeat_inhibited'])
def test_return_is_break_before_make_and_reset_keeps_manual_inhibit(scenario,refs):
    ref=refs[scenario]
    for t in np.arange(151)*.002:
        q=ref.sample(float(t));assert q['g.connected']+q['source.connected']<=1
    assert ref.sample(.17)['source.connected']==0
    assert ref.sample(.2)['g.connected']==0 and ref.sample(.2)['source.connected']==0
    assert ref.sample(.22)['source.connected']==1
    assert ref.sample(.24)['reserve-cb.manual_inhibit']==1
    if scenario=='repeat_inhibited':
        close=[e for e in ref.events if e['kind']=='breaker_close'][-1]
        assert close['outcome']=='blocked' and close['reason']=='manual_inhibit'
        assert ref.sample(.3)['reserve-cb.close_attempts']==1


def test_reserve_CT_unavailable_during_transfer_does_not_fabricate_trip(refs):
    ref=refs['ct_during_transfer'];a=ref.sample(.09)
    assert a['g.connected']==1 and a['reserve-oc.measurement_valid']==0 and a['reserve-oc.progress']==0
    assert a['g.current_pu']>0 and a['reserve-oc.measurement']==0
    trip=next(e for e in ref.events if e['kind']=='network_relay_trip')
    assert trip['time_s']==pytest.approx(.11,abs=2e-10)


@pytest.mark.parametrize('method',['radau','network_dae'])
def test_rounded_transfer_deadline_obeys_scheduled_command_priority(method,reports):
    events=reports['ct_during_transfer',method]['event_log']
    loss=next(i for i,e in enumerate(events) if e['kind']=='relay_measurement_lost')
    request=next(i for i,e in enumerate(events) if e['kind']=='breaker_close_requested')
    assert loss<request
    assert events[loss]['time_s']==events[request]['time_s']==.075


@pytest.mark.parametrize('method',['radau','network_dae'])
@pytest.mark.parametrize('time',[.074,.076])
def test_distinct_measurement_command_is_not_snapped_to_deadline(method,time):
    s=example('ct_during_transfer');s['coupled']['integration_method']=method
    next(e for e in s['coupled']['events'] if e['kind']=='relay_measurement_lost')['time_s']=time
    r=run_study(StudySpec.model_validate(s));assert r['completed']
    events=r['event_log'];loss=next(i for i,e in enumerate(events) if e['kind']=='relay_measurement_lost')
    request=next(i for i,e in enumerate(events) if e['kind']=='breaker_close_requested')
    assert (loss<request)==(time<.075)
    assert events[loss]['time_s']==time


def test_PT_mode_invalidates_DAE_signature_and_clock_action():
    from tests.test_two_cage_winding import initial
    from power_grid_env.dae import mode_token
    m,x,_=initial(example());bank=m.automation;bank.initialize(x)
    old=mode_token(m);bank.transfer_available['ats']['reserve']=False
    assert mode_token(m)!=old
    bank.transfer_available['ats']['primary']=False;bank.ats_active[0]=True
    x[bank.offset]=.5
    assert bank.derivatives(0,x,m.electrical(x)[0])[0]==-.5/.01
    cb=m.integration_jacobian();u=np.zeros(len(x));u[bank.offset]=1.
    actual=cb.jacobian_actions(0,x,u[:,None],m.electrical(x))[:,0]
    assert actual[bank.offset]==pytest.approx(-100.)


@pytest.mark.parametrize('scenario',['primary_pt_pause','ct_during_transfer'])
def test_initial_audit_distinguishes_standby_internal_voltage_from_bus(scenario,reports):
    r=reports[scenario,'radau'];a=r['operating_point']['initial_voltage_constraints']['g']
    assert a['kind']=='standby_internal_voltage_target'
    assert a['requested_internal_voltage_pu']==pytest.approx(.96)
    assert a['solved_voltage_pu']==pytest.approx(.96)
    assert a['solved_bus_voltage_pu']==pytest.approx(r['channels']['g.bus_voltage_pu'][0])
    assert abs(a['solved_bus_voltage_pu']-a['solved_voltage_pu'])>.01


@pytest.mark.parametrize('event',['transfer_primary_measurement_lost','transfer_primary_measurement_restored','transfer_reserve_measurement_lost','transfer_reserve_measurement_restored'])
def test_unknown_supply_measurement_target_rejected(event):
    s=example();s['coupled']['events'].append(dict(time_s=.06,kind=event,equipment_id='missing'))
    r=run_study(StudySpec.model_validate(s));assert not r['completed']
    assert 'unknown transfer measurement target' in str(r)


@pytest.mark.parametrize('mutation',['side','valid','reason','interlock','inhibit','clock'])
def test_modified_availability_and_return_evidence_rejected(mutation,refs,reports):
    k='primary_pt_pause' if mutation in ('side','valid','clock') else 'retransfer' if mutation=='interlock' else 'repeat_inhibited' if mutation=='inhibit' else 'reserve_pt_inflight'
    r=copy.deepcopy(reports[k,'radau'])
    if mutation in ('side','valid'):
        e=next(e for e in r['event_log'] if e['kind']=='transfer_primary_measurement_lost')
        e['measurement_side' if mutation=='side' else 'measurement_available']='reserve' if mutation=='side' else True
    elif mutation=='reason':next(e for e in r['event_log'] if e['kind']=='breaker_close')['synchronism']['reason']='synchronized'
    elif mutation=='interlock':next(e for e in r['event_log'] if e.get('synchronism',{}).get('reason')=='interlocked_breaker_closed')['synchronism']['interlock_id']='grid-cb'
    elif mutation=='inhibit':r['channels']['reserve-cb.manual_inhibit'][-1]=0.
    else:r['channels']['ats.progress'][-1]+=.01
    assert not compare(refs[k],r)['passed']


def test_reference_does_not_use_native_availability_or_initialization(monkeypatch):
    import power_grid_env.network_automation as auto
    import power_grid_env.coupled_initialization as init
    def forbidden(*a,**k):raise AssertionError('native helper called')
    for name in ('service','refresh','guards','command','check_close','derivatives'):monkeypatch.setattr(auto.NetworkAutomationBank,name,forbidden)
    monkeypatch.setattr(init,'initialize_coupled',forbidden)
    for k in ('primary_pt_pause','ct_during_transfer','retransfer'):
        r=TransferLifecycleReference(example(k)).run();assert coverage(r.events,k)
