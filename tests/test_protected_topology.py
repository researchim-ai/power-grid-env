import copy
import math
from decimal import Decimal,localcontext
import numpy as np
import pytest

from benchmarks.protected_topology_cases import example,SCENARIOS
from benchmarks.protected_topology_reference import ProtectedTopologyReference
from benchmarks.protected_topology_acceptance import compare,coverage
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.protection import iec_operate_time


@pytest.mark.parametrize('curve,k,alpha',[('standard_inverse',.14,.02),('very_inverse',13.5,1.),('extremely_inverse',80.,2.)])
@pytest.mark.parametrize('pickup',[1.,150.,1e6])
def test_inverse_curves_next_float_above_pickup_match_high_precision(curve,k,alpha,pickup):
    current=math.nextafter(pickup,math.inf)
    with localcontext() as ctx:
        ctx.prec=80
        ratio=Decimal(current)/Decimal(pickup)
        expected=Decimal(.0002)*Decimal(k)/((Decimal(alpha)*ratio.ln()).exp()-1)
    actual=iec_operate_time(current,pickup,curve,.0002)
    assert math.isfinite(actual) and actual>0
    assert actual==pytest.approx(float(expected),rel=3e-15)
    assert math.isinf(iec_operate_time(pickup,pickup,curve,.0002))


@pytest.fixture(scope='module')
def refs():return {kind:ProtectedTopologyReference(example(kind)).run() for kind in SCENARIOS}


@pytest.fixture(scope='module')
def reports():
    rows={}
    for kind in SCENARIOS:
        for method in ('radau','network_dae'):
            s=example(kind);s['coupled']['integration_method']=method
            rows[kind,method]=run_study(StudySpec.model_validate(s))
    return rows


@pytest.mark.parametrize('kind',SCENARIOS)
@pytest.mark.parametrize('method',['radau','network_dae'])
def test_active_protection_island_backup_and_reclose_reference(kind,method,refs,reports):
    report=reports[kind,method]
    assert report['completed'],report
    assert report['additional_validation']['ok'] and compare(refs[kind],report)['passed']
    assert coverage(refs[kind].events,kind)==coverage(report['event_log'],kind)


@pytest.mark.parametrize('kind',['definite_reclose','standard_inverse','very_inverse','extremely_inverse'])
def test_successful_reclose_resets_clock_and_rearms_retrip_once(kind,refs):
    ref=refs[kind];close=next(e['time_s'] for e in ref.events if e['kind']=='breaker_close')
    pre,post=[ref.sample(close,side) for side in ('pre_event','post_event')]
    assert pre['primary-oc.latched']==1 and pre['primary-oc.progress']==1
    assert post['primary-oc.latched']==0 and post['primary-oc.progress']==0
    assert post['primary-oc.active']==1 and post['primary-cb.reclose_shots']==1
    assert post['primary-cb.closed']==1
    assert coverage(ref.events,kind)['relay_trips']==2
    final=ref.sample(.5)
    assert final['primary-cb.closed']==0 and final['primary-cb.reclose_shots']==1


@pytest.mark.parametrize('kind,attempts',[('manual_inhibit',0),('sync_blocked',1)])
def test_reclose_blocking_causes_have_distinct_attempt_semantics(kind,attempts,refs):
    ref=refs[kind];q=ref.sample(.5)
    assert q['primary-cb.close_attempts']==attempts and q['primary-cb.closed']==0
    assert q['primary-cb.reclose_shots']==1 and q['primary-cb.open_attempts']==1
    assert q['primary-cb.manual_inhibit']==int(kind=='manual_inhibit')


@pytest.mark.parametrize('mutation',['cause','backup_target','dead_time','manual_reason','shot_count','clock_reset'])
def test_mutated_protection_evidence_rejected(mutation,refs,reports):
    kind='failure_backup' if mutation=='backup_target' else 'manual_inhibit' if mutation=='manual_reason' else 'very_inverse'
    report=copy.deepcopy(reports[kind,'radau'])
    if mutation=='cause':next(e for e in report['event_log'] if e['kind']=='breaker_open')['cause']='manual'
    elif mutation=='backup_target':next(e for e in report['event_log'] if e['kind']=='breaker_open_requested' and e['cause'].startswith('breaker_failure'))['equipment_id']='primary-cb'
    elif mutation=='dead_time':next(e for e in report['event_log'] if e['kind']=='breaker_close')['time_s']+=.001
    elif mutation=='manual_reason':next(e for e in report['event_log'] if e['kind']=='breaker_close')['reason']='synchronized'
    elif mutation=='shot_count':report['channels']['primary-cb.reclose_shots'][-1]=2.
    else:
        t=next(e['time_s'] for e in report['event_log'] if e['kind']=='breaker_close');i=next(i for i,(time,side) in enumerate(zip(report['time_s'],report['sample_side'])) if time==t and side=='post_event')
        report['channels']['primary-oc.progress'][i]=1.
    assert not compare(refs[kind],report)['passed']


def test_independent_reference_does_not_use_native_clock_or_queue(monkeypatch):
    import power_grid_env.network_automation as auto
    import power_grid_env.protection as protection
    def forbidden(*a,**k):raise AssertionError('native helper called')
    monkeypatch.setattr(auto,'iec_operate_time',forbidden);monkeypatch.setattr(protection,'iec_operate_time',forbidden)
    for k in ('guards','apply','service','request','refresh','check_close','derivatives'):
        monkeypatch.setattr(auto.NetworkAutomationBank,k,forbidden)
    for kind in ('very_inverse','failure_backup','undervoltage'):
        ref=ProtectedTopologyReference(example(kind)).run();assert coverage(ref.events,kind)['relay_trips']>=1


@pytest.mark.parametrize('method',['radau','network_dae'])
def test_inverse_event_and_equipment_permutation(method,reports):
    s=example('very_inverse',reverse=True);s['coupled']['integration_method']=method
    actual=run_study(StudySpec.model_validate(s));expected=reports['very_inverse',method]
    assert actual['completed']
    for k in ('event_log','time_s','sample_side','channels'):assert actual[k]==expected[k]


@pytest.mark.parametrize('feature',['ATS','two_shots','close_failure','directional','CT_command'])
def test_outside_combined_scope_rejected(feature):
    s=example();a=s['coupled']['network_automation']
    if feature=='ATS':
        # Existing validated transfer shape, deliberately outside this reference.
        a['transfers']=[dict(id='ats',primary_breaker_id='primary-cb',reserve_breaker_id='backup-cb',primary_voltage=dict(kind='line',id='feeder'),reserve_voltage=dict(kind='source',id='source'))]
    elif feature=='two_shots':a['breakers'][0]['maximum_reclose_shots']=2
    elif feature=='close_failure':a['breakers'][0]['fail_close']=True
    elif feature=='directional':next(r for r in a['relays'] if r['id']=='primary-oc')['directional']=True
    else:s['coupled']['events'].append(dict(time_s=.09,kind='relay_measurement_lost',equipment_id='primary-oc'))
    with pytest.raises(ValueError):ProtectedTopologyReference(s)
