import csv
import io
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from power_grid_env.motor_recordings import Recording, RecordingImport, convert_recording, import_recordings, verify_recordings
from power_grid_env.motor_catalog import catalog_entry
from power_grid_env.study_artifacts import encoded, seal, verify, export_zip, extract_zip


def fixture(coordinates='abc_phase_to_neutral', frequency=50., id='train'):
    time = np.linspace(0, .1, 501)
    phase = 2 * math.pi * frequency * time
    voltage = 230 * np.exp(1j * phase)
    current = 8 * np.exp(1j * (phase - .3))
    names = ['speed'] + (['ua', 'ub', 'ia', 'ib'] if coordinates == 'alpha_beta' else ['u_a', 'u_b', 'u_c', 'i_a', 'i_b', 'i_c'])
    config = dict(id=id, source=f'{id}.csv', provenance='Authored analytic balanced fixture', origin='synthetic', split='fitting' if id == 'train' else 'validation', frequency_hz=frequency,
                  coordinates=coordinates, voltage_unit='kV', current_unit='mA', speed_unit='rpm', time_unit='ms', time_column='clock',
                  channels={name: dict(column=name) for name in names}, initial_stator_flux_vs=[0, 0], initial_rotor_flux_vs=[0, 0], current_sigma_a=.01, speed_sigma_rad_s=.02)
    columns = {'clock': (time + 2.) * 1000, 'speed': np.linspace(0, 100, len(time)) * 60 / (2 * math.pi)}
    if coordinates == 'alpha_beta':
        columns.update(ua=voltage.real / 1000, ub=voltage.imag / 1000, ia=current.real * 1000, ib=current.imag * 1000)
    else:
        for prefix, vector, factor in [('u', voltage, .001), ('i', current, 1000)]:
            for k, name in enumerate('abc'):
                columns[f'{prefix}_{name}'] = (vector * np.exp(-2j * math.pi * k / 3)).real * factor
    return config, columns


def raw(columns, delimiter=','):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, delimiter=delimiter)
    writer.writerow(columns)
    writer.writerows(zip(*columns.values(), strict=True))
    return stream.getvalue().encode()


def write_protocol(root, coordinates='abc_phase_to_neutral'):
    configs = []
    for frequency, id in [(50., 'train'), (40., 'holdout')]:
        config, columns = fixture(coordinates, frequency, id)
        (root / config['source']).write_bytes(raw(columns))
        configs.append(config)
    protocol = dict(identification=dict(initial=catalog_entry('aalto-linear-fan')['motor'].model_dump(mode='json'), mechanical_load='fixed'), recordings=configs)
    path = root / 'input.json'
    path.write_bytes(encoded(protocol))
    return path, protocol


@pytest.mark.parametrize('coordinates', ['abc_phase_to_neutral', 'alpha_beta'])
def test_known_peak_clarke_units_and_clock_origin(coordinates):
    config, columns = fixture(coordinates)
    trace, diagnostic = convert_recording(raw(columns), Recording(**config))
    time = np.array(trace.time_s)
    assert np.array(trace.voltage_alpha_v) == pytest.approx(230 * np.cos(2 * np.pi * 50 * time), abs=2e-10)
    assert np.array(trace.voltage_beta_v) == pytest.approx(230 * np.sin(2 * np.pi * 50 * time), abs=2e-10)
    assert np.array(trace.current_alpha_a) == pytest.approx(8 * np.cos(2 * np.pi * 50 * time - .3), abs=2e-11)
    assert trace.speed_rad_s[-1] == pytest.approx(100)
    assert diagnostic['source_time_origin_s'] == 2.
    assert not diagnostic['resampled'] and not diagnostic['filtered']


def test_calibration_is_explicit_and_in_declared_input_units():
    config, columns = fixture('alpha_beta')
    columns['ia'] = -columns['ia'] / 2 + 17
    config['channels']['ia'].update(gain=2., polarity=-1, offset=17.)
    trace, _ = convert_recording(raw(columns), Recording(**config))
    assert trace.current_alpha_a[0] == pytest.approx(8 * math.cos(.3))


@pytest.mark.parametrize('defect', ['missing', 'duplicate_header', 'nan', 'blank', 'ragged', 'duplicate_time', 'reverse_time', 'gap', 'few', 'zero_sequence', 'negative_sequence', 'reverse_phases', 'zero_voltage', 'oversize', 'non_utf8', 'calibration_overflow', 'si_overflow', 'clock_overflow'])
def test_bad_measurements_are_rejected(defect):
    config, columns = fixture()
    if defect == 'missing': del columns['u_c']
    elif defect == 'nan': columns['i_a'][10] = float('nan')
    elif defect == 'duplicate_time': columns['clock'][10] = columns['clock'][9]
    elif defect == 'reverse_time': columns['clock'] = -columns['clock']
    elif defect == 'gap': columns['clock'][250:] += 5
    elif defect == 'few': columns = {k: v[:20] for k, v in columns.items()}
    elif defect == 'zero_sequence':
        for p in 'abc': columns[f'u_{p}'] += .02
    elif defect == 'negative_sequence': columns['u_a'] *= 1.4
    elif defect == 'reverse_phases': columns['u_b'], columns['u_c'] = columns['u_c'], columns['u_b']
    elif defect == 'zero_voltage':
        for p in 'abc': columns[f'u_{p}'] *= 0
    elif defect == 'calibration_overflow':
        columns['i_a'][3] = 1e308
        config['channels']['i_a']['gain'] = 1e6
    elif defect == 'si_overflow': columns['u_a'][3] = 1e308
    elif defect == 'clock_overflow': columns['clock'][0], columns['clock'][-1] = -1e308, 1e308
    source = raw(columns)
    if defect == 'duplicate_header': source = source.replace(b'u_c', b'u_b', 1)
    elif defect == 'blank': source += b'\n'
    elif defect == 'ragged': source += b'1,2\n'
    elif defect == 'oversize': source = b'x' * 12_000_001
    elif defect == 'non_utf8': source = b'\xff'
    with pytest.raises(ValueError): convert_recording(source, Recording(**config))


@pytest.mark.parametrize('defect', ['rms', 'line_voltage', 'duplicate_mapping', 'path', 'absolute', 'unknown', 'missing_flux', 'blank_provenance'])
def test_ambiguous_or_unsupported_protocol_is_rejected(defect):
    config, _ = fixture()
    if defect == 'rms': config['voltage_unit'] = 'V RMS'
    elif defect == 'line_voltage': config['coordinates'] = 'abc_line_to_line'
    elif defect == 'duplicate_mapping': config['channels']['u_c']['column'] = 'u_b'
    elif defect == 'path': config['source'] = '../escape.csv'
    elif defect == 'absolute': config['source'] = '/tmp/source.csv'
    elif defect == 'unknown': config['auto_align'] = True
    elif defect == 'missing_flux': del config['initial_rotor_flux_vs']
    else: config['provenance'] = ' '
    with pytest.raises(ValueError): Recording(**config)


def test_sealed_sources_semantic_verification_and_portable_export(tmp_path):
    path, _ = write_protocol(tmp_path)
    result = import_recordings(path, tmp_path / 'prepared')
    assert result['prepared'] and not result['identification_approved']
    assert verify_recordings(tmp_path / 'prepared')['recordings'] == 2
    export_zip(tmp_path / 'prepared', tmp_path / 'data.zip')
    extract_zip(tmp_path / 'data.zip', tmp_path / 'restored')
    assert verify_recordings(tmp_path / 'restored')['verified']
    with pytest.raises(ValueError): import_recordings(path, tmp_path / 'prepared')


@pytest.mark.parametrize('target', ['study.json', 'result.json', 'sources/00.csv', 'report.html'])
def test_resealing_does_not_hide_inconsistent_conversion(tmp_path, target):
    path, _ = write_protocol(tmp_path)
    out = tmp_path / 'prepared'
    import_recordings(path, out)
    file = out / target
    if target == 'study.json':
        value = json.loads(file.read_bytes()); value['fitting'][0]['current_alpha_a'][1] += .1
        file.write_bytes(encoded(value))
    elif target == 'result.json':
        value = json.loads(file.read_bytes()); value['identification_approved'] = True
        file.write_bytes(encoded(value))
    elif target == 'report.html':
        file.write_text('Imported and approved')
    else:
        file.write_bytes(file.read_bytes().replace(b'8000', b'7000'))
        # Ensure a real change even when the textual numeric pattern is absent.
        file.write_bytes(file.read_bytes() + b'\n')
    (out / 'manifest.json').unlink(); seal(out); verify(out)
    with pytest.raises(ValueError): verify_recordings(out)


def test_symlink_source_and_escaping_directory_fail(tmp_path):
    path, protocol = write_protocol(tmp_path)
    original = tmp_path / 'train.csv'; original.rename(tmp_path / 'real.csv'); original.symlink_to('real.csv')
    with pytest.raises(ValueError): import_recordings(path, tmp_path / 'prepared')
    assert not (tmp_path / 'prepared').exists()


def test_holdout_identity_still_checked_after_conversion(tmp_path):
    path, protocol = write_protocol(tmp_path)
    protocol['recordings'][1].update(source='train.csv', frequency_hz=50.)
    path.write_bytes(encoded(protocol))
    with pytest.raises(ValueError, match='hold-out excitations'): import_recordings(path, tmp_path / 'prepared')


def test_cli_import_and_verify(tmp_path):
    path, _ = write_protocol(tmp_path)
    base = [sys.executable, '-m', 'power_grid_env.cli', 'motor-recordings']
    completed = subprocess.run(base + ['import', '--input', str(path), '--out', str(tmp_path / 'prepared')], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    completed = subprocess.run(base + ['verify', str(tmp_path / 'prepared')], capture_output=True, text=True)
    assert completed.returncode == 0 and json.loads(completed.stdout)['verified']


def test_traces_cannot_bypass_csv_protocol(tmp_path):
    _, protocol = write_protocol(tmp_path)
    protocol['identification']['fitting'] = []
    with pytest.raises(ValueError): RecordingImport(**protocol)


def test_solver_cli_rejects_tampered_import_before_creating_output(tmp_path):
    path, _ = write_protocol(tmp_path)
    out = tmp_path / 'prepared'
    import_recordings(path, out)
    (out / 'study.json').write_text('{}')
    command = [sys.executable, '-m', 'power_grid_env.cli', 'identify-dynamic-motor', '--recordings', str(out), '--out', str(tmp_path / 'identified')]
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode == 2 and not (tmp_path / 'identified').exists()


def test_load_prepared_input_checks_manifest_and_semantics(tmp_path):
    from power_grid_env.motor_recordings import load_recordings
    path, _ = write_protocol(tmp_path)
    out = tmp_path / 'prepared'
    import_recordings(path, out)
    assert len(load_recordings(out)['fitting']) == 1


def test_coordinate_roundoff_does_not_create_an_independent_holdout(tmp_path):
    first, a = fixture('alpha_beta')
    second, b = fixture('abc_phase_to_neutral', id='holdout')
    second['frequency_hz'] = first['frequency_hz']
    (tmp_path / first['source']).write_bytes(raw(a))
    (tmp_path / second['source']).write_bytes(raw(b))
    protocol = dict(identification=dict(initial=catalog_entry('aalto-linear-fan')['motor'].model_dump(mode='json'), mechanical_load='fixed'), recordings=[first, second])
    (tmp_path / 'input.json').write_bytes(encoded(protocol))
    with pytest.raises(ValueError, match='same excitation'):
        import_recordings(tmp_path / 'input.json', tmp_path / 'prepared')


def test_html_escapes_labels_and_schema_is_exported(tmp_path):
    from power_grid_env.schema_export import export_schemas
    path, protocol = write_protocol(tmp_path)
    protocol['recordings'][0]['id'] = '<script>attack</script>'
    path.write_bytes(encoded(protocol))
    import_recordings(path, tmp_path / 'prepared')
    html = (tmp_path / 'prepared/report.html').read_text()
    assert '<script>attack' not in html and '&lt;script&gt;attack' in html and '<svg' in html
    export_schemas(tmp_path / 'schemas')
    assert (tmp_path / 'schemas/motor-recordings.schema.json').exists()


@pytest.mark.parametrize('name', ['linear', 'saturated', 'viscous', 'locked'])
def test_frozen_independent_observations_survive_phase_conversion(tmp_path, name):
    from benchmarks.motor_recording_acceptance import csv_fixture
    from power_grid_env.study_artifacts import read_json
    document = read_json(Path(__file__).resolve().parents[1] / 'cases/dynamic-identification' / (name + '.json'))
    path = csv_fixture(document, tmp_path / 'input', 'abc_phase_to_neutral')
    import_recordings(path, tmp_path / 'prepared')
    converted = read_json(tmp_path / 'prepared/study.json')
    for split in ('fitting', 'validation'):
        for original, actual in zip(document[split], converted[split], strict=True):
            for field in ('time_s', 'voltage_alpha_v', 'voltage_beta_v', 'current_alpha_a', 'current_beta_a', 'speed_rad_s'):
                assert np.asarray(actual[field]) == pytest.approx(original[field], abs=1e-10)
    assert verify_recordings(tmp_path / 'prepared')['verified']
