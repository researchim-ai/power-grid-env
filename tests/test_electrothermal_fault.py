"""Warm winding initialization, electrothermal identities and independent faults."""
import copy

import numpy as np
import pytest

from benchmarks.electrothermal_fault_cases import jobs
from benchmarks.electrothermal_fault_reference import run_reference
from benchmarks.electrothermal_fault_acceptance import comparison, thermal_audit
from power_grid_env.phase_network import MotorPort, PhaseCase, PhaseNetwork
from power_grid_env.phase_research import PhaseStudy, evaluate
from power_grid_env.phase_core_motor import PhaseCoreMotor
from power_grid_env.vf_drive import PhaseMotor


def example():
    return next(j['study'] for j in jobs() if j['id'] == 'c1-hot_rotating-ll-feedback')


@pytest.mark.parametrize('core', [False, True])
def test_hot_stator_and_rotor_initialize_independently_with_correct_resistances(core):
    d = example()
    m = d['case']['motors'][0]
    if not core:
        m['passport']['core_loss_s'] = 0.
    port = MotorPort.model_validate(m)
    machine = (PhaseCoreMotor if core else PhaseMotor)(port)
    x = machine.initial()
    assert list(x[-2:]) == [90., 70.]
    rate, info = machine.rates(0., 200.+30j, x)
    h = port.passport.thermal
    assert info['stator_resistance_ohm'] == pytest.approx(port.passport.stator_r_ohm*(1+.00393*70), abs=1e-14)
    assert info['rotor1_resistance_ohm'] == pytest.approx(port.passport.cages[0].resistance_ohm*(1+.00393*50), abs=1e-14)
    assert info['stator_net_heat_w'] == pytest.approx(rate[-2]*h.stator_capacity_j_per_k)
    assert info['rotor_net_heat_w'] == pytest.approx(rate[-1]*h.rotor_capacity_j_per_k)
    step = 1e-8
    plus = machine.rates(0., 200.+30j, x+step*rate)[1]['stored_j']
    minus = machine.rates(0., 200.+30j, x-step*rate)[1]['stored_j']
    assert (plus-minus)/(2*step) == pytest.approx(info['motor_power_w']-info['loss_w']-info['mechanical_power_w'], rel=3e-6, abs=.01)


@pytest.mark.parametrize('kind', ['no_thermal', 'no_provenance', 'empty_provenance', 'maximum', 'negative_resistance', 'absolute_zero', 'extra_temperature', 'nan', 'orphan_provenance'])
def test_invalid_hot_initial_state_rejected_before_integration(kind):
    d = example()['case']['motors'][0]
    if kind == 'no_thermal':
        d['passport']['thermal'] = None
    elif kind == 'no_provenance':
        d.pop('initial_temperature_provenance')
    elif kind == 'empty_provenance':
        d['initial_temperature_provenance'] = '  '
    elif kind == 'maximum':
        d['initial_temperature_c'][0] = 180.
    elif kind == 'negative_resistance':
        d['initial_temperature_c'][0] = -250.
    elif kind == 'absolute_zero':
        d['initial_temperature_c'][0] = -274.
    elif kind == 'extra_temperature':
        d['initial_temperature_c'].append(30.)
    elif kind == 'nan':
        d['initial_temperature_c'][0] = float('nan')
    else:
        d.pop('initial_temperature_c')
    with pytest.raises(ValueError):
        MotorPort.model_validate(d)


def test_legacy_ambient_initialization_keeps_canonical_schema():
    m = example()['case']['motors'][0]
    m.pop('initial_temperature_c')
    m.pop('initial_temperature_provenance')
    p = MotorPort.model_validate(m)
    assert p.model_dump(mode='json') == m
    machine = PhaseCoreMotor(p)
    assert list(machine.initial()[-2:]) == [20., 20.]


@pytest.fixture(scope='module')
def fault():
    doc = example()
    result = evaluate(PhaseStudy.model_validate(doc))
    reference = run_reference(doc['case'], result['time_s'], sides=result['sample_side'])
    reference.update(time_s=result['time_s'], sample_side=result['sample_side'])
    return doc, result, reference


def test_hot_fault_reference_and_thermal_balances(fault):
    doc, result, reference = fault
    assert comparison(doc, result, reference)['passed']
    from power_grid_env.study_artifacts import encoded
    assert thermal_audit(doc, result)['passed']
    assert encoded(thermal_audit(doc, result))
    assert result['channels']['core.stator_temperature_c'][-1] != 90.
    assert result['channels']['core.stator_resistance_ohm'][-1] != result['channels']['core.stator_resistance_ohm'][0]
    for t in [.012, .024]:
        assert [(time, side) for time, side in zip(result['time_s'], result['sample_side']) if time == t] == [(t, 'pre_event'), (t, 'post_event')]


def test_separate_flux_reference_cannot_call_native_network_or_machine(fault, monkeypatch):
    from power_grid_env.flux_machine import GammaWinding
    doc, result, _ = fault

    def forbidden(*a, **k):
        raise AssertionError('native equations used in independent reference')

    for target, methods in [(PhaseNetwork, ['rhs', 'fields', 'initial']), (PhaseCoreMotor, ['rates', 'initial', 'terminal_relation']),
                            (GammaWinding, ['quantities', 'derivatives'])]:
        for method in methods:
            monkeypatch.setattr(target, method, forbidden)
    reference = run_reference(doc['case'], result['time_s'], sides=result['sample_side'])
    reference.update(time_s=result['time_s'], sample_side=result['sample_side'])
    assert comparison(doc, result, reference)['passed']
    bad = copy.deepcopy(result)
    bad['channels']['core.rotor_temperature_c'][-1] += 1.
    assert not comparison(doc, bad, reference)['passed']
    bad = copy.deepcopy(result)
    bad['channels']['core.stator_net_heat_w'][10] += 10.
    assert not thermal_audit(doc, bad)['passed']


def test_matched_fixed_resistance_changes_physics_and_retains_thermal_states(fault):
    doc, feedback, _ = fault
    fixed = copy.deepcopy(doc)
    fixed['case']['motors'][0]['passport']['thermal']['alpha_per_c'] = 0.
    result = evaluate(PhaseStudy.model_validate(fixed))
    assert max(result['channels']['core.stator_resistance_ohm']) == min(result['channels']['core.stator_resistance_ohm'])
    assert result['channels']['core.stator_temperature_c'][-1] != 90.
    delta = np.hypot(np.array(result['channels']['core.current_alpha_a'])-feedback['channels']['core.current_alpha_a'],
                     np.array(result['channels']['core.current_beta_a'])-feedback['channels']['core.current_beta_a'])
    assert max(delta) > .01
