"""Independent hybrid physics, action timing, negative and isolation checks."""
import copy
from pathlib import Path

import pytest

from benchmarks.loaded_protection_winding_reference import ProtectionReference
from benchmarks.loaded_protection_winding_acceptance import compare
from power_grid_env.study_artifacts import read_json
from power_grid_env.study_spec import StudySpec, run_study

ROOT = Path(__file__).resolve().parents[1]


def example(kind='primary-uv'):
    return read_json(ROOT/'cases/motor-only-loaded/protected'/f'{kind}.json')


def native(study, method):
    study = copy.deepcopy(study)
    study['coupled']['integration_method'] = method
    return run_study(StudySpec.model_validate(study))


@pytest.mark.parametrize('method', ['radau', 'network_dae'])
@pytest.mark.parametrize('curve', ['definite', 'standard_inverse', 'very_inverse', 'extremely_inverse'])
@pytest.mark.parametrize('kind', ['primary-uv', 'breaker_failure-uv', 'measurement_loss-uv'])
def test_independent_curve_clock_and_breaker_motor_actions(curve, method, kind):
    study = example(kind)
    for relay in study['coupled']['network_automation']['relays']:
        relay['curve'] = curve
    reference = ProtectionReference(study).run()
    checked = compare(reference, native(study, method))
    assert checked['passed'], {k: v for k, v in checked.items() if k != 'values'}
    assert any(e['kind'] == 'network_relay_trip' for e in reference.events)
    assert reference.sample(.6)['m.connected'] == 0


@pytest.mark.parametrize('kind', ['breaker_failure-uv', 'measurement_loss-uv', 'primary-no-uv'])
def test_failure_measurement_loss_and_negative_control(kind):
    study = example(kind)
    reference = ProtectionReference(study).run()
    assert compare(reference, native(study, 'network_dae'))['passed']
    assert reference.sample(.6)['m.connected'] == float(kind == 'primary-no-uv')
    if kind == 'breaker_failure-uv':
        assert any(e['kind'] == 'breaker_failure_confirmed' for e in reference.events)
    if kind == 'measurement_loss-uv':
        assert not any(e['kind'] == 'network_relay_trip' and e['equipment_id'] == 'primary-oc' for e in reference.events)


def test_independent_exponential_reset_and_measurement_restoration():
    study = example()
    c = study['coupled']
    c['faults'][0]['clear_s'] = .105
    c['faults'].append(dict(c['faults'][0], id='second-fault', apply_s=.2, clear_s=.4))
    c['events'] = [dict(time_s=.102, kind='relay_measurement_lost', equipment_id='primary-oc'),
                   dict(time_s=.15, kind='relay_measurement_restored', equipment_id='primary-oc')]
    reference = ProtectionReference(study).run()
    assert compare(reference, native(study, 'network_dae'))['passed']
    assert reference.sample(.14)['primary-oc.progress'] < reference.sample(.102, 'post_event')['primary-oc.progress']
    assert any(e['kind'] == 'relay_measurement_restored' for e in reference.events)
    assert any(e['kind'] == 'network_relay_trip' for e in reference.events)


@pytest.mark.parametrize('method', ['radau', 'network_dae'])
def test_independent_thermal_trip(method):
    study = example()
    study['coupled']['operating_point']['thermal_state'] = 'passport'
    study['coupled']['motors'][0]['thermal'].update(initial_level=0., time_constant_s=.5, trip_level=.2)
    reference = ProtectionReference(study).run()
    assert any(e['kind'] == 'thermal_trip' for e in reference.events)
    checked = compare(reference, native(study, method))
    assert checked['passed'], {k: v for k, v in checked.items() if k != 'values'}
    assert reference.sample(.6)['m.thermal_latched'] == 1


def test_reference_does_not_call_native_physics_clocks_or_event_engine(monkeypatch):
    from power_grid_env.coupled_rms import CoupledNetwork
    from power_grid_env.network_automation import NetworkAutomationBank
    from power_grid_env.motor_contactor import MotorContactorBank
    import power_grid_env.coupled_initialization as initialization
    import power_grid_env.network_automation as automation
    study = example('breaker_failure-uv')
    report = native(study, 'network_dae')

    def forbidden(*a, **k):
        raise AssertionError('native implementation used to advance independent reference')

    for target, names in [(CoupledNetwork, ['static_electrical', 'physical_rhs']),
                          (NetworkAutomationBank, ['apply', 'service', 'derivatives', 'refresh']),
                          (MotorContactorBank, ['observe', 'actions'])]:
        for name in names:
            monkeypatch.setattr(target, name, forbidden)
    monkeypatch.setattr(initialization, 'initialize_coupled', forbidden)
    monkeypatch.setattr(automation, 'iec_operate_time', forbidden)
    reference = ProtectionReference(study).run()
    assert compare(reference, report)['passed']
    damaged = copy.deepcopy(report)
    damaged['channels']['backup-oc.progress'][300] += .01
    assert not compare(reference, damaged)['passed']
    damaged = copy.deepcopy(report)
    event = next(e for e in damaged['event_log'] if e['kind'] == 'breaker_open')
    event['time_s'] += .001
    assert not compare(reference, damaged)['passed']
    damaged = copy.deepcopy(report)
    damaged['channels']['m.speed_pu'][0] = float('nan')
    with pytest.raises(ValueError, match='nonfinite'):
        compare(reference, damaged)
    damaged = copy.deepcopy(report)
    damaged['event_log'][0]['time_s'] = float('nan')
    with pytest.raises(ValueError, match='event time'):
        compare(reference, damaged)


def test_order_invariance_one_sided_island_samples_and_refinement():
    study = example('breaker_failure-uv')
    first = ProtectionReference(study).run(max_step=.002)
    study['coupled']['network_automation']['relays'].reverse()
    study['coupled']['network_automation']['breakers'].reverse()
    second = ProtectionReference(study).run(max_step=.0005)
    assert [(e['kind'], e['equipment_id']) for e in first.events] == [(e['kind'], e['equipment_id']) for e in second.events]
    assert max(abs(a['time_s']-b['time_s']) for a, b in zip(first.events, second.events)) < 1e-8
    event = next(e for e in first.events if e['kind'] == 'motor_open')
    assert first.sample(event['time_s'], 'pre_event')['m.connected'] == 1
    assert first.sample(event['time_s'], 'post_event')['m.connected'] == 0
    assert first.sample(.3)['primary-cb.closed'] == 1
    assert first.sample(.3)['backup-cb.closed'] == 0


@pytest.mark.parametrize('feature', ['reclose', 'directional', 'restart', 'ATS-event'])
def test_unsupported_physics_rejected(feature):
    study = example()
    c = study['coupled']
    if feature == 'reclose':
        c['network_automation']['breakers'][0]['maximum_reclose_shots'] = 1
    elif feature == 'directional':
        c['network_automation']['relays'][0]['directional'] = True
    elif feature == 'restart':
        c['motors'][0]['contactor']['maximum_automatic_restarts'] = 1
    else:
        c['events'] = [dict(time_s=.2, kind='breaker_open', equipment_id='primary-cb')]
    with pytest.raises(ValueError):
        ProtectionReference(study)
