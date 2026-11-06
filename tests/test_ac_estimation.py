"""AC metering, derivatives, observability, leverage and bad-data recovery."""
from dataclasses import replace
import math
import numpy as np
import pytest

from power_grid_env.ac_estimation import Measurement, MeasurementModel, estimate_state
from power_grid_env.steady_study import run_steady_study
from power_grid_env.balanced_ac import case_document
from test_balanced_ac_workflows import exact_case, independent_injections


def meters(case, voltage):
    rows = []
    injections = independent_injections(case, voltage)
    for i, b in enumerate(case.buses):
        for repeat in range(2):
            rows.append(Measurement(f'v{b.id}-{repeat}', 'vm_pu', abs(voltage[i]), .001, bus_id=b.id))
            if i:
                rows.append(Measurement(f'a{b.id}-{repeat}', 'va_rad', np.angle(voltage[i]), .001, bus_id=b.id))
        rows.append(Measurement(f'p{b.id}', 'p_injection_mw', injections[i].real, .05, bus_id=b.id))
        rows.append(Measurement(f'q{b.id}', 'q_injection_mvar', injections[i].imag, .05, bus_id=b.id))
    index = {b.id: i for i, b in enumerate(case.buses)}
    for k, branch in enumerate(case.branches):
        i, j = index[branch.fbus], index[branch.tbus]
        tap = (branch.ratio or 1)*np.exp(1j*math.radians(branch.angle_deg))
        current = (voltage[i]/tap-voltage[j])/complex(branch.r, branch.x)
        f = voltage[i]*np.conj((current+1j*branch.b/2*voltage[i]/tap)/np.conj(tap))*case.base_mva
        t = voltage[j]*np.conj(-current+1j*branch.b/2*voltage[j])*case.base_mva
        for label, value in [('p_from_mw', f.real), ('q_from_mvar', f.imag), ('p_to_mw', t.real), ('q_to_mvar', t.imag)]:
            rows.append(Measurement(f'{label}-{k}', label, value, .05, branch_index=k))
    return rows


def test_analytical_jacobian_of_every_meter_matches_central_difference():
    case, voltage = exact_case(); model = MeasurementModel(case, meters(case, voltage))
    x = model.state(abs(voltage), np.angle(voltage)); _, analytic = model.evaluate(x)
    h = 1e-6; columns = []
    for i in range(len(x)):
        d = np.zeros_like(x); d[i] = h
        columns.append((model.evaluate(x+d)[0]-model.evaluate(x-d)[0])/(2*h))
    np.testing.assert_allclose(analytic, np.array(columns).T, atol=2e-7, rtol=2e-7)


def test_wls_recovers_independent_exact_phasors_and_full_covariance():
    case, voltage = exact_case(); result = estimate_state(case, meters(case, voltage))
    assert result['completed'] and result['observable'] and result['accepted'], result
    actual = np.array(result['vm_pu'])*np.exp(1j*np.radians(result['va_deg']))
    np.testing.assert_allclose(actual, voltage, atol=1e-10)
    covariance = np.array(result['covariance'])
    np.testing.assert_allclose(covariance, covariance.T, atol=1e-12)
    assert np.linalg.eigvalsh(covariance)[0] > 0
    assert result['rank'] == 5 and result['degrees_of_freedom'] > 0


def test_one_bad_flow_is_removed_without_removing_good_meters():
    case, voltage = exact_case(); rows = meters(case, voltage)
    bad = next(i for i, r in enumerate(rows) if r.id == 'p_from_mw-0')
    rows[bad] = replace(rows[bad], value=rows[bad].value+10.)
    raw = estimate_state(case, rows)
    assert not raw['accepted'] and not raw['bad_data_test_passed']
    fixed = estimate_state(case, rows, reject_bad_data=True)
    assert fixed['accepted'], fixed
    assert fixed['removed_measurement_ids'] == ['p_from_mw-0']
    np.testing.assert_allclose(fixed['vm_pu'], abs(voltage), atol=1e-9)
    limited = estimate_state(case, rows, reject_bad_data=True, max_removals=0)
    assert not limited['accepted'] and limited['removal_status'] == 'removal_budget_exhausted'


def test_missing_angles_is_unobservable_and_critical_meter_is_not_fake_clean():
    case, voltage = exact_case()
    only_v = [Measurement('v'+str(b.id), 'vm_pu', abs(voltage[i]), .001, bus_id=b.id) for i, b in enumerate(case.buses)]
    result = estimate_state(case, only_v)
    assert not result['completed'] and not result['observable'] and result['estimate'] is None
    direct = only_v + [Measurement('a'+str(b.id), 'va_rad', np.angle(voltage[i]), .001, bus_id=b.id) for i, b in enumerate(case.buses) if i]
    result = estimate_state(case, direct, reject_bad_data=True)
    assert result['completed'] and result['observable'] and not result['accepted']
    assert result['degrees_of_freedom'] == 0 and not result['bad_data_test_available']
    assert len(result['critical_measurement_ids']) == 5
    assert all(v is None for v in result['normalized_residuals'].values())


def test_reference_angle_is_relative_and_measurement_wrap_is_periodic():
    case, voltage = exact_case(); rows = meters(case, voltage)
    rows = [replace(r, value=r.value+2*np.pi) if r.kind == 'va_rad' else r for r in rows]
    result = estimate_state(case, rows, initial_theta=np.angle(voltage)+.4)
    assert result['accepted']
    np.testing.assert_allclose(np.radians(result['va_deg']), np.angle(voltage), atol=1e-10)


def test_failed_iteration_budget_and_offline_branch_jacobian():
    case, voltage = exact_case(); rows = meters(case, voltage)
    result = estimate_state(case, rows, max_evaluations=1)
    assert not result['completed'] and not result['accepted']
    offline = replace(case, branches=[replace(b, status=0) if i == 0 else b for i, b in enumerate(case.branches)])
    model = MeasurementModel(offline, [Measurement('off', 'p_from_mw', 0., .1, branch_index=0)])
    predicted, jac = model.evaluate(model.state(abs(voltage), np.angle(voltage)))
    assert predicted[0] == 0 and not np.any(jac)


@pytest.mark.parametrize('kind,sigma,bus,branch', [('vm_pu', 0., 10, None), ('vm_pu', float('nan'), 10, None),
    ('unknown', 1., 10, None), ('vm_pu', 1., 999, None), ('p_to_mw', 1., None, -1), ('vm_pu', 1., 10, 0)])
def test_invalid_meters_rejected(kind, sigma, bus, branch):
    case, _ = exact_case()
    with pytest.raises(ValueError): MeasurementModel(case, [Measurement('bad', kind, 1., sigma, bus, branch)])


def test_shared_study_accepts_ac_measurements_without_internal_module_edits(tmp_path):
    from dataclasses import asdict
    case, voltage = exact_case()
    doc = dict(schema='power-grid-env.steady-study/v1', id='ac-metering', task='state_estimation',
               case=case_document(case), measurements=[asdict(r) for r in meters(case, voltage)],
               options=dict(reject_bad_data=True))
    result = run_steady_study(doc, tmp_path/'study')
    assert result['accepted'] and result['completed']
