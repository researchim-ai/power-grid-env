"""Bind local execution to a sealed candidate. Runner environment is untrusted metadata."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
from benchmarks.release_candidate import source_hashes
from benchmarks.release_verify import canonical, digest, strict_json, verify


def validate_tests(profile, tests, identity):
    if tests.get('schema') != 'pge-test-inventory-v1' or tests.get('passed') is not True or tests.get('collection_errors') != [] or tests.get('pytest_exitstatus') != 0:
        raise ValueError('test inventory did not pass')
    if tests.get('profile_sha256') != digest(canonical(profile)):
        raise ValueError('test inventory belongs to a different profile')
    if tests.get('source_inventory_sha256') != identity:
        raise ValueError('test inventory belongs to different source')
    nodes = tests.get('tests', {}); files = set(profile['required_test_files'])
    required = {n: e for n, e in nodes.items() if n.split('::')[0] in files}
    outside = {n: e for n, e in nodes.items() if n.split('::')[0] not in files}
    if not required or {n.split('::')[0] for n in required} != files:
        raise ValueError('required test inventory incomplete')
    if tests.get('collected_count') != len(nodes) or tests.get('required_count') != len(required) or tests.get('outside_profile_count') != len(outside):
        raise ValueError('test inventory counts disagree')
    for e in required.values():
        phases = e.get('phases', {})
        if e.get('status') != 'passed' or set(phases) != {'setup', 'call', 'teardown'} or any(p.get('outcome') != 'passed' or p.get('xfail') is not None for p in phases.values()):
            raise ValueError('required test incomplete')
    if any(e.get('status') != 'outside_profile' or e.get('phases') for e in outside.values()):
        raise ValueError('outside-profile accounting inconsistent')
    return {'required': len(required), 'outside': len(outside)}


def create(candidate, test_inventory, source, out):
    candidate = Path(candidate); source = Path(source); out = Path(out)
    if out.exists() or out.absolute().is_relative_to(candidate.absolute()):
        raise ValueError('receipt must be new and outside sealed candidate')
    checked = verify(candidate)
    manifest = strict_json(candidate / 'manifest.json'); profile = strict_json(candidate / 'profile.json')
    tests = strict_json(test_inventory)
    identity = digest(canonical(source_hashes(source)))
    if manifest['metadata']['source_inventory_sha256'] != identity:
        raise ValueError('tested source differs from candidate')
    counts = validate_tests(profile, tests, identity)
    actual = {'system': platform.system(), 'machine': platform.machine(), 'python': '.'.join(map(str, sys.version_info[:2]))}
    if actual != profile['platform']:
        raise ValueError('receipt outside supported platform')
    packages = sorted(({'name': d.metadata['Name'], 'version': d.version} for d in importlib.metadata.distributions()), key=lambda x: (x['name'].lower(), x['version']))
    result = {'schema': 'pge-release-execution-v1', 'candidate_id': checked['candidate_id'],
              'code_sha256': manifest['metadata']['code_sha256'], 'source_inventory_sha256': identity,
              'test_inventory_sha256': digest(Path(test_inventory).read_bytes()),
              'profile_sha256': digest(canonical(profile)), 'platform': actual, 'python_patch': platform.python_version(),
              'test_environment_packages': packages, 'required_tests_passed': counts['required'],
              'outside_profile_tests': counts['outside'], 'all_project_tests_executed': counts['outside'] == 0,
              'runner_claims_untrusted': {k: os.environ.get(k) for k in ('GITHUB_ACTIONS', 'GITHUB_REPOSITORY', 'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'RUNNER_ENVIRONMENT', 'RUNNER_OS', 'RUNNER_ARCH')},
              'local_candidate_checks_passed': True, 'independent_host_verified': False,
              'industrial_release_qualified': False,
              'remote_gate': 'pending_authoritative_CI_run_and_artifact_binding'}
    out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, required=True); p.add_argument('--inventory', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True); p.add_argument('--out', type=Path, required=True)
    a = p.parse_args(); print(json.dumps(create(a.candidate, a.inventory, a.source, a.out), indent=2))


if __name__ == '__main__':
    main()
