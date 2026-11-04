"""Read-only GitHub release provenance verifier. Requires trusted source and modern gh.

Only live API responses plus freshly cryptographically verified attestations may
open the remote-host gate. Downloaded Python, shell and verify.py are never run.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import tempfile
from benchmarks.release_archive import PAYLOAD, unpack
from benchmarks.release_candidate import EXCLUDED, ROOT_FILES, SOURCE_ROOTS, source_hashes, validate_installation
from benchmarks.release_verify import MAX_TOTAL, canonical, digest, safe_name, strict_json, verify

WORKFLOW = '.github/workflows/release-candidate.yml'
MANDATORY_STEPS = (
    'Prepare hash-locked runtime and test environment',
    'Build twice and replay in two source-independent offline installations',
    'Freeze source, wheels, notices, profile and negative-case inventory',
    'Collect the entire suite and execute every required profile test',
    'Bind source and complete test inventory to candidate identity',
    'Assemble immutable attestation subject',
    'Sign release provenance on an explicit release run',
    'Publish signed release payload',
)
DIAGNOSTIC_STEP = 'Retain failure diagnostics separately from a qualifying payload'


def expected(repo, commit, run_id, attempt, artifact_id):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or any(s in ('.', '..') for s in repo.split('/')):
        raise ValueError('invalid GitHub repository')
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('a full lowercase commit SHA is required')
    if any(type(x) is not int or x <= 0 for x in (run_id, attempt, artifact_id)):
        raise ValueError('positive run/attempt/artifact IDs required')
    return {'repo': repo, 'commit': commit, 'run_id': run_id, 'attempt': attempt, 'artifact_id': artifact_id}


def metadata_contract(e, run, jobs, artifact):
    """Pure contract check; this function does not authenticate supplied dictionaries."""
    def require(ok, why):
        if not ok: raise ValueError(why)
    require(run.get('id') == e['run_id'] and run.get('run_attempt') == e['attempt'], 'run/attempt mismatch')
    require(run.get('head_sha') == e['commit'] and run.get('path') == WORKFLOW, 'commit/workflow mismatch')
    require(run.get('status') == 'completed' and run.get('conclusion') == 'success', 'run not successfully completed')
    require(run.get('event') == 'workflow_dispatch', 'only explicit workflow_dispatch qualifies; PR merge checks are separate')
    repository = run.get('repository', {}); head = run.get('head_repository', {})
    require(repository.get('full_name') == e['repo'] and head.get('full_name') == e['repo'] and type(repository.get('id')) is int and repository['id'] > 0 and head.get('id') == repository['id'], 'repository/fork mismatch')
    require(isinstance(jobs.get('jobs'), list) and jobs.get('total_count') == len(jobs['jobs']) == 1, 'complete single-job inventory required')
    job = jobs['jobs'][0]
    require(job.get('name') == 'candidate' and job.get('run_id') == e['run_id'] and job.get('run_attempt') == e['attempt'] and job.get('head_sha') == e['commit'], 'job identity mismatch')
    require(job.get('status') == 'completed' and job.get('conclusion') == 'success', 'job not successfully completed')
    steps = job.get('steps', [])
    require(bool(steps) and all(s.get('status') == 'completed' and (s.get('conclusion') == 'success' or (s.get('name') == DIAGNOSTIC_STEP and s.get('conclusion') == 'skipped')) for s in steps), 'incomplete/skipped/failed job step')
    for name in MANDATORY_STEPS:
        matches = [s for s in steps if s.get('name') == name]
        require(len(matches) == 1 and matches[0].get('conclusion') == 'success', 'missing/duplicate required build step: ' + name)
    require(artifact.get('id') == e['artifact_id'] and artifact.get('expired') is False, 'wrong or expired artifact')
    require(artifact.get('name') == f"ex04-linux-x86_64-py312-{e['commit']}-{e['attempt']}", 'artifact name/attempt mismatch')
    arun = artifact.get('workflow_run', {})
    require(arun.get('id') == e['run_id'] and arun.get('head_sha') == e['commit'] and arun.get('repository_id') == repository['id'] and arun.get('head_repository_id') == repository['id'], 'artifact workflow/repository binding mismatch')
    require(type(artifact.get('size_in_bytes')) is int and 0 < artifact['size_in_bytes'] <= MAX_TOTAL, 'artifact size budget')
    require(bool(re.fullmatch('sha256:[0-9a-f]{64}', str(artifact.get('digest')))), 'missing artifact SHA-256 digest')
    def time(s):
        t = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if t.utcoffset() is None: raise ValueError('timezone required')
        return t
    require(time(job['started_at']) <= time(artifact['created_at']) <= time(job['completed_at']), 'artifact created outside successful job interval')
    require(bool(re.fullmatch(r'[A-Za-z0-9_./-]+', str(run.get('head_branch', '')))), 'unsupported branch')
    return {'repository_id': repository['id'], 'job_id': job['id'], 'ref': 'refs/heads/' + run['head_branch']}


def signature_contract(e, bindings, verified, payload_sha256):
    """Inspect freshly verified gh output; arbitrary JSON is NOT a verification API."""
    if not isinstance(verified, list) or not verified:
        raise ValueError('no verified attestation')
    repo_uri = 'https://github.com/' + e['repo']
    signer = repo_uri + '/' + WORKFLOW + '@' + bindings['ref']
    required = {'issuer': 'https://token.actions.githubusercontent.com', 'runnerEnvironment': 'github-hosted',
                'sourceRepositoryURI': repo_uri, 'sourceRepositoryDigest': e['commit'],
                'sourceRepositoryIdentifier': str(bindings['repository_id']), 'sourceRepositoryRef': bindings['ref'],
                'buildSignerURI': signer, 'buildSignerDigest': e['commit'], 'buildTrigger': 'workflow_dispatch',
                'runInvocationURI': f"{repo_uri}/actions/runs/{e['run_id']}/attempts/{e['attempt']}"}
    for entry in verified:
        v = entry.get('verificationResult', {}); cert = v.get('signature', {}).get('certificate', {})
        statement = v.get('statement', {})
        if any(cert.get(k) != value for k, value in required.items()):
            continue
        if not v.get('verifiedTimestamps') or statement.get('predicateType') != 'https://slsa.dev/provenance/v1':
            continue
        if any(s.get('digest', {}).get('sha256') == payload_sha256 for s in statement.get('subject', [])):
            return {'signed_run_invocation': required['runInvocationURI'], 'signed_runner_environment': 'github-hosted'}
    raise ValueError('verified certificate does not bind this repository/source/workflow/run/attempt/host/payload')


def tree_contract(tree, source):
    """Bind all frozen source bytes and file names to the complete committed tree."""
    if tree.get('truncated') is not False or not isinstance(tree.get('tree'), list):
        raise ValueError('complete Git tree required')
    selected = {}; seen = set()
    for item in tree['tree']:
        name = safe_name(item['path'])
        if name in seen: raise ValueError('duplicate Git tree path')
        seen.add(name)
        parts = name.split('/')
        wanted = name in ROOT_FILES or (parts[0] in SOURCE_ROOTS and not any(p in EXCLUDED for p in parts) and not name.endswith('.pyc'))
        if not wanted or item.get('type') == 'tree': continue
        if item.get('type') != 'blob' or item.get('mode') not in ('100644', '100755'):
            raise ValueError('nonregular committed source')
        selected[name] = item
    local = source_hashes(Path(source))
    if set(selected) != set(local):
        raise ValueError('frozen/committed source file inventory differs')
    for name, item in selected.items():
        data = (Path(source) / name).read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\x00' + data).hexdigest()
        if item.get('sha') != blob or item.get('size') != len(data):
            raise ValueError('frozen source bytes differ from committed Git blob: ' + name)
    return digest(canonical(local))


def _command(gh, args, out, limit=16 * 1024 * 1024, timeout=120):
    env = os.environ.copy(); env.pop('GH_DEBUG', None)
    env.update(GH_HOST='github.com', GH_PROMPT_DISABLED='1', GH_PAGER='cat')
    def cap(): resource.setrlimit(resource.RLIMIT_FSIZE, (limit + 1, limit + 1))
    with Path(out).open('xb') as stream:
        try:
            r = subprocess.run([gh, *args], stdout=stream, stderr=subprocess.DEVNULL, env=env, timeout=timeout, preexec_fn=cap)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('GitHub verification command timed out') from exc
    if r.returncode or Path(out).stat().st_size > limit:
        raise RuntimeError('GitHub verification command failed; authentication, permissions, tool support or proof may be missing')


def verify_remote(repo, commit, run_id, attempt, artifact_id, source, out, gh='gh'):
    e = expected(repo, commit, run_id, attempt, 1 if artifact_id is None else artifact_id)
    source = Path(source).resolve(); out = Path(out).resolve()
    if out.exists() or out.is_relative_to(source) and out.parts[len(source.parts):len(source.parts)+1] != ('reports',):
        raise ValueError('new report directory outside frozen source roots required')
    trusted_source = source_hashes(source)
    out.mkdir(parents=True)
    try:
        gh = shutil.which(gh)
        if not gh: raise RuntimeError('trusted GitHub CLI executable required')
        _command(gh, ['attestation', 'verify', '--help'], out / 'gh-features.txt')
        help_text = (out / 'gh-features.txt').read_text()
        flags = ['--deny-self-hosted-runners', '--source-digest', '--signer-digest', '--signer-workflow', '--format', '--hostname']
        if any(f not in help_text for f in flags):
            raise RuntimeError('GitHub CLI lacks required attestation verification flags')
        _command(gh, ['--version'], out / 'gh-version.txt')
        base = f'repos/{repo}'
        def api(suffix, filename):
            p = out / filename
            _command(gh, ['api', '--hostname', 'github.com', '--method', 'GET', base + suffix], p)
            return strict_json(p)
        run = api(f'/actions/runs/{run_id}/attempts/{attempt}', 'run.json')
        jobs = api(f'/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100', 'jobs.json')
        if artifact_id is None:
            listing = api(f'/actions/runs/{run_id}/artifacts?per_page=100', 'artifact-list.json')
            entries = listing.get('artifacts', [])
            if listing.get('total_count') != len(entries) or len(entries) > 100:
                raise ValueError('complete bounded artifact list required')
            matches = [x for x in entries if x.get('name') == f'ex04-linux-x86_64-py312-{commit}-{attempt}']
            if len(matches) != 1:
                raise ValueError('exactly one release artifact for this attempt required')
            artifact_id = matches[0]['id']; e = expected(repo, commit, run_id, attempt, artifact_id)
        artifact = api(f'/actions/artifacts/{artifact_id}', 'artifact.json')
        bindings = metadata_contract(e, run, jobs, artifact)
        tree = api(f'/git/trees/{commit}?recursive=1', 'tree.json')
        source_identity = tree_contract(tree, source)
        outer = out / 'github-artifact.zip'
        _command(gh, ['api', '--hostname', 'github.com', '--method', 'GET', base + f'/actions/artifacts/{artifact_id}/zip'], outer, MAX_TOTAL, 300)
        if outer.stat().st_size != artifact['size_in_bytes'] or 'sha256:' + digest(outer.read_bytes()) != artifact['digest']:
            raise ValueError('downloaded artifact size/digest mismatch')
        transport = unpack(outer, out / 'transport', only_file=PAYLOAD)
        payload = transport / PAYLOAD; payload_hash = digest(payload.read_bytes())
        _command(gh, ['attestation', 'verify', str(payload), '--hostname', 'github.com', '--repo', repo,
                      '--signer-workflow', repo + '/' + WORKFLOW, '--source-digest', commit, '--signer-digest', commit,
                      '--deny-self-hosted-runners', '--format', 'json'], out / 'attestation-verification.json', timeout=180)
        signed = signature_contract(e, bindings, strict_json(out / 'attestation-verification.json'), payload_hash)
        extracted = unpack(payload, out / 'payload', allowed_prefixes={'candidate', 'evidence'})
        candidate = extracted / 'candidate'; evidence = extracted / 'evidence'
        if {p.name for p in evidence.iterdir()} != {'execution.json', 'test-inventory.json', 'tests.xml'}:
            raise ValueError('unexpected execution evidence inventory')
        checked = verify(candidate)
        m = strict_json(candidate / 'manifest.json')
        if source_hashes(candidate / 'source') != trusted_source or m['metadata'].get('source_inventory_sha256') != source_identity or m['metadata'].get('base_commit') != commit or m['metadata'].get('working_tree_clean') is not True:
            raise ValueError('candidate source/commit/clean checkout mismatch')
        installation, _ = validate_installation(candidate / 'installation', candidate / 'source')
        if installation['code_sha256'] != m['metadata']['code_sha256']:
            raise ValueError('candidate metadata differs from installed wheel code')
        from benchmarks.release_receipt import validate_tests
        profile = strict_json(candidate / 'profile.json')
        if profile != strict_json(source / 'requirements/release-balanced-dol-linux-py312.json'):
            raise ValueError('candidate profile differs from trusted source')
        tests = strict_json(evidence / 'test-inventory.json')
        counts = validate_tests(profile, tests, source_identity)
        receipt = strict_json(evidence / 'execution.json')
        if receipt.get('candidate_id') != checked['candidate_id'] or receipt.get('local_candidate_checks_passed') is not True or receipt.get('test_inventory_sha256') != digest((evidence / 'test-inventory.json').read_bytes()) or receipt.get('source_inventory_sha256') != source_identity or receipt.get('profile_sha256') != digest(canonical(profile)) or receipt.get('platform') != profile['platform'] or receipt.get('code_sha256') != m['metadata']['code_sha256'] or receipt.get('required_tests_passed') != counts['required'] or receipt.get('outside_profile_tests') != counts['outside']:
            raise ValueError('execution receipt/candidate/test inventory mismatch')
        if source_hashes(source) != trusted_source:
            raise ValueError('trusted local source changed during verification')
        result = {'schema': 'pge-remote-release-verification-v1', **e, **signed,
                  'candidate_id': checked['candidate_id'], 'source_inventory_sha256': source_identity,
                  'payload_sha256': payload_hash, 'artifact_sha256': artifact['digest'],
                  'platform': receipt['platform'], 'profile_sha256': digest(canonical(profile)),
                  'workflow_sha256': digest((source / WORKFLOW).read_bytes()),
                  'required_tests_passed': counts['required'], 'outside_profile_tests': counts['outside'],
                  'independent_host_verified': True, 'industrial_release_qualified': False,
                  'scope': 'Authenticated execution of the declared release profile, not universal numerical or site qualification'}
        (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
        return result
    except BaseException:
        (out / 'failure.json').write_text(json.dumps({'completed': False, 'independent_host_verified': False, 'industrial_release_qualified': False}) + '\n')
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'commit'): p.add_argument('--' + name, required=True)
    for name in ('run-id', 'attempt'): p.add_argument('--' + name, type=int, required=True)
    p.add_argument('--artifact-id', type=int)
    p.add_argument('--source', type=Path, required=True); p.add_argument('--out', type=Path, required=True); p.add_argument('--gh', default='gh')
    a = p.parse_args(); print(json.dumps(verify_remote(a.repo, a.commit, a.run_id, a.attempt, a.artifact_id, a.source, a.out, a.gh), indent=2))


if __name__ == '__main__': main()
