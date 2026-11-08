"""Frozen original-motulator observations -> CSV units -> fit -> free replay.

No production RHS creates observations. Frozen B15 inputs retain their origin;
this exercise does not claim a newly executed original simulator or hardware run.
"""
from pathlib import Path
import argparse
import csv
import json
import math

import numpy as np

from power_grid_env.dynamic_motor_identification import identify_dynamic
from power_grid_env.evidence import package_fingerprint
from power_grid_env.motor_catalog import catalog_entry
from power_grid_env.motor_recordings import import_recordings, verify_recordings
from power_grid_env.study_artifacts import digest, encoded, read_json, export_zip, extract_zip, seal

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ('linear', 'saturated', 'viscous', 'locked')
FIXTURE_HASHES = {
    'linear': '3441c0b1c2654192c0e8e4ec7da74fd68bb53fd88a77a1ec29b4d3056fb5ae3f',
    'saturated': '64dae48e4fdf96b965e2a9ce1c76a9705ea3e9c49d70aa17a88f93bd18d82426',
    'viscous': '98b1da57eed600b7e189ab4f57ee7c05f491a58f359226223e0fb640b33d9c5c',
    'locked': '0c71c2ddf58bbd3920ad45694a90475fbe0783ab59394d67961ccf336b25502a',
}


def csv_fixture(document, directory, coordinates):
    """Encode existing independent observations, without a numerical model call."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    recordings = []
    for split in ('fitting', 'validation'):
        for trace in document[split]:
            source = trace['id'] + '.csv'
            columns = {'time_ms': (np.array(trace['time_s']) + 2) * 1000,
                       'speed_rpm': np.array(trace['speed_rad_s']) * 60 / (2 * math.pi)}
            voltage = np.array(trace['voltage_alpha_v']) + 1j * np.array(trace['voltage_beta_v'])
            current = np.array(trace['current_alpha_a']) + 1j * np.array(trace['current_beta_a'])
            names = {}
            if coordinates == 'alpha_beta':
                for prefix, vector, factor in [('u', voltage, .001), ('i', current, 1000.)]:
                    for axis, values in [('a', vector.real), ('b', vector.imag)]:
                        name = prefix + axis
                        columns[name] = values * factor
                        names[name] = dict(column=name)
            else:
                for prefix, vector, factor in [('u', voltage, .001), ('i', current, 1000.)]:
                    for k, phase in enumerate('abc'):
                        name = prefix + '_' + phase
                        columns[name] = np.real(vector * np.exp(-2j * math.pi * k / 3)) * factor
                        names[name] = dict(column=name)
            # Explicit transducer inversion/offset, exactly reversible.
            channel = next(name for name in names if name.startswith('i'))
            columns[channel] = -columns[channel] / 2 + 17
            names[channel].update(gain=2., polarity=-1, offset=17.)
            names['speed'] = dict(column='speed_rpm')
            with (directory / source).open('w', newline='', encoding='utf-8') as stream:
                writer = csv.writer(stream, delimiter=';')
                writer.writerow(columns)
                writer.writerows(zip(*columns.values(), strict=True))
            recordings.append(dict(id=trace['id'], source=source, provenance=trace['provenance'], origin='synthetic', split=split,
                                   frequency_hz=trace['frequency_hz'], coordinates=coordinates, voltage_unit='kV', current_unit='mA',
                                   speed_unit='rpm', time_unit='ms', time_column='time_ms', channels=names, delimiter=';',
                                   initial_stator_flux_vs=trace['initial_stator_flux_vs'], initial_rotor_flux_vs=trace['initial_rotor_flux_vs'],
                                   current_sigma_a=trace['current_sigma_a'], speed_sigma_rad_s=trace['speed_sigma_rad_s']))
    protocol = dict(schema_version='power-grid-env.motor-recordings/v1', identification={k: v for k, v in document.items() if k not in ('fitting', 'validation')}, recordings=recordings)
    (directory / 'protocol.json').write_bytes(encoded(protocol))
    return directory / 'protocol.json'


def run(out):
    out = Path(out)
    if out.exists():
        raise ValueError('new acceptance output required')
    code = package_fingerprint()
    sources = {name: read_json(ROOT / 'cases' / 'dynamic-identification' / (name + '.json')) for name in PROFILES}
    identities = {name: digest((ROOT / 'cases' / 'dynamic-identification' / (name + '.json')).read_bytes()) for name in PROFILES}
    if identities != FIXTURE_HASHES:
        raise ValueError('frozen original-motulator fixture identity changed')
    rows = []
    for name, document in sources.items():
        for coordinates in ('alpha_beta', 'abc_phase_to_neutral'):
            root = out / (name + '-' + coordinates)
            path = csv_fixture(document, root / 'input', coordinates)
            import_recordings(path, root / 'prepared')
            verify_recordings(root / 'prepared')
            converted = read_json(root / 'prepared' / 'study.json')
            errors = {}
            for split in ('fitting', 'validation'):
                for expected, actual in zip(document[split], converted[split], strict=True):
                    for field in ('time_s', 'voltage_alpha_v', 'voltage_beta_v', 'current_alpha_a', 'current_beta_a', 'speed_rad_s'):
                        errors[expected['id'] + '/' + field] = float(np.max(np.abs(np.array(expected[field]) - actual[field])))
            converted_ok = max(errors.values()) < 1e-9
            export_zip(root / 'prepared', root / 'recordings.zip')
            extract_zip(root / 'recordings.zip', root / 'restored')
            verify_recordings(root / 'restored')
            if read_json(root / 'restored' / 'study.json') != converted:
                raise ValueError('CSV package transport changed prepared study')
            result = identify_dynamic(converted)
            (root / 'identification.json').write_bytes(encoded(result))
            truth = catalog_entry('aalto-linear-fan' if name in ('linear', 'locked') else 'aalto-saturated-fan')['motor'].model_dump(mode='json')
            if name == 'viscous':
                truth['fan_nm_per_rad_s2'] = 0.
                truth['viscous_nm_per_rad_s'] = 14.6 / (math.pi * 50)
            actual = result.get('passport', {})
            parameters = {}
            if result.get('passed'):
                for field in ('stator_r_ohm', 'unsaturated_h', 'inertia_kg_m2', 'fan_nm_per_rad_s2', 'viscous_nm_per_rad_s', 'saturation_beta_per_vs'):
                    parameters[field] = abs(actual[field] - truth[field]) / max(abs(truth[field]), 1e-12)
                for field in ('resistance_ohm', 'leakage_h'):
                    parameters[field] = abs(actual['cages'][0][field] / truth['cages'][0][field] - 1)
            passed = bool(converted_ok and result.get('passed') and parameters and max(parameters.values()) <= 5e-5)
            rows.append(dict(profile=name, coordinates=coordinates, passed=passed, conversion_errors=errors,
                             parameter_relative_errors=parameters, independent_holdout_free_replay=result.get('assessments'),
                             source_fixture_sha256=identities[name]))
    if package_fingerprint() != code:
        raise ValueError('acceptance producer changed')
    summary = dict(schema='power-grid-env.motor-recordings-acceptance/v1', passed=all(r['passed'] for r in rows),
                   producer_sha256=code, benchmark_sha256=digest(Path(__file__).read_bytes()),
                   frozen_original_motulator_fixtures_sha256=identities, profiles=rows,
                   observations='frozen B15 synthetic motulator traces; no new native execution; no measured hardware',
                   maximum_conversion_absolute_error=1e-9, parameter_relative_tolerance=5e-5)
    (out / 'summary.json').write_bytes(encoded(summary))
    seal(out)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    result = run(args.out)
    print(json.dumps(dict(passed=result['passed'], profiles=len(result['profiles']))))
    raise SystemExit(0 if result['passed'] else 2)
