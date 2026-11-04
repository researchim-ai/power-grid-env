"""Standalone, offline integrity checker. Hash integrity is not authentication.

Copy this file beside manifest.json and run: python verify.py CANDIDATE.
The trusted expected identifier must come from a separate trusted channel.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re

MAX_FILE = 128 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_FILES = 20000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def strict_json(path):
    path = Path(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('JSON exceeds size limit')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError('nonfinite JSON: ' + value)
    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('nonfinite JSON number')
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs, parse_constant=constant, parse_float=number)


def safe_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise ValueError('unsafe path')
    p = PurePosixPath(name)
    if p.is_absolute() or any(x in ('', '.', '..') for x in name.split('/')) or p.as_posix() != name:
        raise ValueError('unsafe path: ' + name)
    return name


def inventory(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('candidate must be a real directory')
    result = {}; folded = set(); total = 0
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise ValueError('symlinks are forbidden')
        if p.is_dir():
            continue
        if not p.is_file():
            raise ValueError('nonregular entry')
        name = safe_name(p.relative_to(root).as_posix())
        if name == 'manifest.json':
            continue
        if name.casefold() in folded:
            raise ValueError('case-colliding paths')
        folded.add(name.casefold())
        size = p.stat().st_size; total += size
        if size > MAX_FILE or total > MAX_TOTAL or len(result) >= MAX_FILES:
            raise ValueError('candidate exceeds resource limits')
        result[name] = {'size': size, 'sha256': digest(p.read_bytes())}
    return result


def verify(root, expected_id=None):
    root = Path(root)
    if root.is_symlink() or (root / 'manifest.json').is_symlink():
        raise ValueError('symlinks are forbidden')
    manifest = strict_json(root / 'manifest.json')
    if set(manifest) != {'schema', 'metadata', 'files', 'candidate_id'} or manifest['schema'] != 'pge-release-candidate-v1':
        raise ValueError('unsupported manifest contract')
    if not isinstance(manifest['files'], dict) or not manifest['files']:
        raise ValueError('empty file inventory')
    for name, entry in manifest['files'].items():
        safe_name(name)
        if not isinstance(entry, dict) or set(entry) != {'size', 'sha256'} or type(entry['size']) is not int or entry['size'] < 0 or not re.fullmatch('[0-9a-f]{64}', str(entry['sha256'])):
            raise ValueError('invalid file entry')
    identifier = digest(canonical({k: manifest[k] for k in ('schema', 'metadata', 'files')}))
    if manifest['candidate_id'] != identifier or (expected_id is not None and identifier != expected_id):
        raise ValueError('candidate identity mismatch')
    if inventory(root) != manifest['files']:
        raise ValueError('missing, modified or unlisted candidate files')
    return {'integrity_verified': True, 'candidate_id': identifier, 'file_count': len(manifest['files']),
            'authenticated': False, 'independent_host_verified': False, 'industrial_release_qualified': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate', type=Path); p.add_argument('--expected-id')
    args = p.parse_args()
    print(json.dumps(verify(args.candidate, args.expected_id), indent=2))


if __name__ == '__main__':
    main()
