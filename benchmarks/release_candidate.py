"""Freeze a bounded source and offline-wheel candidate; never grant remote qualification."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile
from benchmarks.release_verify import canonical, digest, inventory, strict_json, verify

SOURCE_ROOTS = ('power_grid_env', 'benchmarks', 'tests', 'cases', 'docs', 'examples', 'requirements', 'plans', '.github')
ROOT_FILES = ('pyproject.toml', 'README.md', 'README.en.md', 'LICENSE', 'CONTRIBUTING.md', 'SECURITY.md', 'CODE_OF_CONDUCT.md', '.gitignore')
EXCLUDED = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'}


def source_files(repo):
    files = []
    for name in (*SOURCE_ROOTS, *ROOT_FILES):
        root = repo / name
        if root.is_symlink():
            raise ValueError('source symlink: ' + name)
        for p in sorted(root.rglob('*')) if root.is_dir() else [root]:
            if any(part in EXCLUDED for part in p.relative_to(repo).parts):
                continue
            if p.is_symlink():
                raise ValueError('source symlink: ' + str(p))
            if p.is_file() and p.suffix != '.pyc':
                files.append(p)
    return sorted(set(files))


def source_hashes(repo):
    return {p.relative_to(repo).as_posix(): digest(p.read_bytes()) for p in source_files(repo)}


def code_hash(files):
    h = hashlib.sha256()
    for name, data in sorted(files):
        h.update(name.encode()); h.update(data)
    return h.hexdigest()


def validate_installation(installation, repo):
    # Historical evidence is admissible only if its complete hashed file inventory is intact.
    recorded = strict_json(installation / 'manifest.json')
    actual = {k: v['sha256'] for k, v in inventory(installation).items()}
    if recorded != actual:
        raise ValueError('installation evidence is incomplete or changed')
    q = strict_json(installation / 'qualification.json')
    required = ('repeat_build_same_sha256', 'offline_hash_checked_install', 'pip_check_passed',
                'repository_independent_import', 'motor_replay_passed', 'opendss_smoke_passed', 'synthetic_trace_comparison_passed')
    if any(q.get(k) is not True for k in required) or q.get('clean_venvs') != 2:
        raise ValueError('installation checks did not all pass')
    if q.get('platform', {}).get('system') != 'Linux' or q['platform'].get('machine') != 'x86_64' or not q['platform'].get('python', '').startswith('3.12.'):
        raise ValueError('installation outside candidate platform')
    if digest((installation / 'install-requirements.lock').read_bytes()) != q.get('install_lock_sha256'):
        raise ValueError('installation lock identity mismatch')
    package = repo / 'power_grid_env'
    fp = code_hash((p.name, p.read_bytes()) for p in package.glob('*.py'))
    wheel = installation / q['wheel_filename']
    if wheel.parent != installation or wheel.suffix != '.whl' or digest(wheel.read_bytes()) != q['wheel_sha256']:
        raise ValueError('invalid project wheel')
    with zipfile.ZipFile(wheel) as z:
        entries = [(Path(n).name, z.read(n)) for n in z.namelist() if n.startswith('power_grid_env/') and n.count('/') == 1 and n.endswith('.py')]
    if fp != q['code_sha256'] or code_hash(entries) != fp:
        raise ValueError('wheel/source/installation code differs')
    # Match all current build inputs, not only executable Python (assets matter too).
    inputs = strict_json(installation / 'build-inputs.json')
    current = {p.relative_to(repo).as_posix(): digest(p.read_bytes()) for p in package.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    current.update({n: digest((repo / n).read_bytes()) for n in ('pyproject.toml', 'README.md', 'LICENSE')})
    if inputs != current or digest((repo / 'requirements/pilot-linux-py312.lock').read_bytes()) != q['runtime_lock_sha256']:
        raise ValueError('stale build inputs or runtime lock; rebuild installation evidence')
    return q, wheel


def supplemental_notices(repo, name, version):
    """Version-specific upstream texts missing from the pinned binary wheel."""
    from benchmarks.release_verify import safe_name
    spec = strict_json(repo / 'requirements/release-license-supplements.json')
    if spec.get('schema') != 'pge-license-supplements-v1':
        raise ValueError('unsupported license supplement schema')
    entries = []
    for item in spec['files']:
        if item['name'] != name:
            continue
        if item['version'] != version:
            raise ValueError('license supplement version differs from locked wheel')
        path = repo / safe_name(item['path'])
        if not item['path'].startswith('docs/third-party-licenses/') or path.is_symlink() or digest(path.read_bytes()) != item['sha256']:
            raise ValueError('license supplement path/hash mismatch')
        entries.append((path, item))
    return entries


def create(repo, out, installation, wheelhouse):
    repo = Path(repo).resolve(); out = Path(out).absolute(); installation = Path(installation).resolve(); wheelhouse = Path(wheelhouse).resolve()
    if out.exists():
        raise ValueError('output must not exist')
    if any(out.is_relative_to(repo / n) for n in SOURCE_ROOTS):
        raise ValueError('output must be outside source roots')
    q, wheel = validate_installation(installation, repo)
    before = source_hashes(repo)
    profile = strict_json(repo / 'requirements/release-balanced-dol-linux-py312.json')
    out.mkdir(parents=True)
    try:
        for name in before:
            dest = out / 'source' / name; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo / name, dest)
        shutil.copyfile(repo / 'benchmarks/release_verify.py', out / 'verify.py')
        shutil.copyfile(repo / 'requirements/release-balanced-dol-linux-py312.json', out / 'profile.json')
        shutil.copytree(installation, out / 'installation')
        lock = (installation / 'install-requirements.lock').read_text()
        (out / 'install.lock').write_text(lock)
        wheels = out / 'wheelhouse'; wheels.mkdir()
        licenses = out / 'licenses'; licenses.mkdir()
        wheel_entries = []
        for line in lock.splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            import re
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^ ]+) --hash=sha256:([0-9a-f]{64})', line)
            if not match:
                raise ValueError('only exact single-hash wheel lock lines supported')
            name, version, expected = match.groups()
            candidates = [p for p in wheelhouse.glob('*.whl') if digest(p.read_bytes()) == expected]
            if digest(wheel.read_bytes()) == expected:
                candidates.append(wheel)
            if not candidates:
                raise ValueError('missing hash-locked wheel: ' + name)
            p = candidates[0]; shutil.copyfile(p, wheels / p.name)
            notices = []
            with zipfile.ZipFile(p) as z:
                for info in z.infolist():
                    n = info.filename
                    if '.dist-info/' not in n or info.is_dir():
                        continue
                    tail = n.split('.dist-info/', 1)[1]
                    if tail != 'METADATA' and not any(x in tail.lower() for x in ('license', 'notice', 'copying')):
                        continue
                    from benchmarks.release_verify import safe_name
                    safe_name(tail)
                    if info.file_size > 8 * 1024 * 1024:
                        raise ValueError('oversized wheel notice')
                    dest = licenses / p.name / tail; dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(info)); notices.append(dest.relative_to(out).as_posix())
            supplements = []
            for path, item in supplemental_notices(repo, name, version):
                dest = licenses / p.name / 'upstream' / path.name
                dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(path, dest)
                notices.append(dest.relative_to(out).as_posix()); supplements.append(item)
            if not notices or all(n.endswith('/METADATA') for n in notices):
                raise ValueError('wheel lacks a license text; add a version-pinned upstream supplement')
            wheel_entries.append({'name': name, 'version': version, 'sha256': expected, 'filename': p.name,
                                  'retained_metadata_and_notices': notices, 'upstream_supplements': supplements,
                                  'legal_redistribution_review': 'not_asserted'})
        (out / 'dependency-inventory.json').write_text(json.dumps(wheel_entries, indent=2) + '\n')
        copied = {name: digest((out / 'source' / name).read_bytes()) for name in before}
        if copied != before:
            raise ValueError('frozen source copy differs from captured inventory')
        if source_hashes(repo) != before:
            raise ValueError('source changed during freezing')
        def git(*args):
            r = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
            return r.stdout.strip() if r.returncode == 0 else None
        metadata = {'profile_id': profile['profile_id'], 'code_sha256': q['code_sha256'],
                    'source_inventory_sha256': digest(canonical(before)), 'base_commit': git('rev-parse', 'HEAD'),
                    'working_tree_clean': git('status', '--porcelain', '--untracked-files=normal') == '',
                    'integrity_is_not_authentication': True,
                    'independent_host_verified': False, 'industrial_release_qualified': False}
        manifest = {'schema': 'pge-release-candidate-v1', 'metadata': metadata, 'files': inventory(out)}
        manifest['candidate_id'] = digest(canonical(manifest))
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        return verify(out)
    except BaseException:
        # Retain failed material as an explicitly unsealed diagnostic, never a candidate.
        (out / 'manifest.json').unlink(missing_ok=True)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True); p.add_argument('--installation', type=Path, required=True)
    p.add_argument('--wheelhouse', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(create(Path(__file__).resolve().parents[1], args.out, args.installation, args.wheelhouse), indent=2))


if __name__ == '__main__':
    main()
