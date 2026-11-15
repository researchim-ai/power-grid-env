"""Independent hybrid extension corpus: all OC curves, CT reset and thermal trip."""
import copy
from pathlib import Path

from benchmarks.loaded_protection_winding_reference import ProtectionReference
from benchmarks.loaded_protection_winding_acceptance import compare
from power_grid_env.study_spec import StudySpec, run_study
from power_grid_env.study_artifacts import read_json, encoded, seal, verify, digest
from power_grid_env.evidence import package_fingerprint


def jobs():
    base = Path('cases/motor-only-loaded/protected')
    for kind in ['primary', 'breaker_failure', 'measurement_loss']:
        for curve in ['definite', 'standard_inverse', 'very_inverse', 'extremely_inverse']:
            s = read_json(base/(kind+'-uv.json'))
            for r in s['coupled']['network_automation']['relays']:
                r['curve'] = curve
            yield kind+'-'+curve, s
    s = read_json(base/'primary-uv.json')
    c = s['coupled']
    c['faults'][0]['clear_s'] = .105
    c['faults'].append(dict(c['faults'][0], id='second-fault', apply_s=.2, clear_s=.4))
    c['events'] = [dict(time_s=.102, kind='relay_measurement_lost', equipment_id='primary-oc'),
                   dict(time_s=.15, kind='relay_measurement_restored', equipment_id='primary-oc')]
    yield 'reset-restore-repeat-fault', s
    s = read_json(base/'primary-uv.json')
    s['coupled']['operating_point']['thermal_state'] = 'passport'
    s['coupled']['motors'][0]['thermal'].update(initial_level=0., time_constant_s=.5, trip_level=.2)
    yield 'thermal-trip', s


def qualify(out):
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    rows = []
    fingerprint = package_fingerprint()
    for id, study in jobs():
        child = root/id
        child.mkdir()
        reference = ProtectionReference(study).run()
        checks = []
        for method in ['radau', 'network_dae']:
            owner = copy.deepcopy(study)
            owner['coupled']['integration_method'] = method
            spec = StudySpec.model_validate(owner)
            report = run_study(spec)
            checked = compare(reference, report)
            checks.append(dict(method=method, **{k: v for k, v in checked.items() if k != 'values'}))
            for name, value in [('study', spec.model_dump(mode='json')), ('trace', report), ('comparison', checked)]:
                (child/(method+'-'+name+'.json')).write_bytes(encoded(value))
        (child/'events.json').write_bytes(encoded(reference.events))
        (child/'initialization.json').write_bytes(encoded(reference.initial))
        row = dict(id=id, passed=all(c['passed'] for c in checks), checks=checks)
        (child/'summary.json').write_bytes(encoded(row))
        seal(child)
        rows.append(dict(**row, manifest_sha256=verify(child)))
        print('hybrid extension', id, row['passed'], flush=True)
    if package_fingerprint() != fingerprint:
        raise ValueError('runtime changed during qualification')
    result = dict(passed=len(rows) == 14 and all(r['passed'] for r in rows), independent_trajectories=14,
                  native_runs=28, code_sha256=fingerprint, rows=rows,
                  producers={p: digest(Path(p).read_bytes()) for p in [__file__, 'benchmarks/loaded_protection_winding_reference.py', 'benchmarks/loaded_protection_winding_acceptance.py', 'benchmarks/motor_only_loaded_reference.py']},
                  scope='One authored endpoint per extension:4 OC families with primary/failed-breaker/CT loss; repeated fault with CT loss/restoration and exponential reset;thermal trip. Two native engines. No tolerance envelope or OEM relay equivalence.')
    (root/'summary.json').write_bytes(encoded(result))
    seal(root)
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', required=True)
    result = qualify(**vars(p.parse_args()))
    print(dict(passed=result['passed'], native_runs=result['native_runs']))
    if not result['passed']:
        raise SystemExit(2)
