"""Known sensor transforms and voltage noise on frozen independent observations."""
from pathlib import Path
import argparse
import csv
import json
import numpy as np

from benchmarks.motor_recording_acceptance import ROOT, FIXTURE_HASHES, csv_fixture
from power_grid_env.calibrated_motor_study import run_calibrated_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json, encoded, digest, seal


def fixture(directory, profile='linear', defect=None, voltage_noise_std_v=.0001):
    directory = Path(directory)
    source = ROOT / 'cases/dynamic-identification' / (profile + '.json')
    if digest(source.read_bytes()) != FIXTURE_HASHES[profile]: raise ValueError('frozen reference changed')
    document = read_json(source)
    document['maximum_rhs_evaluations'] = 4000000
    if voltage_noise_std_v >= .01:
        document['maximum_rhs_evaluations'] = 10000000
        document['maximum_wall_s'] = 600.
    for split_index, split in enumerate(('fitting', 'validation')):
        for i, trace in enumerate(document[split]):
            rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence([18001, split_index, i])))
            for field in ('voltage_alpha_v', 'voltage_beta_v', 'current_alpha_a', 'current_beta_a'):
                std = voltage_noise_std_v if field.startswith('voltage') else .0001
                trace[field] = (np.asarray(trace[field]) + rng.normal(0, std, len(trace['time_s']))).tolist()
            trace['current_sigma_a'] = .004; trace['speed_sigma_rad_s'] = .01
            trace['provenance'] += f'; authored voltage noise std={voltage_noise_std_v} V, current std=.0001 A; PCG64 SeedSequence([18001,split,trace]); known transducer transforms'
    protocol = csv_fixture(document, directory, 'abc_phase_to_neutral')
    config = read_json(protocol)
    for recording in config['recordings']:
        path = directory / recording['source']
        with path.open(newline='') as stream:
            reader = csv.DictReader(stream, delimiter=';'); columns = reader.fieldnames; rows = list(reader)
        for role, channel in recording['channels'].items():
            if role == 'speed': continue
            gain = 1.125 if role.startswith('u') else .8
            polarity = -1 if role.endswith('_a') else 1
            offset = .013 if role.startswith('u') else 17.
            values = (np.array([float(row[channel['column']]) for row in rows]) - channel.get('offset', 0.)) * channel.get('gain', 1.) * channel.get('polarity', 1)
            raw = offset + values / (gain * polarity)
            for row, value in zip(rows, raw, strict=True): row[channel['column']] = str(float(value))
            channel.update(gain=gain, polarity=polarity, offset=offset, raw_minimum=-2. if role.startswith('u') else -100000., raw_maximum=2. if role.startswith('u') else 100000.)
            if defect == 'wrong_holdout_voltage_gain' and recording['split'] == 'validation' and role.startswith('u'):
                channel['gain'] *= 1.02
        with path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, delimiter=';'); writer.writeheader(); writer.writerows(rows)
    protocol.write_bytes(encoded(config))
    ref = dict(basis='synthetic_known', evidence='Authored known transforms of hash-pinned original motulator fixture; not hardware calibration')
    study = dict(recording_protocol='protocol.json', voltage_reference=ref, current_reference=ref, speed_reference=ref,
                 initial_flux_reference='known_in_declared_alpha_beta_axes')
    if defect == 'unknown_joint_scales': study['joint_sensor_parameter_estimation'] = True
    if defect == 'unknown_flux': study['initial_flux_reference'] = 'unknown'
    path = directory / 'study.json'; path.write_bytes(encoded(study))
    return path


def run(out, wheel, *, extended_voltage_challenge=False):
    from benchmarks.extended_equipment_validation import verify_native
    from benchmarks.flux_network_native import reference
    from benchmarks.dynamic_identification_acceptance import deployment_compare
    from power_grid_env.flux_network import run_flux_network
    from power_grid_env.flux_machine import FluxMachinePassport
    from power_grid_env.motor_catalog import catalog_entry, catalog_study
    from power_grid_env.study_artifacts import export_zip, extract_zip, verify
    native_identity = verify_native(Path(wheel)); root = Path(out)
    if root.exists(): raise ValueError('new calibration acceptance directory required')
    code = package_fingerprint()
    paths = {name: fixture(root / name / 'input', name) for name in ('linear', 'saturated', 'viscous', 'locked')}
    if extended_voltage_challenge:
        paths['voltage_noise_10mv'] = fixture(root / 'voltage_noise_10mv/input', voltage_noise_std_v=.01)
    for name in ('wrong_holdout_voltage_gain', 'unknown_joint_scales', 'unknown_flux'):
        paths[name] = fixture(root / name / 'input', defect=name)
    (root / 'protocol.json').write_bytes(encoded(dict(profiles=list(paths), seed=18001, voltage_noise_std_v=[.0001, .01] if extended_voltage_challenge else [.0001], current_noise_std_a=.0001,
                                                    parameter_relative_limit=.02, deployment_limits='unchanged B15 deployment_compare limits')))
    rows = []
    for name, path in paths.items():
        if name in ('unknown_joint_scales', 'unknown_flux'):
            try: run_calibrated_study(path, root / name / 'run')
            except ValueError as exc: rows.append(dict(id=name, passed=True, rejected_before_solver=True, reason=str(exc)))
            else: rows.append(dict(id=name, passed=False))
            continue
        result = run_calibrated_study(path, root / name / 'run')
        if name == 'wrong_holdout_voltage_gain':
            rows.append(dict(id=name, passed=result['completed'] and not result['passed'] and not (root / name / 'run/passport.json').exists(), reason=result['reason']))
            continue
        truth = catalog_entry('aalto-linear-fan' if name in ('linear', 'locked', 'voltage_noise_10mv') else 'aalto-saturated-fan')['motor']
        if name == 'viscous': truth.fan_nm_per_rad_s2 = 0.; truth.viscous_nm_per_rad_s = 14.6 / (np.pi * 50)
        if name == 'locked': truth.locked_rotor = True
        errors = {}; check = dict(passed=False)
        if result['passed']:
            fitted = FluxMachinePassport.model_validate(read_json(root / name / 'run/passport.json'))
            for field in ('stator_r_ohm', 'unsaturated_h', 'inertia_kg_m2', 'fan_nm_per_rad_s2', 'viscous_nm_per_rad_s', 'saturation_beta_per_vs'):
                errors[field] = abs(getattr(fitted, field) - getattr(truth, field)) / max(abs(getattr(truth, field)), 1e-12)
            for field in ('resistance_ohm', 'leakage_h'):
                errors[field] = abs(getattr(fitted.cages[0], field) / getattr(truth.cages[0], field) - 1)
            net, settings = catalog_study('aalto-linear-fan', disturbance=False); settings.duration_s = .24; settings.step_s = .002
            native = settings.model_copy(deep=True); native.motors = [truth]; settings.motors = [fitted]
            primary = run_flux_network(net, settings); independent = reference(net, native, max_step=.0001)
            check = deployment_compare(primary, independent)
            for label, value in [('primary', primary), ('native', independent), ('deployment', check)]:
                (root / name / (label + '.json')).write_bytes(encoded(value))
            verify(root / name / 'run'); export_zip(root / name / 'run', root / name / 'study.zip')
            extract_zip(root / name / 'study.zip', root / name / 'imported'); verify(root / name / 'imported')
        rows.append(dict(id=name, passed=bool(result['passed'] and check['passed'] and errors and max(errors.values()) <= .02), parameter_relative_errors=errors, deployment=check))
    if code != package_fingerprint(): raise ValueError('calibration producer changed')
    summary = dict(passed=all(r['passed'] for r in rows), profiles=rows, producer_sha256=code,
                   scope='extended_voltage_challenge' if extended_voltage_challenge else 'known_calibration_small_voltage_noise',
                   checker_sha256=digest(Path(__file__).read_bytes()), independent_reference=native_identity,
                   wheel_sha256=digest(Path(wheel).read_bytes()), field_validated=False, calibration_authenticated=False)
    (root / 'summary.json').write_bytes(encoded(summary)); seal(root)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--out', required=True); parser.add_argument('--wheel', required=True)
    parser.add_argument('--extended-voltage-challenge', action='store_true', help='include currently unqualified 0.01 V full-identification profile; resource refusal is not a pass')
    args = parser.parse_args(); result = run(args.out, args.wheel, extended_voltage_challenge=args.extended_voltage_challenge)
    print(json.dumps(dict(passed=result['passed'], profiles=len(result['profiles'])))); raise SystemExit(0 if result['passed'] else 2)
