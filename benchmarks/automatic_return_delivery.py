"""Bind opt-in automatic-return runtime, independent checks and installed replay."""
import shutil
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from power_grid_env.study_artifacts import read_json, encoded, seal, verify, digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.automatic_return_acceptance import producers

ROOTS = ['reports/automatic-return-qualified-final-2026-11-16',
         'reports/automatic-return-accepted-final-2026-11-16',
         'reports/automatic-return-installed-replay-final-2026-11-16']
TESTS = [Path('reports/automatic-return-regression-final-2026-11-16.xml'),
         Path('reports/automatic-return-physical-regression-final-2026-11-16.xml')]


def deliver(out):
    fp = package_fingerprint()
    anchors = {p: verify(p) for p in ROOTS}
    q, a, i = [read_json(Path(p)/name) for p, name in
               zip(ROOTS, ('summary.json', 'summary.json', 'receipt.json'))]
    if not all(r['passed'] and r['code_sha256'] == fp for r in (q, a, i)) or q['producers'] != producers():
        raise ValueError('qualification/runtime mismatch')
    expected = dict(independent_reference_replays=44, recomputed_comparisons=176,
                    native_integral_energy_checks=88, permutation_pairs=16,
                    reference_clock_segments_checked=1240, initial_spectra_checked=88)
    if (q['physical_cases'], q['native_runs'], q['independent_trajectories'], q['comparisons']) != (22, 88, 44, 176):
        raise ValueError('qualification inventory differs')
    if (a['source_manifest_sha256'] != anchors[ROOTS[0]] or
        any(a[k] != v for k, v in expected.items()) or
        a['maximum_native_integral_error_j'] > .1 or
        a['maximum_reference_integral_error_j'] > 1e-4 or
        a['maximum_clock_integral_error'] > 2e-8 or
        len(i['rows']) != 108 or not i['reference_imports_blocked'] or
        (i['new_records'], i['legacy_records']) != (88, 20)):
        raise ValueError('consumer/replay coverage mismatch')
    if not all(r['passed'] and r['compared'] and r['common_channels_bitwise_exact'] for r in i['rows']):
        raise ValueError('installed trace mismatch')
    actions = dict(return_starts=128, return_completions=56, return_blocks=48,
                   fallbacks=16, cancelled_actions=40, recovery_resets=16, forward_transfers=104)
    if a['native_protection_actions'] != actions:
        raise ValueError('return/cancellation/fallback inventory differs')
    inventories = []
    for p in TESTS:
        tests = list(ET.parse(p).getroot().iter('testcase'))
        if not tests or any(c.find(k) is not None for c in tests for k in ('failure', 'error', 'skipped')):
            raise ValueError('nonpassing targeted test evidence')
        inventories.append(dict(path=str(p), count=len(tests), sha256=digest(p.read_bytes())))
    if [r['count'] for r in inventories] != [235, 149]:
        raise ValueError('targeted regression inventory differs')
    new_tests = sum(c.attrib.get('classname', '').endswith('test_automatic_return')
                    for c in ET.parse(TESTS[0]).getroot().iter('testcase'))
    if new_tests != 41:
        raise ValueError('new return test inventory differs')
    old = Path('reports/transfer-lifecycle-delivery-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    wheel = Path('reports/automatic-return-wheel-final-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(old.read_bytes()) != 'f688e5fd4f5fbd5e778fa2cd2cf9c8eba9fd86918c5ad784a0f60fe6f01da439':
        raise ValueError('previous wheel differs')
    with ZipFile(old) as x, ZipFile(wheel) as y:
        before = {p: x.read(p) for p in x.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
        after = {p: y.read(p) for p in y.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed = sorted(p for p in before if before[p] != after.get(p))
    added = sorted(after.keys()-before.keys())
    if (changed != ['power_grid_env/coupled_rms.py', 'power_grid_env/dae.py', 'power_grid_env/network_automation.py'] or
        added != ['power_grid_env/automatic_return.py'] or before.keys()-after.keys() or
        any(Path(p).read_bytes() != value for p, value in after.items())):
        raise ValueError('runtime source boundary differs')
    cli = Path('reports/automatic-return-cli-final-2026-11-16.json')
    trace = read_json(cli)
    if not trace['completed'] or trace['code_sha256'] != fp or not any(
            e['kind'] == 'automatic_return_completed' for e in trace['event_log']):
        raise ValueError('CLI example differs')
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    for p in [wheel, cli, *TESTS]:
        shutil.copyfile(p, root/p.name)
    (root/'old-runtime').mkdir()
    shutil.copyfile(old, root/'old-runtime'/old.name)
    sources = ({Path(p) for p in producers()} |
               set(Path('benchmarks').glob('automatic_return*.py')) |
               set(Path('cases/automatic-return').glob('*')) |
               {Path('tests/test_automatic_return.py'),
                Path('benchmarks/rms_equilibrium_installed_replay.py'),
                Path('plans/CURRENT_STATUS.md'), Path('plans/agent-designer.yaml'),
                Path('README.md'), Path('README.en.md')})
    for p in sources:
        dest = root/'sources'/p
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    result = dict(passed=True, code_sha256=fp, wheel_sha256=digest(wheel.read_bytes()),
                  previous_wheel_sha256=digest(old.read_bytes()), changed_runtime_files=changed,
                  added_runtime_files=added, evidence_manifests=anchors, test_inventories=inventories,
                  targeted_tests=sum(r['count'] for r in inventories), new_return_tests=new_tests,
                  installed_checks=108, independent_host=False, authenticated=False,
                  normative_approval=False, scope=q['scope'], native_protection_actions=actions,
                  maximum_native_integral_error_j=a['maximum_native_integral_error_j'],
                  maximum_reference_integral_error_j=a['maximum_reference_integral_error_j'],
                  maximum_clock_integral_error=a['maximum_clock_integral_error'], **expected)
    (root/'delivery.json').write_bytes(encoded(result))
    if fp != package_fingerprint() or anchors != {p: verify(p) for p in ROOTS}:
        raise ValueError('evidence changed')
    seal(root)
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--out', required=True)
    print(deliver(**vars(p.parse_args())))
