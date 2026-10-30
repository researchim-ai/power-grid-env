"""Frozen CSV contract rejects missing data, stale calculations and unit ambiguity."""
from pathlib import Path
import copy
import csv
import hashlib
import io
import json
import numpy as np
import pytest
from power_grid_env.motor_trace import MotorTraceProtocol, compare_motor_trace, _right_continuous_interpolate
from power_grid_env.study_spec import StudySpec, run_study

CSV = Path('cases/measurements/synthetic_motor_cold_trip.csv')
PROTOCOL = Path('cases/measurements/synthetic_motor_cold_trip.protocol.json')
STUDY = Path('cases/studies/thermal_cold_trip.json')


@pytest.fixture(scope='module')
def calculation():
    spec = StudySpec.model_validate_json(STUDY.read_text())
    result = run_study(spec)
    assert result['completed'], result
    return spec, result


def protocol():
    return MotorTraceProtocol.model_validate_json(PROTOCOL.read_text())


def rewrite(tmp_path, rows):
    p = tmp_path/'trace.csv'; output = io.StringIO(); writer = csv.writer(output)
    writer.writerows(rows); p.write_text(output.getvalue())
    cfg = protocol(); cfg.csv_sha256 = hashlib.sha256(p.read_bytes()).hexdigest()
    return p, cfg


def test_independent_trace_passes_without_releasing_field_qualification(calculation):
    spec, result = calculation
    compared = compare_motor_trace(spec, result, CSV, protocol())
    assert compared['passed'] and compared['completed']
    assert compared['samples'] >= 200
    assert all(row['passed'] for row in compared['metrics'].values())
    assert compared['protocol']['source_type'] == 'synthetic_reference'
    assert not compared['field_validated'] and not compared['industrial_profile_qualified']
    cfg = protocol(); cfg.source_type = 'field_measurement'
    declared_field = compare_motor_trace(spec, result, CSV, cfg)
    assert declared_field['passed'] and not declared_field['provenance_authenticated']
    assert not declared_field['field_validated']


def test_outside_fixed_tolerance_is_not_a_parse_failure(tmp_path, calculation):
    spec, result = calculation
    rows = list(csv.reader(CSV.read_text().splitlines()))
    for row in rows[1:]:
        row[1] = str(float(row[1])+1.)
    path, cfg = rewrite(tmp_path, rows)
    report = compare_motor_trace(spec, result, path, cfg)
    assert report['completed'] and not report['passed']
    assert report['metrics']['current_a']['maximum_absolute_error'] > .9
    assert report['status'] == 'outside_tolerance'


@pytest.mark.parametrize('defect', ['missing_column', 'nan', 'missing_cell', 'duplicate_time', 'missing_start', 'large_gap', 'negative_rms', 'wrong_units'])
def test_bad_trace_cannot_become_a_success(defect, tmp_path, calculation):
    spec, result = calculation
    rows = list(csv.reader(CSV.read_text().splitlines()))
    if defect == 'missing_column':
        rows = [r[:-1] for r in rows]
    elif defect == 'nan':
        rows[1][1] = 'NaN'
    elif defect == 'missing_cell':
        rows[1][2] = ''
    elif defect == 'duplicate_time':
        rows[2][0] = rows[1][0]
    elif defect == 'missing_start':
        del rows[1]
    elif defect == 'large_gap':
        del rows[2:10]
    elif defect == 'negative_rms':
        rows[1][1] = '-1'
    elif defect == 'wrong_units':
        rows[0][1] = 'current_pu'
    path, cfg = rewrite(tmp_path, rows)
    with pytest.raises(ValueError):
        compare_motor_trace(spec, result, path, cfg)


@pytest.mark.parametrize('defect', ['stale_code', 'network', 'unfinished', 'missing_channel', 'bad_event_side', 'settings', 'negative_current', 'negative_voltage'])
def test_mismatched_or_incomplete_simulation_is_rejected(defect, calculation):
    spec, result = calculation
    changed = copy.deepcopy(result)
    if defect == 'stale_code':
        changed['code_sha256'] = '0'*64
    elif defect == 'network':
        changed['network_sha256'] = '0'*64
    elif defect == 'unfinished':
        changed['completed'] = False
    elif defect == 'missing_channel':
        del changed['channels']['m.current_pu']
    elif defect == 'bad_event_side':
        i = changed['sample_side'].index('post_event'); changed['sample_side'][i] = 'regular'
    elif defect == 'settings':
        changed['settings']['duration_s'] = 1.
    elif defect == 'negative_current':
        changed['channels']['m.current_pu'][0] = -1.
    elif defect == 'negative_voltage':
        changed['channels']['m.bus_voltage_pu'][0] = -1.
    with pytest.raises(ValueError):
        compare_motor_trace(spec, changed, CSV, protocol())


def test_checksum_and_declared_time_offset_are_enforced(tmp_path, calculation):
    spec, result = calculation
    cfg = protocol(); cfg.csv_sha256 = '0'*64
    with pytest.raises(ValueError, match='checksum'):
        compare_motor_trace(spec, result, CSV, cfg)
    cfg = protocol(); cfg.time_offset_s = .1
    with pytest.raises(ValueError, match='horizon'):
        compare_motor_trace(spec, result, CSV, cfg)
    # An explicitly declared offset can align a recorder whose epoch is -5 s.
    rows = list(csv.reader(CSV.read_text().splitlines()))
    for row in rows[1:]:
        row[0] = str(float(row[0])-5.)
    path, cfg = rewrite(tmp_path, rows); cfg.time_offset_s = 5.
    assert compare_motor_trace(spec, result, path, cfg)['passed']


def test_interpolation_preserves_event_jump_and_uses_post_event_limit():
    t = np.array([0., 1., 1., 2.]); values = np.array([[0.], [10.], [0.], [10.]])
    actual = _right_continuous_interpolate(t, values, np.array([.5, 1., 1.5]))
    assert actual[:, 0] == pytest.approx([5., 0., 5.])


def test_comparison_cli_and_exported_protocol_schema(tmp_path, calculation):
    _, result = calculation
    path = tmp_path/'result.json'; path.write_text(json.dumps(result))
    from power_grid_env.cli import main
    arguments = ['compare-motor-trace', '--study', str(STUDY), '--result', str(path), '--trace', str(CSV),
                 '--protocol', str(PROTOCOL), '--out', str(tmp_path/'comparison.json')]
    assert main(arguments) == 0
    bad = protocol(); bad.csv_sha256 = '0'*64
    bad_path = tmp_path/'protocol.json'; bad_path.write_text(bad.model_dump_json())
    arguments[arguments.index('--protocol')+1] = str(bad_path)
    assert main(arguments) == 2
    assert not json.loads((tmp_path/'comparison.json').read_text())['passed']
    from power_grid_env.schema_export import export_schemas
    export_schemas(tmp_path/'schemas')
    from jsonschema import validate
    validate(protocol().model_dump(mode='json'), json.loads((tmp_path/'schemas/motor-trace.schema.json').read_text()))
