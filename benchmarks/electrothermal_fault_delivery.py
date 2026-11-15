"""Bind qualified electrothermal evidence, source boundary and installed replays."""
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json, encoded, digest, verify, seal

ROOTS = ['reports/electrothermal-fault-qualified-2026-11-15',
         'reports/electrothermal-frozen-fault-qualified-2026-11-15',
         'reports/electrothermal-fault-accepted-2026-11-15',
         'reports/electrothermal-legacy-old-wheel-2026-11-15',
         'reports/electrothermal-installed-replay-2026-11-15',
         'reports/electrothermal-fault-cli-2026-11-15']
TESTS = ['reports/electrothermal-fault-regression-tests-2026-11-15.xml',
         'reports/electrothermal-fault-adapter-tests-2026-11-15.xml',
         'reports/electrothermal-frozen-control-tests-2026-11-15.xml']


def deliver(out):
    anchors = {p: verify(p) for p in ROOTS}
    fp = package_fingerprint()
    accepted = read_json(Path(ROOTS[2])/'summary.json')
    replay = read_json(Path(ROOTS[4])/'receipt.json')
    if not accepted['passed'] or not accepted['independent_reference_replayed'] or accepted['code_sha256'] != fp or accepted['manifests'] != {p: anchors[p] for p in ROOTS[:2]} or not replay['passed'] or replay['code_sha256'] != fp or len(replay['rows']) != 8:
        raise ValueError('acceptance/installed evidence binding differs')
    for p in ROOTS[:2]:
        s = read_json(Path(p)/'summary.json')
        for name, sha in s.get('producers', s.get('base_producers')).items():
            if digest(Path(name).read_bytes()) != sha:
                raise ValueError('qualification producer differs')
        if 'producer_sha256' in s and s['producer_sha256'] != digest(Path('benchmarks/electrothermal_frozen_fault.py').read_bytes()):
            raise ValueError('frozen producer differs')
    counts = {k: 0 for k in ['tests', 'errors', 'failures', 'skipped']}
    for file in TESTS:
        for suite in ET.parse(file).getroot().findall('testsuite'):
            for k in counts:
                counts[k] += int(suite.get(k, '0'))
    if counts['tests'] != 630 or any(counts[k] for k in ['errors', 'failures', 'skipped']):
        raise ValueError('expected630 focused passing checks')
    previous = Path('reports/motor-only-loaded-delivery-2026-11-15/power_grid_env-0.1.0-py3-none-any.whl')
    wheel = Path('reports/electrothermal-fault-wheel-2026-11-15/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(previous.read_bytes()) != 'd9c6def7c5add1dbacd85dfb099d1425257e1f6bd7bb00a197cbe2bd72b235f8':
        raise ValueError('old runtime wheel differs')
    with ZipFile(previous) as old, ZipFile(wheel) as new:
        a = {p: old.read(p) for p in old.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
        b = {p: new.read(p) for p in new.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed = sorted(p for p in a.keys() & b.keys() if a[p] != b[p])
    if a.keys() != b.keys() or changed != ['power_grid_env/phase_core_motor.py', 'power_grid_env/phase_network.py', 'power_grid_env/vf_drive.py'] or any(Path(p).read_bytes() != raw for p, raw in b.items()):
        raise ValueError('wheel/runtime/source boundary differs')
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(wheel, root/wheel.name)
    (root/'old-runtime').mkdir()
    shutil.copyfile(previous, root/'old-runtime'/previous.name)
    for file in TESTS:
        shutil.copyfile(file, root/Path(file).name)
    files = sorted(Path('benchmarks').glob('electrothermal_fault*.py'))+[Path('benchmarks/electrothermal_frozen_fault.py'), Path('benchmarks/electrothermal_installed_replay.py'), Path('benchmarks/phase_core_acceptance.py'), Path('cases/studies/flux-saturated_dol.json'), Path('tests/test_electrothermal_fault.py'), Path('tests/test_electrothermal_frozen_fault.py')]
    files += sorted(Path('cases/electrothermal-fault').glob('*'))
    for file in files:
        target = root/'sources'/file
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
    proof = dict(changed_runtime_files=changed, added_runtime_files=[], missing_runtime_files=[],
                 old_wheel_sha256=digest(previous.read_bytes()), new_wheel_sha256=digest(wheel.read_bytes()))
    (root/'source-boundary.json').write_bytes(encoded(proof))
    receipt = dict(passed=True, code_sha256=fp, wheel_sha256=proof['new_wheel_sha256'], evidence_manifests=anchors,
                   tests=counts, optional_ANDES_deselected=4, native_runs=120, independent_trajectories=60,
                   independent_reference_replays=60, new_wheel_replays=8, old_wheel_phase_baselines=2,
                   runtime_changes=changed, independent_host=False, authenticated=False, normative_approval=False,
                   scope='Local R06.37 delivery: bounded full-winding electrothermal faults, reference20 and frozen-initial-R controls.630 targeted checks, not full repository suite.8 new-wheel installed replays including4 new cases,2 old-phase and2 prior RMS baselines;same host/dependencies. Previous sealed results retain old wheel identity. No RMS R(T), OEM thermal fit, CI/publication or universal superiority claim.')
    (root/'delivery.json').write_bytes(encoded(receipt))
    if anchors != {p: verify(p) for p in ROOTS} or package_fingerprint() != fp:
        raise ValueError('evidence/runtime changed during delivery')
    seal(root)
    return receipt


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', required=True)
    result = deliver(**vars(p.parse_args()))
    print(dict(passed=result['passed'], native_runs=result['native_runs'], tests=result['tests']['tests']))
