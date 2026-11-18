"""Bind initial-PT runtime, independent thermal/ATS evidence and local wheel."""
import shutil
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from power_grid_env.study_artifacts import read_json, encoded, seal, verify, digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.standby_interlocks_benchmark import producers

ROOTS = ['reports/standby-interlocks-qualified-final-2026-11-16',
         'reports/standby-interlocks-accepted-final-2026-11-16',
         'reports/standby-interlocks-installed-replay-2026-11-16']
TESTS = [Path('reports/standby-interlocks-regression-2026-11-16.xml'),
         Path('reports/standby-interlocks-physical-regression-2026-11-16.xml')]


def test_inventory(path):
    cases = list(ET.parse(path).getroot().iter('testcase'))
    if not cases or any(c.find(k) is not None for c in cases for k in ('failure', 'error', 'skipped')):
        raise ValueError('nonpassing test evidence')
    return dict(path=str(path), count=len(cases), sha256=digest(path.read_bytes()))


def deliver(out):
    fp = package_fingerprint()
    anchors = {p: verify(p) for p in ROOTS}
    q, a, i = [read_json(Path(p)/name) for p, name in zip(ROOTS, ('summary.json', 'summary.json', 'receipt.json'))]
    if not all(r['passed'] and r['code_sha256'] == fp for r in (q, a, i)) or q['producers'] != producers():
        raise ValueError('evidence/runtime mismatch')
    actions = dict(thermal_trips=24, thermal_close_blocks=48, transfer_starts=112,
                   transfer_blocks=16, return_completions=16)
    if ((q['physical_cases'], q['native_runs'], q['independent_trajectories'], q['comparisons']) != (20, 80, 40, 160) or
        a['source_manifest_sha256'] != anchors[ROOTS[0]] or
        (a['independent_reference_replays'], a['recomputed_comparisons'], a['native_energy_integrals'], a['permutation_pairs']) != (40, 160, 80, 16) or
        a['native_actions'] != actions or a['maximum_clock_error'] > 2e-8 or
        a['maximum_native_energy_error_j'] > .1 or a['maximum_reference_energy_error_j'] > 1e-4 or
        a['reference_clock_segments'] != 864 or len(i['rows']) != 96 or
        (i['new_records'], i['legacy_records']) != (80, 16) or not i['reference_imports_blocked']):
        raise ValueError('interlock acceptance inventory differs')
    if not all(r['passed'] and r['compared'] and r['common_channels_bitwise_exact'] for r in i['rows']):
        raise ValueError('installed trace differs')
    inventories = [test_inventory(p) for p in TESTS]
    recheck = Path('reports/standby-interlocks-owner-recheck-final-2026-11-16.xml')
    if [r['count'] for r in inventories] != [266, 149] or test_inventory(recheck)['count'] != 1:
        raise ValueError('regression inventory differs')
    if sum(c.attrib.get('classname', '').endswith('test_standby_interlocks') for c in ET.parse(TESTS[0]).getroot().iter('testcase')) != 31:
        raise ValueError('new interlock test inventory differs')
    old = Path('reports/automatic-return-delivery-final-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    wheel = Path('reports/standby-interlocks-wheel-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(old.read_bytes()) != '6785400aeff5ff2ebbaade98090f2897905e7677749314f41eafbb36c1cd87fb':
        raise ValueError('previous wheel differs')
    with ZipFile(old) as x, ZipFile(wheel) as y:
        before = {p: x.read(p) for p in x.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
        after = {p: y.read(p) for p in y.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed = sorted(p for p in before if before[p] != after.get(p))
    if changed != ['power_grid_env/network_automation.py'] or before.keys() != after.keys() or any(Path(p).read_bytes() != v for p, v in after.items()):
        raise ValueError('runtime boundary differs')
    cli = Path('reports/standby-interlocks-cli-2026-11-16.json')
    trace = read_json(cli)
    if not trace['completed'] or trace['code_sha256'] != fp or len([e for e in trace['event_log'] if e.get('synchronism', {}).get('reason') == 'generator_thermal_latch']) != 2:
        raise ValueError('CLI thermal retry differs')
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    for p in [wheel, cli, recheck, *TESTS]:
        shutil.copyfile(p, root/p.name)
    (root/'old-runtime').mkdir()
    shutil.copyfile(old, root/'old-runtime'/old.name)
    sources = ({Path(p) for p in producers()} |
               set(Path('benchmarks').glob('standby_supply*.py')) |
               set(Path('cases/standby-interlocks').glob('*')) |
               {Path('tests/test_standby_interlocks.py'), Path('benchmarks/rms_equilibrium_installed_replay.py'),
                Path('plans/CURRENT_STATUS.md'), Path('plans/agent-designer.yaml'), Path('README.md'), Path('README.en.md')})
    for p in sources:
        dest = root/'sources'/p
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    result = dict(passed=True, code_sha256=fp, wheel_sha256=digest(wheel.read_bytes()),
                  previous_wheel_sha256=digest(old.read_bytes()), changed_runtime_files=changed,
                  evidence_manifests=anchors, test_inventories=inventories,
                  targeted_tests=415, new_interlock_tests=31, supplemental_owner_recheck=test_inventory(recheck),
                  installed_checks=96, independent_host=False, authenticated=False,
                  normative_approval=False, scope=q['scope'], native_actions=actions,
                  independent_reference_replays=40, recomputed_comparisons=160,
                  native_energy_integrals=80, permutation_pairs=16,
                  reference_clock_segments=a['reference_clock_segments'],
                  maximum_clock_error=a['maximum_clock_error'],
                  maximum_native_energy_error_j=a['maximum_native_energy_error_j'],
                  maximum_reference_energy_error_j=a['maximum_reference_energy_error_j'])
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
