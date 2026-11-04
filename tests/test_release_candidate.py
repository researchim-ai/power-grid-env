"""Tamper, bounded-path and honest test/host accounting for release candidates."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from benchmarks.release_verify import canonical, digest, inventory, safe_name, strict_json, verify
from benchmarks.release_candidate import source_hashes, validate_installation


def seal(root):
    m = {'schema': 'pge-release-candidate-v1', 'metadata': {'independent_host_verified': False}, 'files': inventory(root)}
    m['candidate_id'] = digest(canonical(m))
    (root / 'manifest.json').write_text(json.dumps(m))
    return m['candidate_id']


@pytest.mark.parametrize('damage', ['modify', 'remove', 'extra', 'symlink', 'identity', 'expected', 'manifest_link'])
def test_sealed_candidate_rejects_tampering(tmp_path, damage):
    (tmp_path / 'a').write_bytes(b'original'); identifier = seal(tmp_path)
    assert verify(tmp_path, identifier)['integrity_verified']
    if damage == 'modify': (tmp_path / 'a').write_bytes(b'changed')
    if damage == 'remove': (tmp_path / 'a').unlink()
    if damage == 'extra': (tmp_path / 'extra').write_bytes(b'unlisted')
    if damage == 'symlink': (tmp_path / 'link').symlink_to('a')
    if damage == 'identity':
        m = strict_json(tmp_path / 'manifest.json'); m['metadata']['forged'] = True
        (tmp_path / 'manifest.json').write_text(json.dumps(m))
    if damage == 'expected': identifier = '0' * 64
    if damage == 'manifest_link':
        (tmp_path / 'manifest.json').rename(tmp_path / 'hidden'); (tmp_path / 'manifest.json').symlink_to('hidden')
    with pytest.raises(ValueError): verify(tmp_path, identifier)


@pytest.mark.parametrize('name', ['../a', '/a', 'a/../b', 'a//b', './a', 'a\\b', 'C:a', '', 'a/'])
def test_unsafe_manifest_paths(name):
    with pytest.raises(ValueError): safe_name(name)


@pytest.mark.parametrize('text', ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_ambiguous_json_is_rejected(tmp_path, text):
    p = tmp_path / 'bad.json'; p.write_text(text)
    with pytest.raises(ValueError): strict_json(p)


def test_case_collisions_and_budget_are_rejected(tmp_path, monkeypatch):
    (tmp_path / 'A').write_bytes(b'12'); (tmp_path / 'a').write_bytes(b'12')
    with pytest.raises(ValueError, match='case-colliding'): inventory(tmp_path)
    (tmp_path / 'A').unlink()
    monkeypatch.setattr('benchmarks.release_verify.MAX_FILE', 1)
    with pytest.raises(ValueError, match='resource'): inventory(tmp_path)


def test_no_self_reported_environment_can_certify_independence(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true'); monkeypatch.setenv('RUNNER_ENVIRONMENT', 'github-hosted')
    (tmp_path / 'a').write_text('data'); seal(tmp_path)
    r = verify(tmp_path)
    assert r['authenticated'] is r['independent_host_verified'] is r['industrial_release_qualified'] is False


def test_source_closure_excludes_credentials_caches_and_reports(tmp_path):
    for name in ['power_grid_env/a.py', 'power_grid_env/__pycache__/a.pyc', '.aws/credentials', '.git/config', 'reports/x.json', 'tests/test_a.py']:
        p = tmp_path / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('x')
    assert set(source_hashes(tmp_path)) == {'power_grid_env/a.py', 'tests/test_a.py'}


def test_source_symlink_is_rejected(tmp_path):
    (tmp_path / 'tests').mkdir(); (tmp_path / 'tests/test_a.py').symlink_to('/etc/passwd')
    with pytest.raises(ValueError): source_hashes(tmp_path)


def test_incomplete_installation_cannot_be_sealed(tmp_path):
    (tmp_path / 'manifest.json').write_text('{}'); (tmp_path / 'orphan.log').write_text('x')
    with pytest.raises(ValueError, match='incomplete'): validate_installation(tmp_path, tmp_path)


@pytest.mark.parametrize('behavior,passed', [
    ('pass', True), ('skip', False), ('xfail', False), ('teardown', False),
    ('missing', False), ('collection', False), ('filter', False), ('partial', False)])
def test_inventory_subprocess_accounts_for_all_nodes(tmp_path, behavior, passed):
    tests = tmp_path / 'tests'; tests.mkdir()
    body = 'def test_required():\n    assert True\n'
    if behavior == 'skip': body = 'import pytest\ndef test_required():\n    pytest.skip("not executed")\n'
    if behavior == 'xfail': body = 'import pytest\n@pytest.mark.xfail\ndef test_required():\n    assert False\n'
    if behavior == 'teardown': body = 'import pytest\n@pytest.fixture\ndef bad():\n    yield\n    assert False\ndef test_required(bad):\n    pass\n'
    if behavior == 'collection': body = 'raise RuntimeError("collection failure")\n'
    (tests / 'test_required.py').write_text(body)
    (tests / 'test_outside.py').write_text('def test_outside():\n    raise AssertionError("must not run")\n')
    profile = tmp_path / 'profile.json'; profile.write_text(json.dumps({'required_test_files': ['tests/test_missing.py' if behavior == 'missing' else 'tests/test_required.py']}))
    out = tmp_path / 'result.json'
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy(); env['PYTHONPATH'] = str(repo); env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    args = [sys.executable, '-m', 'pytest', '-q', '-p', 'benchmarks.release_inventory', '--rootdir', str(tmp_path),
            'tests/test_required.py' if behavior == 'partial' else 'tests', '--release-profile', str(profile), '--release-inventory', str(out)]
    if behavior == 'filter': args += ['-k', 'nomatch']
    run = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=40)
    assert (run.returncode == 0) == passed, run.stdout + run.stderr
    if out.exists():
        r = strict_json(out); assert r['passed'] == passed
        if behavior not in ('collection',):
            assert r['collected_count'] == 2
            assert r['all_project_tests_executed'] is False
            assert r['tests']['tests/test_outside.py::test_outside']['status'] == 'outside_profile'


def test_overflowing_json_float_is_rejected(tmp_path):
    p = tmp_path / 'bad.json'; p.write_text('{"a":1e9999}')
    with pytest.raises(ValueError): strict_json(p)


def test_standalone_checker_does_not_need_project_imports(tmp_path):
    root = tmp_path / 'candidate'; root.mkdir(); (root / 'a').write_text('x'); identifier = seal(root)
    checker = Path(__file__).resolve().parents[1] / 'benchmarks/release_verify.py'
    run = subprocess.run([sys.executable, '-I', str(checker), str(root), '--expected-id', identifier], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)['candidate_id'] == identifier


@pytest.mark.parametrize('damage', [None, 'skip', 'counts', 'profile', 'source', 'missing_phase', 'missing_file', 'outside_passed'])
def test_execution_receipt_rejects_incomplete_or_mismatched_tests(tmp_path, monkeypatch, damage):
    from benchmarks.release_receipt import create
    root = tmp_path / 'source'; (root / 'tests').mkdir(parents=True)
    (root / 'tests/test_a.py').write_text('def test_a(): pass\n')
    profile = {'platform': {'system': 'Linux', 'machine': 'x86_64', 'python': '3.12'}, 'required_test_files': ['tests/test_a.py']}
    candidate = tmp_path / 'candidate'; candidate.mkdir()
    (candidate / 'profile.json').write_text(json.dumps(profile))
    m = {'schema': 'pge-release-candidate-v1', 'metadata': {'source_inventory_sha256': digest(canonical(source_hashes(root))), 'code_sha256': 'b' * 64}, 'files': inventory(candidate)}
    m['candidate_id'] = digest(canonical(m)); (candidate / 'manifest.json').write_text(json.dumps(m))
    phases = {k: {'outcome': 'passed', 'xfail': None} for k in ('setup', 'call', 'teardown')}
    report = {'schema': 'pge-test-inventory-v1', 'passed': True, 'pytest_exitstatus': 0, 'collection_errors': [],
              'profile_sha256': digest(canonical(profile)), 'source_inventory_sha256': digest(canonical(source_hashes(root))),
              'collected_count': 2, 'required_count': 1, 'outside_profile_count': 1,
              'tests': {'tests/test_a.py::test_a': {'status': 'passed', 'phases': phases},
                        'tests/test_b.py::test_b': {'status': 'outside_profile', 'phases': {}}}}
    if damage == 'skip': phases['call']['outcome'] = 'skipped'
    if damage == 'counts': report['collected_count'] = 3
    if damage == 'profile': report['profile_sha256'] = '0' * 64
    if damage == 'source': (root / 'tests/test_a.py').write_text('changed')
    if damage == 'missing_phase': phases.pop('teardown')
    if damage == 'missing_file': report['tests'].pop('tests/test_a.py::test_a')
    if damage == 'outside_passed': report['tests']['tests/test_b.py::test_b']['status'] = 'passed'
    path = tmp_path / 'inventory.json'; path.write_text(json.dumps(report))
    monkeypatch.setenv('GITHUB_ACTIONS', 'true'); monkeypatch.setenv('RUNNER_ENVIRONMENT', 'github-hosted')
    if damage:
        with pytest.raises(ValueError): create(candidate, path, root, tmp_path / 'receipt.json')
    else:
        r = create(candidate, path, root, tmp_path / 'receipt.json')
        assert r['local_candidate_checks_passed']
        assert r['independent_host_verified'] is r['industrial_release_qualified'] is False
        assert r['outside_profile_tests'] == 1 and not r['all_project_tests_executed']


@pytest.mark.parametrize('damage', [None, 'hash', 'version', 'path', 'symlink'])
def test_upstream_license_supplements_are_version_and_hash_bound(tmp_path, damage):
    from benchmarks.release_candidate import supplemental_notices
    p = tmp_path / 'docs/third-party-licenses/library-LICENSE'; p.parent.mkdir(parents=True); p.write_text('License text')
    entry = {'name': 'library', 'version': '1.0', 'path': p.relative_to(tmp_path).as_posix(), 'sha256': digest(p.read_bytes()), 'url': 'https://example.invalid/library/v1.0/LICENSE'}
    if damage == 'hash': entry['sha256'] = '0' * 64
    if damage == 'version': entry['version'] = '2.0'
    if damage == 'path': entry['path'] = '../outside'
    if damage == 'symlink':
        target = tmp_path / 'other'; p.rename(target); p.symlink_to(target)
    (tmp_path / 'requirements').mkdir()
    (tmp_path / 'requirements/release-license-supplements.json').write_text(json.dumps({'schema': 'pge-license-supplements-v1', 'files': [entry]}))
    if damage:
        with pytest.raises(ValueError): supplemental_notices(tmp_path, 'library', '1.0')
    else:
        assert supplemental_notices(tmp_path, 'library', '1.0')[0][0] == p
        assert supplemental_notices(tmp_path, 'other', '1.0') == []
