from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from benchmarks.calibrated_motor_acceptance import fixture
from power_grid_env.calibrated_motor_study import CalibratedMotorStudy, run_calibrated_study
from power_grid_env.motor_catalog import catalog_entry
from power_grid_env.motor_recordings import Channel, Recording, convert_recording
from power_grid_env.study_artifacts import encoded, read_json, verify, export_zip, extract_zip


def quick_fixture(root, defect=None):
    path = fixture(root, defect=defect)
    protocol = read_json(root / 'protocol.json')
    protocol['identification']['initial'] = catalog_entry('aalto-linear-fan')['motor'].model_dump(mode='json')
    protocol['identification']['electrical_parameters'] = ['stator_r_ohm']
    protocol['identification']['mechanical_load'] = 'fixed'
    (root / 'protocol.json').write_bytes(encoded(protocol))
    return path


def test_known_calibration_noise_and_portable_approved_passport(tmp_path):
    path = quick_fixture(tmp_path / 'input')
    result = run_calibrated_study(path, tmp_path / 'run')
    assert result['completed'] and result['passed'], result
    assert not result['calibration_authenticated'] and not result['sensor_scales_estimated']
    passport = read_json(tmp_path / 'run/passport.json')
    assert passport['stator_r_ohm'] == pytest.approx(3.7, rel=.002)
    assert 'measurement_contract' in passport['provenance']
    verify(tmp_path / 'run'); export_zip(tmp_path / 'run', tmp_path / 'run.zip')
    extract_zip(tmp_path / 'run.zip', tmp_path / 'restored'); verify(tmp_path / 'restored')
    assert read_json(tmp_path / 'restored/passport.json') == passport


def test_wrong_holdout_voltage_gain_rejected_without_root_or_nested_passport(tmp_path):
    path = quick_fixture(tmp_path / 'input', 'wrong_holdout_voltage_gain')
    result = run_calibrated_study(path, tmp_path / 'run')
    assert result['completed'] and not result['passed'], result
    assert not (tmp_path / 'run/passport.json').exists()
    assert not (tmp_path / 'run/identification/passport.json').exists()
    verify(tmp_path / 'run')


@pytest.mark.parametrize('defect', ['unknown_joint_scales', 'unknown_flux'])
def test_unidentifiable_or_unknown_initial_state_rejected_before_solver(tmp_path, defect):
    path = fixture(tmp_path / 'input', defect=defect)
    with pytest.raises(ValueError): run_calibrated_study(path, tmp_path / 'run')
    assert not (tmp_path / 'run').exists()


def test_measured_origin_cannot_use_synthetic_calibration_references(tmp_path):
    path = fixture(tmp_path / 'input')
    config = read_json(path.parent / 'protocol.json')
    for recording in config['recordings']: recording['origin'] = 'measured'
    (path.parent / 'protocol.json').write_bytes(encoded(config))
    with pytest.raises(ValueError, match='external references'): run_calibrated_study(path, tmp_path / 'run')
    assert not (tmp_path / 'run').exists()


@pytest.mark.parametrize('defect', ['empty_evidence', 'missing_current_reference', 'absolute_source', 'escape_source', 'extra'])
def test_invalid_or_ambiguous_contract_rejected(tmp_path, defect):
    path = fixture(tmp_path / 'input'); request = read_json(path)
    if defect == 'empty_evidence': request['voltage_reference']['evidence'] = ' '
    elif defect == 'missing_current_reference': del request['current_reference']
    elif defect == 'absolute_source': request['recording_protocol'] = '/tmp/source.json'
    elif defect == 'escape_source': request['recording_protocol'] = '../source.json'
    else: request['estimate_voltage_gain'] = True
    path.write_bytes(encoded(request))
    with pytest.raises(ValueError): run_calibrated_study(path, tmp_path / 'run')
    assert not (tmp_path / 'run').exists()


@pytest.mark.parametrize('rails', [dict(raw_minimum=0.), dict(raw_maximum=1.), dict(raw_minimum=1., raw_maximum=1.), dict(raw_minimum=2., raw_maximum=1.), dict(raw_minimum=float('-inf'), raw_maximum=1.)])
def test_invalid_instrument_rails_rejected(rails):
    with pytest.raises(ValueError): Channel(column='x', **rails)


@pytest.mark.parametrize('value', [-1., 1., 2.])
def test_instrument_rail_contact_and_overflow_are_not_silently_calibrated(value):
    from tests.test_motor_recordings import fixture as data_fixture, raw
    config, columns = data_fixture('alpha_beta')
    config['channels']['ua'].update(raw_minimum=-1., raw_maximum=1.)
    columns['ua'][3] = value
    with pytest.raises(ValueError, match='instrument rail'): convert_recording(raw(columns), Recording(**config))


def test_voltage_current_scale_gauge_preserves_speed_with_different_physical_passport():
    from power_grid_env.dynamic_motor_identification import DynamicMotorIdentification, WorkBudget, replay, _currents
    document = read_json(Path(__file__).resolve().parents[1] / 'cases/dynamic-identification/saturated.json')
    spec = DynamicMotorIdentification.model_validate(document)
    p = catalog_entry('aalto-saturated-fan')['motor']; trace = spec.fitting[0]
    a, b = 1.2, .8
    q = p.model_copy(deep=True)
    q.stator_r_ohm *= a / b; q.unsaturated_h *= a / b; q.saturation_beta_per_vs /= a
    q.cages[0].resistance_ohm *= a / b; q.cages[0].leakage_h *= a / b
    q.inertia_kg_m2 *= a * b; q.fan_nm_per_rad_s2 *= a * b
    first_i, first_ir, first_t = _currents(p, .4 + .2j, .3 + .1j)
    other_i, other_ir, other_t = _currents(q, a * (.4 + .2j), a * (.3 + .1j))
    assert other_i == pytest.approx(b * first_i, rel=1e-13)
    assert other_ir == pytest.approx(b * first_ir, rel=1e-13)
    assert other_t == pytest.approx(a * b * first_t, rel=1e-13)
    scaled = trace.model_copy(deep=True)
    scaled.voltage_alpha_v = (np.array(trace.voltage_alpha_v) * a).tolist()
    scaled.voltage_beta_v = (np.array(trace.voltage_beta_v) * a).tolist()
    first = replay(p, trace, WorkBudget(spec)); second = replay(q, scaled, WorkBudget(spec))
    assert q.stator_r_ohm != p.stator_r_ohm
    assert second['current'] == pytest.approx(first['current'] * b, rel=1e-7, abs=1e-7)
    assert second['speed'] == pytest.approx(first['speed'], rel=1e-7, abs=1e-7)
    assert second['torque'] == pytest.approx(first['torque'] * a * b, rel=1e-7, abs=1e-6)


def test_cli_rejects_unknown_joint_calibration_before_publishing(tmp_path):
    path = fixture(tmp_path / 'input', defect='unknown_joint_scales')
    process = subprocess.run([sys.executable, '-m', 'power_grid_env.cli', 'calibrated-motor', '--input', str(path), '--out', str(tmp_path / 'run')], capture_output=True, text=True)
    assert process.returncode == 2 and not (tmp_path / 'run').exists()


def test_public_schema_and_escaped_calibration_evidence(tmp_path):
    from power_grid_env.schema_export import export_schemas
    path = quick_fixture(tmp_path / 'input'); request = read_json(path)
    request['voltage_reference']['evidence'] = '<script>alert(1)</script>'
    path.write_bytes(encoded(request)); run_calibrated_study(path, tmp_path / 'run')
    html = (tmp_path / 'run/report.html').read_text()
    assert '<script>alert' not in html and '&lt;script&gt;' in html
    export_schemas(tmp_path / 'schemas')
    assert (tmp_path / 'schemas/calibrated-motor-study.schema.json').exists()
