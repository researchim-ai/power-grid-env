import copy
import numpy as np
import pytest

from benchmarks.standby_interlocks_cases import SCENARIOS, example
from benchmarks.standby_interlocks_reference import StandbyInterlocksReference
from benchmarks.standby_interlocks_benchmark import coverage, physical_gate, report_sample
from benchmarks.automatic_return_acceptance import compare
from power_grid_env.study_spec import StudySpec, run_study


@pytest.fixture(scope='module')
def references():
    return {name: StandbyInterlocksReference(example(name)).run() for name in SCENARIOS}


@pytest.fixture(scope='module')
def reports():
    rows = {}
    for name in SCENARIOS:
        for engine in ('radau', 'network_dae'):
            study = example(name)
            study['coupled']['integration_method'] = engine
            rows[name, engine] = run_study(StudySpec.model_validate(study))
    return rows


@pytest.mark.parametrize('scenario', SCENARIOS)
@pytest.mark.parametrize('engine', ['radau', 'network_dae'])
def test_initial_fault_and_thermal_reconnection_matches_independent_SI(scenario, engine, references, reports):
    r = reports[scenario, engine]
    assert r['completed'] and r['additional_validation']['ok']
    assert compare(references[scenario], r)['passed']
    assert coverage(r['event_log'], scenario) == coverage(references[scenario].events, scenario)
    physical_gate(report_sample(r), r['event_log'], scenario)
    physical_gate(references[scenario].sample, references[scenario].events, scenario)


@pytest.mark.parametrize('scenario', ['primary_restore', 'both_restore', 'primary_missing'])
def test_initial_missing_primary_prevents_false_loss_until_observable(scenario, references):
    ref = references[scenario]
    q = ref.sample(.058)
    assert q['ats.primary_measurement_valid'] == 0
    assert q['ats.primary_voltage_pu'] == 0 and q['ats.progress'] == 0
    assert q['source.connected'] == 1 and q['g.connected'] == 0


@pytest.mark.parametrize('scenario', ['thermal_manual', 'thermal_retry'])
def test_cooling_and_ATS_reset_do_not_clear_thermal_memory(scenario, references):
    ref = references[scenario]
    assert ref.sample(.3)['g.stator_thermal_level'] < .03
    assert ref.sample(.3)['g.stator_thermal_latched'] == 1
    blocked = [e for e in ref.events if e.get('synchronism', {}).get('reason') == 'generator_thermal_latch']
    assert len(blocked) == 2 and all(e['outcome'] == 'blocked' for e in blocked)
    assert ref.sample(.3)['g.connected'] == 0


@pytest.mark.parametrize('side', ['primary', 'reserve'])
def test_initial_mask_serialization_and_roundtrip(side):
    from benchmarks.automatic_return_cases import example as old
    data = old()
    owner = StudySpec.model_validate(data)
    original = owner.model_dump(mode='json')
    p = owner.coupled.network_automation.transfers[0]
    for key in ('initial_primary_measurement_available', 'initial_reserve_measurement_available'):
        assert key not in p.model_dump(mode='json')
    key = 'initial_'+side+'_measurement_available'
    setattr(p, key, False)
    encoded = owner.model_dump(mode='json')
    assert encoded['coupled']['network_automation']['transfers'][0][key] is False
    assert getattr(StudySpec.model_validate(encoded).coupled.network_automation.transfers[0], key) is False
    setattr(p, key, True)
    assert owner.model_dump(mode='json') == original


def test_initial_observation_fault_does_not_change_physical_initialization():
    from tests.test_two_cage_winding import initial
    from benchmarks.automatic_return_cases import example as old
    a, x, _ = initial(old())
    b, y, _ = initial(example('both_restore'))
    assert np.array_equal(x, y)
    assert np.array_equal(a.electrical(x)[0], b.electrical(y)[0])
    assert b.automation.transfer_available['ats'] == dict(primary=False, reserve=False)
    assert b.automation.measured_transfers == {'ats'}


def test_reference_does_not_call_native_current_or_protection_helpers(monkeypatch):
    from power_grid_env.network_automation import NetworkAutomationBank
    from power_grid_env.generator_thermal import GeneratorThermalBank
    def forbidden(*a, **kw):
        raise AssertionError('native helper called')
    for cls, names in [(NetworkAutomationBank, ('measurement', 'check_close', 'service', 'refresh')),
                       (GeneratorThermalBank, ('derivatives', 'apply', 'guards'))]:
        for name in names:
            monkeypatch.setattr(cls, name, forbidden)
    for scenario in ('both_restore', 'thermal_retry'):
        r = StandbyInterlocksReference(example(scenario)).run()
        coverage(r.events, scenario)


@pytest.mark.parametrize('mutation', ['validity', 'thermal_latch', 'thermal_level', 'reason', 'owner'])
def test_modified_interlock_evidence_rejected(mutation, references, reports):
    scenario = 'primary_missing' if mutation in ('validity', 'owner') else 'thermal_manual'
    report = copy.deepcopy(reports[scenario, 'radau'])
    if mutation == 'validity':
        report['channels']['ats.primary_measurement_valid'][0] = 1.
    elif mutation == 'thermal_latch':
        report['channels']['g.stator_thermal_latched'][-1] = 0.
    elif mutation == 'thermal_level':
        report['channels']['g.stator_thermal_level'][-1] += .1
    elif mutation == 'reason':
        next(e for e in report['event_log'] if e.get('synchronism', {}).get('reason') == 'generator_thermal_latch')['synchronism']['reason'] = 'synchronized'
    else:
        data = example(scenario)
        data['coupled']['integration_method'] = 'radau'
        assert StudySpec.model_validate(data).coupled.model_dump(mode='json') == report['settings']
        data['coupled']['network_automation']['transfers'][0]['initial_primary_measurement_available'] = True
        assert StudySpec.model_validate(data).coupled.model_dump(mode='json') != report['settings']
        return
    assert not compare(references[scenario], report)['passed']


def test_unavailable_initial_measurement_is_in_DAE_discrete_token():
    from tests.test_two_cage_winding import initial
    from power_grid_env.dae import mode_token
    m, x, _ = initial(example('both_restore'))
    old = mode_token(m)
    m.automation.transfer_available['ats']['primary'] = True
    assert mode_token(m) != old
