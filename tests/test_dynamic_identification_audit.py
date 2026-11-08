import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from benchmarks.noisy_identification_acceptance import specimen
from power_grid_env.dynamic_identification_audit import DynamicIdentificationAudit, ResidualLimits, assess_metrics, audit_identification, residual_metrics, run_audit_artifact, training_document
from power_grid_env.motor_catalog import catalog_entry
from power_grid_env.study_artifacts import encoded, read_json, verify


def request(defect=None, window=0):
    protocol = specimen(defect=defect, window=window)
    protocol['identification']['initial'] = catalog_entry('aalto-linear-fan')['motor'].model_dump(mode='json')
    protocol['identification']['initial']['stator_r_ohm'] *= 1.05
    protocol['identification']['electrical_parameters'] = ['stator_r_ohm']
    protocol['identification']['mechanical_load'] = 'fixed'
    return protocol


@pytest.mark.parametrize('values,accepted', [([0.] * 100, True), ([.8] * 100, False),
    ([2., -2.] * 50, False), ([-.8] * 50 + [.8] * 50, False), ([0.] * 99 + [6.], False)])
def test_mean_rms_drift_and_outlier_are_independent_rejection_criteria(values, accepted):
    assert assess_metrics(residual_metrics(values), ResidualLimits(), 5.) == accepted


@pytest.mark.parametrize('values', [[1., 2.], [[0.] * 5], [0., 1., 2., float('nan')]])
def test_invalid_residuals_fail(values):
    with pytest.raises(ValueError): residual_metrics(values)


def test_large_finite_rms_does_not_overflow_and_impossible_drift_fails():
    assert residual_metrics([1e200] * 8)['rms_sigma'] == pytest.approx(1e200)
    with pytest.raises(ValueError): residual_metrics([-1e308] * 4 + [1e308] * 4)


def test_filter_changes_only_training_speed_and_preserves_original_input():
    document = DynamicIdentificationAudit(**request(window=9))
    before = encoded(document.model_dump(mode='json'))
    prepared = training_document(document)
    assert encoded([t.model_dump(mode='json') for t in prepared.validation]) == encoded([t.model_dump(mode='json') for t in document.identification.validation])
    assert prepared.fitting[0].speed_rad_s != document.identification.fitting[0].speed_rad_s
    assert prepared.fitting[0].speed_rad_s[0] == document.identification.fitting[0].speed_rad_s[0]
    assert prepared.fitting[0].current_alpha_a == document.identification.fitting[0].current_alpha_a
    assert encoded(document.model_dump(mode='json')) == before


def test_filter_rejects_nonuniform_clock_without_resampling():
    document = request(window=9)
    document['identification']['fitting'][0]['time_s'][10] += .000001
    with pytest.raises(ValueError, match='uniform clock'):
        training_document(DynamicIdentificationAudit(**document))


@pytest.mark.parametrize('field,value', [('training_speed_filter_window', 8), ('unknown', True), ('residual_limits', {'maximum_mean_sigma': 0})])
def test_invalid_policy_is_rejected(field, value):
    document = request(); document[field] = value
    with pytest.raises(ValueError): DynamicIdentificationAudit(**document)


@pytest.fixture(scope='module')
def baseline():
    document = request()
    return document, audit_identification(document)


def test_noisy_frozen_trace_identifies_and_passes_raw_replay(baseline):
    document, result = baseline
    assert result['completed'] and result['passed'] and result['raw_residuals_passed'], result
    assert result['passport']['stator_r_ohm'] == pytest.approx(catalog_entry('aalto-linear-fan')['motor'].stator_r_ohm, rel=.002)
    assert all(row['passed'] for row in result['assessments'])
    assert not result['sensor_bias_estimated'] and not result['statistical_confidence_claimed']


def test_small_holdout_bias_can_pass_old_peak_rule_but_is_rejected_by_raw_mean(tmp_path):
    result = run_audit_artifact(request('heldout_current_bias'), tmp_path / 'audit')
    assert result['completed'] and result['fit_passed'] and not result['passed'], result
    assert result['reason'] == 'raw_residual_quality_rejected'
    assert not (tmp_path / 'audit/passport.json').exists()
    verify(tmp_path / 'audit')


def test_holdout_noise_and_bias_do_not_change_parameter_estimate(baseline):
    document, first = baseline
    second = audit_identification(request('heldout_current_bias'))
    assert second['passport']['stator_r_ohm'] == first['passport']['stator_r_ohm']
    assert second['electrical'] == first['electrical']
    assert second['audit_protocol_sha256'] != first['audit_protocol_sha256']


def test_filtered_fit_is_still_assessed_against_raw_observations(tmp_path):
    document = request(window=9)
    result = run_audit_artifact(document, tmp_path / 'audit')
    assert result['passed'], result
    raw = document['identification']['fitting'][0]
    response = result['trajectories'][raw['id']]['speed_rad_s']
    measured = np.asarray(raw['speed_rad_s'])
    error = (np.asarray(response) - measured) / raw['speed_sigma_rad_s']
    actual = result['assessments'][0]['residual_metrics']['speed']
    assert actual == residual_metrics(error)
    assert read_json(tmp_path / 'audit/fit-input.json')['fitting'][0]['speed_rad_s'] != raw['speed_rad_s']
    assert (tmp_path / 'audit/passport.json').exists()


def test_rhs_budget_includes_extra_raw_replays():
    document = request(); document['identification']['maximum_rhs_evaluations'] = 1
    result = audit_identification(document)
    assert not result['completed'] and not result['passed'] and result['rhs_evaluations'] <= 1


def test_cli_seals_rejected_audit_and_does_not_publish_passport(tmp_path):
    (tmp_path / 'input.json').write_bytes(encoded(request('heldout_current_bias')))
    completed = subprocess.run([sys.executable, '-m', 'power_grid_env.cli', 'audit-dynamic-motor', '--input', str(tmp_path / 'input.json'), '--out', str(tmp_path / 'audit')], capture_output=True, text=True)
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert not (tmp_path / 'audit/passport.json').exists()
    verify(tmp_path / 'audit')


def test_noise_streams_are_reproducible_and_holdout_is_separate():
    first = specimen(); second = specimen()
    assert encoded(first) == encoded(second)
    changed = specimen(seed=17002)
    assert first['identification']['validation'][0]['current_alpha_a'] != changed['identification']['validation'][0]['current_alpha_a']
    assert first['identification']['initial'] == changed['identification']['initial']


def test_campaign_rejects_bias_and_preserves_audit_artifact(tmp_path):
    from power_grid_env.campaign import CampaignCase, CampaignPlan, run_campaign, verify_campaign
    case = CampaignCase(id='bias', kind='dynamic_identification_audit', study=request('heldout_current_bias'), worker_wall_s=60.)
    result = run_campaign(CampaignPlan(id='audit', cases=[case]).model_dump(mode='json'), tmp_path / 'campaign')
    assert result['completed'] and not result['passed']
    assert not (tmp_path / 'campaign/attempts/bias/passport.json').exists()
    verify_campaign(tmp_path / 'campaign')


@pytest.mark.parametrize('field', ['fit_passed', 'raw_residuals_passed'])
def test_campaign_cannot_approve_contradictory_success_flags(field):
    from power_grid_env.campaign import CampaignCase, assessment
    case = CampaignCase(id='audit', kind='dynamic_identification_audit', study=request())
    result = dict(completed=True, passed=True, identifiable=True, fit_passed=True, raw_residuals_passed=True)
    result[field] = False
    assert not assessment(case, result)['passed']


def test_audit_schema_is_public(tmp_path):
    from power_grid_env.schema_export import export_schemas
    export_schemas(tmp_path)
    schema = read_json(tmp_path / 'dynamic-identification-audit.schema.json')
    assert schema['additionalProperties'] is False
    assert 'identification' in schema['properties']
