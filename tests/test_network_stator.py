import copy
import pytest

from benchmarks.network_stator_cases import example
from benchmarks.network_stator_reference import NetworkStatorReference
from benchmarks.network_stator_acceptance import compare, coverage
from benchmarks.network_stator_verify import physical_gate
from power_grid_env.study_spec import StudySpec, run_study


@pytest.fixture(scope='module')
def references():
    return {kind:NetworkStatorReference(example(kind)).run()
            for kind in ('relay_first','thermal_first','failed_open')}


@pytest.fixture(scope='module')
def reports():
    result={}
    for method in ('radau','network_dae'):
        for kind in ('relay_first','thermal_first','failed_open'):
            s=example(kind);s['coupled']['integration_method']=method
            result[method,kind]=run_study(StudySpec.model_validate(s))
    return result


@pytest.mark.parametrize('method',['radau','network_dae'])
@pytest.mark.parametrize('kind',['relay_first','thermal_first','failed_open'])
def test_network_oc_i2_race_matches_independent_si_reference(method,kind,references,reports):
    report=reports[method,kind]
    assert report['completed'] and report['additional_validation']['ok']
    assert compare(references[kind],report)['passed']
    actual=coverage(report['event_log'],kind);expected=coverage(references[kind].events,kind)
    assert actual['outcome']==expected['outcome']
    for k,v in expected.items():
        if not isinstance(v,str): assert actual[k]==pytest.approx(v,abs=2e-7)


@pytest.mark.parametrize('kind',['relay_first','thermal_first','failed_open'])
def test_opening_cause_clock_continuity_and_cooling(kind,references):
    ref=references[kind];effects=coverage(ref.events,kind);physical_gate(ref.sample,effects)
    pickup=next(e['time_s'] for e in ref.events if e['kind']=='network_relay_pickup')
    trip=next(e['time_s'] for e in ref.events if e['kind']=='network_relay_trip')
    assert ref.sample(pickup,'pre_event')['primary-oc.measurement']==pytest.approx(150,abs=1e-7)
    assert ref.sample(trip,'post_event')['primary-oc.progress']==1
    assert trip-pickup==pytest.approx(.005,abs=1e-10)


@pytest.mark.parametrize('mutation',['cause','breaker_id','deadline','attempts'])
def test_wrong_causality_and_attempt_counts_are_rejected(mutation,references,reports):
    report=copy.deepcopy(reports['radau','thermal_first'])
    if mutation=='cause':
        next(e for e in report['event_log'] if e['kind']=='breaker_open')['cause']='manual'
    elif mutation=='breaker_id':
        next(e for e in report['event_log'] if e['kind']=='network_relay_trip')['breaker_id']='backup-cb'
    elif mutation=='deadline':
        next(e for e in report['event_log'] if e['kind']=='breaker_open_requested')['due_s']+=.001
    else:
        report['channels']['primary-cb.open_attempts'][-1]=1.
    assert not compare(references['thermal_first'],report)['passed']


def test_reference_uses_no_native_automation_or_boundary_helpers(monkeypatch):
    from power_grid_env.network_automation import NetworkAutomationBank
    from power_grid_env.controller_boundaries import ControllerBoundaries
    from power_grid_env.generator_thermal import GeneratorThermalBank
    import power_grid_env.generator_stator as stator
    def forbidden(*a,**k): raise AssertionError('native helper called')
    for name in ('measurement','derivatives','guards','apply','service','refresh','request'):
        monkeypatch.setattr(NetworkAutomationBank,name,forbidden)
    for name in ('guards','apply','refresh','rates'):
        monkeypatch.setattr(ControllerBoundaries,name,forbidden)
    monkeypatch.setattr(GeneratorThermalBank,'apply',forbidden)
    monkeypatch.setattr(stator,'impedance',forbidden)
    ref=NetworkStatorReference(example('failed_open')).run()
    assert coverage(ref.events,'failed_open')['failure_confirmations']==1


@pytest.mark.parametrize('feature',['reclose','backup','directional','inverse','initial_active'])
def test_outside_reference_scope_is_explicitly_rejected(feature):
    s=example();a=s['coupled']['network_automation']
    b=next(b for b in a['breakers'] if b['id']=='primary-cb')
    r=next(r for r in a['relays'] if r['id']=='primary-oc')
    if feature=='reclose': b['maximum_reclose_shots']=1
    elif feature=='backup': b['backup_breaker_ids']=['backup-cb']
    elif feature=='directional': r['directional']=True
    elif feature=='inverse': r['curve']='very_inverse'
    else: r['pickup']=1.
    with pytest.raises(ValueError): NetworkStatorReference(s).run()


def test_historical_measurements_do_not_follow_final_open_state(references):
    ref=references['thermal_first'];before=ref.sample(.052)
    ref.sample(.5)
    assert before==ref.sample(.052)
    assert before['g.connected']==1 and before['primary-oc.active']==1
    assert before['primary-oc.measurement']>150
    assert ref.sample(.5)['primary-oc.measurement']==0


@pytest.mark.parametrize('method',['radau','network_dae'])
@pytest.mark.parametrize('step',[.001,.0005])
def test_definite_pickup_root_does_not_stall_on_refined_grid(method,step,references):
    s=example();s['coupled'].update(integration_method=method,step_s=step)
    report=run_study(StudySpec.model_validate(s))
    assert report['completed'],report
    assert compare(references['relay_first'],report)['passed']


def test_definite_clock_follows_hysteresis_and_availability(monkeypatch):
    from tests.test_two_cage_winding import initial
    m,x,_=initial(example());bank=m.automation;bank.initialize(x)
    i=next(i for i,r in enumerate(bank.relays) if r.id=='primary-oc');r=bank.relays[i]
    voltage=m.electrical(x)[0];bank.active[i]=True
    for measured in (r.pickup,r.pickup*(1+r.dropout_ratio)/2):
        monkeypatch.setattr(bank,'measurement',lambda *a:(measured,True))
        assert bank.derivatives(0,x,voltage)[i]==1/r.time_multiplier_s
    monkeypatch.setattr(bank,'measurement',lambda *a:(0.,False))
    assert bank.derivatives(0,x,voltage)[i]==0
    bank.active[i]=False;x[bank.offset+i]=.5
    assert bank.derivatives(0,x,voltage)[i]==-.5/r.reset_s
    bank.tripped[i]=True
    assert bank.derivatives(0,x,voltage)[i]==0


@pytest.mark.parametrize('method',['radau','network_dae'])
def test_event_relay_and_breaker_list_permutation(method,reports):
    s=example('thermal_first',reverse=True);s['coupled']['integration_method']=method
    reversed_report=run_study(StudySpec.model_validate(s));original=reports[method,'thermal_first']
    assert reversed_report['completed']
    for key in ('time_s','sample_side','event_log','channels'):
        assert reversed_report[key]==original[key]
