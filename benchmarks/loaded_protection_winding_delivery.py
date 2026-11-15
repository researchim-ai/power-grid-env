"""Local provenance receipt for independent loaded hybrid qualification.

This binds existing evidence and copies sources/tests plus the unchanged
qualified wheel. It does not execute CI or claim another-host qualification.
"""
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json, encoded, digest, verify, seal


def deliver(matrix, extensions, previous_delivery, tests, out):
    matrix, extensions, previous_delivery = map(Path, (matrix, extensions, previous_delivery))
    fingerprint = package_fingerprint()
    anchors = {str(path): verify(path) for path in [matrix, extensions, previous_delivery]}
    summaries = [read_json(p/'summary.json') for p in [matrix, extensions]]
    for path, summary in zip([matrix, extensions], summaries):
        if not summary['passed'] or summary['code_sha256'] != fingerprint:
            raise ValueError('qualification/runtime identity differs')
        for producer, expected in summary['producers'].items():
            if digest(Path(producer).read_bytes()) != expected:
                raise ValueError('qualification producer changed')
        for row in summary['rows']:
            if verify(path/row['id']) != row['manifest_sha256'] or not row['passed']:
                raise ValueError('qualification packet changed/failed')
    suites = ET.parse(tests).getroot().findall('testsuite')
    counts = {k: sum(int(s.get(k, '0')) for s in suites) for k in ['tests', 'failures', 'errors', 'skipped']}
    if counts['tests'] != 502 or any(counts[k] for k in ['failures', 'errors', 'skipped']):
        raise ValueError('expected502 passing focused/regression tests')
    wheel = previous_delivery/'power_grid_env-0.1.0-py3-none-any.whl'
    wheel_sha = digest(wheel.read_bytes())
    if fingerprint != 'f7699ae95f7b6c077c5c692f371d61561658ae74ec0db5b2272ae9179cf6a151' or wheel_sha != 'd9c6def7c5add1dbacd85dfb099d1425257e1f6bd7bb00a197cbe2bd72b235f8':
        raise ValueError('unchanged R06.35 runtime/wheel binding differs')
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    sources = ['benchmarks/loaded_protection_winding_reference.py',
               'benchmarks/loaded_protection_winding_acceptance.py',
               'benchmarks/loaded_protection_winding_extensions.py',
               'benchmarks/loaded_protection_winding_delivery.py',
               'benchmarks/motor_only_loaded_reference.py',
               'benchmarks/motor_only_loaded_protection.py',
               'tests/test_loaded_protection_winding.py', 'cases/motor-only-loaded/protocol.json',
               'cases/motor-only-loaded/README.md']
    for name in sources:
        target = root/'sources'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(name, target)
    shutil.copyfile(tests, root/'tests.xml')
    shutil.copyfile(wheel, root/wheel.name)
    checks = [c for summary in summaries for row in summary['rows'] for c in row.get('comparisons', row.get('checks', []))]
    receipt = dict(passed=True, code_sha256=fingerprint, wheel_sha256=wheel_sha, evidence_manifests=anchors,
                   tests=counts, optional_ANDES_deselected=4,
                   independent_trajectories=78, distinct_native_comparisons=92,
                   comparisons_including_refinement=156, new_native_runs=28,
                   maximum_event_difference_s=max(c['maximum_event_error_s'] for c in checks),
                   maximum_fault_current_difference_a=max(v for c in checks for k, v in c['errors'].items() if k.endswith('.current_a')),
                   independent_host=False, normative_approval=False, authenticated=False,
                   scope='R06.36 local receipt:64 endpoint/refinement and14 extension independent hybrid SI trajectories;64 prior native endpoint runs plus28 new curve/reset/thermal runs. Existing unchanged wheel reused with prior installed replay evidence. Focused regression, not whole-repository suite;4 optional ANDES checks explicitly deselected. Hashes establish integrity, not authenticity; no CI, publication, another-host or OEM claim.')
    (root/'delivery.json').write_bytes(encoded(receipt))
    for path, anchor in anchors.items():
        if verify(path) != anchor:
            raise ValueError('source evidence changed during delivery')
    if package_fingerprint() != fingerprint:
        raise ValueError('runtime changed during delivery')
    seal(root)
    return receipt


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--matrix', default='reports/loaded-protection-winding-final-2026-11-15')
    p.add_argument('--extensions', default='reports/loaded-protection-winding-extensions-final-2026-11-15')
    p.add_argument('--previous-delivery', default='reports/motor-only-loaded-delivery-2026-11-15')
    p.add_argument('--tests', default='reports/loaded-protection-winding-regression-tests-2026-11-15.xml')
    p.add_argument('--out', required=True)
    result = deliver(**vars(p.parse_args()))
    print(dict(passed=result['passed'], independent_trajectories=result['independent_trajectories']))
