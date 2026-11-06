import pytest
from benchmarks.network_automation_reference import NAMES,example,reference,compare
from power_grid_env.study_spec import run_study

@pytest.mark.parametrize('name',NAMES)
def test_automation_matches_independent_SI_dynamics_and_prescribed_timelines(name):
    spec=example(name);ref=reference(name,spec);primary=run_study(spec)
    result=compare(primary,ref);assert result['passed'],result

@pytest.mark.parametrize('defect',['extra_trip','missing_action','nonfinite','missing_sample'])
def test_automation_reference_fails_closed(defect):
    spec=example('overcurrent');ref=reference('overcurrent',spec);primary=run_study(spec)
    if defect=='extra_trip':primary['event_log'].append(dict(kind='network_relay_trip',equipment_id='unexpected',time_s=.3))
    elif defect=='missing_action':primary['event_log']=[e for e in primary['event_log'] if e['kind']!='network_relay_trip']
    elif defect=='nonfinite':primary['channels']['g.omega'][0]=float('nan')
    else:primary['time_s'][0]=.00001
    if defect in {'extra_trip','missing_action'}:assert not compare(primary,ref)['passed']
    else:
        with pytest.raises(ValueError):compare(primary,ref)


def test_reference_rejects_a_changed_unqualified_network():
    spec=example('standby_transfer');spec.network.lines[0].r_ohm[0][0]+=.1
    with pytest.raises(ValueError,match='explicitly frozen'):reference('standby_transfer',spec)

@pytest.mark.parametrize('defect',['wrong_position','missing_block'])
def test_standby_transfer_reference_detects_unsafe_behavior(defect):
    spec=example('standby_transfer');ref=reference('standby_transfer',spec);primary=run_study(spec)
    if defect=='wrong_position':primary['channels']['reserve-cb.closed'][-1]=0.
    else:primary['event_log']=[e for e in primary['event_log'] if e.get('synchronism',{}).get('reason')!='interlocked_breaker_closed']
    assert not compare(primary,ref)['passed']


@pytest.mark.parametrize('defect',['empty_reference','incomplete_reference','missing_channel','nan_reference','wrong_failure_delay'])
def test_automation_evidence_cannot_hide_missing_or_invalid_reference(defect):
    spec=example('failed_primary_transfer');ref=reference('failed_primary_transfer',spec);primary=run_study(spec)
    if defect=='empty_reference':ref['channels']={}
    elif defect=='incomplete_reference':ref['completed']=False
    elif defect=='missing_channel':del ref['channels']['g.delta']
    elif defect=='nan_reference':ref['channels']['g.omega'][0]=float('nan')
    else:
        for event in primary['event_log']:
            if event['kind']=='breaker_open' and event.get('outcome')=='failed_to_operate':event['time_s']+=.01
    if defect=='wrong_failure_delay':assert not compare(primary,ref)['passed']
    else:
        with pytest.raises(ValueError):compare(primary,ref)
