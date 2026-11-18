import copy
import numpy as np
import pytest
from benchmarks.measurement_transfer_cases import example,CT_CASES,ATS_CASES
from benchmarks.measurement_transfer_reference import MeasurementTransferReference
from benchmarks.measurement_transfer_acceptance import compare,coverage
from benchmarks.measurement_transfer_verify import physical_gate,clock_gate
from power_grid_env.study_spec import StudySpec,run_study


@pytest.fixture(scope='module')
def refs():return {k:MeasurementTransferReference(example(k,'passport')).run() for k in CT_CASES+ATS_CASES}


@pytest.fixture(scope='module')
def reports():
    out={}
    for k in CT_CASES+ATS_CASES:
        for method in ('radau','network_dae'):
            s=example(k,'passport');s['coupled']['integration_method']=method
            out[k,method]=run_study(StudySpec.model_validate(s))
    return out


@pytest.mark.parametrize('scenario',CT_CASES+ATS_CASES)
@pytest.mark.parametrize('method',['radau','network_dae'])
def test_measured_relay_and_standby_transfer_match_independent_equations(scenario,method,refs,reports):
    r=reports[scenario,method]
    assert r['completed'],r
    assert r['additional_validation']['ok'] and compare(refs[scenario],r)['passed']
    assert coverage(r['event_log'],scenario)==coverage(refs[scenario].events,scenario)


@pytest.mark.parametrize('scenario',CT_CASES+ATS_CASES)
def test_physical_continuity_memory_and_clock_integral(scenario,refs):
    ref=refs[scenario];physical_gate(ref.sample,ref.events,scenario)
    count,error=clock_gate(ref)
    assert count>0 and error<2e-8


@pytest.mark.parametrize('scenario',['ct_interrupt','pt_interrupt'])
def test_invalid_measurement_is_not_a_physical_zero_and_reset_is_continuous(scenario,refs):
    ref=refs[scenario];loss=next(e['time_s'] for e in ref.events if e['kind']=='relay_measurement_lost')
    pre,post=[ref.sample(loss,s) for s in ('pre_event','post_event')]
    assert pre['primary-oc.progress']==post['primary-oc.progress']>0
    assert post['primary-oc.active']==0 and post['primary-oc.measurement_valid']==0
    assert post['primary-oc.measurement']==0 and post['m.bus_voltage_pu']>.5
    q=ref.sample(loss+.001)
    assert q['primary-oc.progress']==pytest.approx(post['primary-oc.progress']*np.exp(-.001/.05),abs=2e-10)


def test_reclose_with_missing_CT_does_not_fabricate_pickup(refs):
    ref=refs['ct_reclose'];close=next(e['time_s'] for e in ref.events if e['kind']=='breaker_close')
    q=ref.sample(close,'post_event')
    assert q['primary-cb.closed']==1 and q['primary-oc.progress']==0 and q['primary-oc.latched']==0
    assert q['primary-oc.measurement_valid']==0 and q['primary-oc.active']==0
    assert not any(e['kind']=='network_relay_trip' and close<e['time_s']<.13 for e in ref.events)


@pytest.mark.parametrize('scenario',ATS_CASES)
def test_standby_has_zero_terminal_current_and_no_false_thermal_latch(scenario,refs):
    ref=refs[scenario];q=ref.sample(0.)
    assert q['g.connected']==0 and q['g.current_pu']==0 and q['g.heat.copper_loss_w']==0
    assert q['g.stator_thermal_latched']==0 and q['g.mechanical_power_pu']>0
    assert ref.sample(.04)['g.stator_thermal_level']==pytest.approx(.2*np.exp(-.04/.05),abs=1e-10)


def test_short_dip_resets_detection_without_transfer(refs):
    ref=refs['ats_recovered_dip'];q=ref.sample(.055,'post_event')
    assert q['ats.active']==0 and q['ats.latched']==0
    assert q['ats.progress']==pytest.approx(.5,abs=1e-10)
    assert ref.sample(.065)['ats.progress']==pytest.approx(.5/np.e,abs=1e-10)
    assert not any(e['kind'].startswith('breaker_') for e in ref.events)


@pytest.mark.parametrize('mutation',['availability','latch','reserve_before_primary','reason','clock','physical_zero'])
def test_mutated_measurement_and_transfer_evidence_rejected(mutation,refs,reports):
    kind='ats_transfer' if mutation in ('latch','reserve_before_primary') else 'ats_primary_failure' if mutation=='reason' else 'ct_interrupt'
    report=copy.deepcopy(reports[kind,'radau'])
    if mutation=='availability':next(e for e in report['event_log'] if e['kind']=='relay_measurement_lost')['measurement_available']=True
    elif mutation=='latch':report['channels']['g.stator_thermal_latched'][0]=1.
    elif mutation=='reserve_before_primary':next(e for e in report['event_log'] if e['kind']=='breaker_close')['time_s']=.06
    elif mutation=='reason':next(e for e in report['event_log'] if e['kind']=='transfer_blocked')['reason']='reserve_unavailable'
    elif mutation=='clock':report['channels']['primary-oc.progress'][-1]+=.01
    else:report['channels']['m.bus_voltage_pu'][0]=0.
    assert not compare(refs[kind],report)['passed']


def test_reference_forbids_native_automation_stator_and_initialization_helpers(monkeypatch):
    import power_grid_env.network_automation as auto
    import power_grid_env.generator_stator as stator
    import power_grid_env.coupled_initialization as init
    def forbidden(*args,**kwargs):raise AssertionError('native helper used')
    for key in ('service','guards','request','refresh','derivatives','check_close'):
        monkeypatch.setattr(auto.NetworkAutomationBank,key,forbidden)
    monkeypatch.setattr(stator,'impedance',forbidden);monkeypatch.setattr(init,'initialize_coupled',forbidden)
    for k in ('ct_interrupt','ats_transfer','ats_recovered_dip'):
        ref=MeasurementTransferReference(example(k,'passport')).run();assert coverage(ref.events,k)


@pytest.mark.parametrize('scenario,mode',[('ct_interrupt','passport'),('ats_transfer','hot')])
@pytest.mark.parametrize('method',['radau','network_dae'])
def test_command_and_equipment_permutation(scenario,mode,method):
    reports=[]
    for reverse in (False,True):
        s=example(scenario,mode,reverse);s['coupled']['integration_method']=method
        reports.append(run_study(StudySpec.model_validate(s)))
    assert all(r['completed'] for r in reports)
    for k in ('event_log','time_s','sample_side','channels'):assert reports[0][k]==reports[1][k]


@pytest.mark.parametrize('feature',['joint_standby','reserve_closed','P_nonzero'])
def test_unsupported_standby_profile_rejected(feature):
    s=example('ats_transfer','passport')
    if feature=='joint_standby':s['coupled']['operating_point']['winding_state']='equilibrium'
    elif feature=='reserve_closed':next(b for b in s['coupled']['network_automation']['breakers'] if b['id']=='reserve-cb')['initial_open']=False
    else:s['network']['generators'][0]['p_w']=1000.
    with pytest.raises(ValueError):MeasurementTransferReference(s)
