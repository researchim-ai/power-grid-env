"""Deterministic release payloads and bounded ZIP extraction; never execute payload code."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import stat
import zipfile
from benchmarks.release_verify import MAX_FILE, MAX_FILES, MAX_TOTAL, digest, inventory, safe_name, strict_json, verify

PAYLOAD = 'release-payload.zip'
EVIDENCE_FILES = ('execution.json', 'test-inventory.json', 'tests.xml')


def unpack(archive, out, *, allowed_prefixes=None, only_file=None):
    """Extract to a new directory, rejecting ambiguous paths before writing anything."""
    archive = Path(archive); out = Path(out)
    if out.exists():
        raise ValueError('extraction directory must not exist')
    if archive.stat().st_size > MAX_TOTAL:
        raise ValueError('archive exceeds compressed size limit')
    with zipfile.ZipFile(archive) as z:
        entries = z.infolist(); names = set(); folded = set(); total = 0
        if not entries or len(entries) > MAX_FILES:
            raise ValueError('invalid archive member count')
        for i in entries:
            name = safe_name(i.filename)
            if i.is_dir() or name in names or name.casefold() in folded:
                raise ValueError('duplicate, directory or case-colliding ZIP entry')
            mode = i.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG) or i.flag_bits & 1:
                raise ValueError('nonregular or encrypted ZIP entry')
            if only_file is not None and name != only_file:
                raise ValueError('unexpected outer artifact member')
            if allowed_prefixes is not None and name.split('/')[0] not in allowed_prefixes:
                raise ValueError('unexpected payload prefix')
            total += i.file_size
            limit = MAX_TOTAL if only_file is not None else MAX_FILE
            if i.file_size > limit or total > MAX_TOTAL or (i.file_size > 1024 * 1024 and i.file_size > 200 * max(1, i.compress_size)):
                raise ValueError('ZIP expansion budget exceeded')
            names.add(name); folded.add(name.casefold())
        prefixes = {}
        for name in names:
            for count in range(1, len(name.split('/')) + 1):
                prefix = '/'.join(name.split('/')[:count]); folded_prefix = prefix.casefold()
                if folded_prefix in prefixes and prefixes[folded_prefix] != prefix:
                    raise ValueError('case-colliding directory prefix')
                prefixes[folded_prefix] = prefix
        if any('/'.join(n.split('/')[:i]) in names for n in names for i in range(1, len(n.split('/')))):
            raise ValueError('file/directory prefix collision')
        out.mkdir(parents=True)
        try:
            for i in entries:
                p = out / i.filename; p.parent.mkdir(parents=True, exist_ok=True)
                with z.open(i) as src, p.open('xb') as dest:
                    remaining = i.file_size
                    while data := src.read(min(1024 * 1024, remaining + 1)):
                        remaining -= len(data)
                        if remaining < 0:
                            raise ValueError('ZIP member exceeds declared size')
                        dest.write(data)
                    if remaining:
                        raise ValueError('truncated ZIP member')
        except BaseException:
            shutil.rmtree(out)
            raise
    return out


def pack(candidate, evidence, out):
    candidate = Path(candidate); evidence = Path(evidence); out = Path(out)
    checked = verify(candidate)
    if out.exists() or out.absolute().is_relative_to(candidate.absolute()) or out.absolute().is_relative_to(evidence.absolute()):
        raise ValueError('payload output must be new and outside input directories')
    receipt = strict_json(evidence / 'execution.json')
    if receipt.get('candidate_id') != checked['candidate_id'] or receipt.get('local_candidate_checks_passed') is not True:
        raise ValueError('execution receipt does not bind candidate')
    if receipt.get('test_inventory_sha256') != digest((evidence / 'test-inventory.json').read_bytes()):
        raise ValueError('test inventory differs from execution receipt')
    before = inventory(candidate)
    paths = [(f'candidate/{p.relative_to(candidate).as_posix()}', p) for p in candidate.rglob('*') if p.is_file()]
    for name in EVIDENCE_FILES:
        p = evidence / name
        if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_FILE:
            raise ValueError('missing or unsafe execution evidence')
        paths.append(('evidence/' + name, p))
    evidence_hashes = {n: digest(p.read_bytes()) for n, p in paths if n.startswith('evidence/')}
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(out, 'x', compression=zipfile.ZIP_STORED) as z:
            for name, p in sorted(paths):
                i = zipfile.ZipInfo(safe_name(name), date_time=(1980, 1, 1, 0, 0, 0)); i.create_system = 3
                i.external_attr = (stat.S_IFREG | 0o644) << 16
                z.writestr(i, p.read_bytes())
        if inventory(candidate) != before or any(digest((evidence / n.split('/', 1)[1]).read_bytes()) != h for n, h in evidence_hashes.items()):
            raise ValueError('inputs changed during payload creation')
        verify(candidate, checked['candidate_id'])
        if out.stat().st_size > MAX_TOTAL:
            raise ValueError('payload size budget exceeded')
        return {'payload_sha256': digest(out.read_bytes()), 'candidate_id': checked['candidate_id'], 'authenticated': False}
    except BaseException:
        out.unlink(missing_ok=True)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, required=True); p.add_argument('--evidence', type=Path, required=True); p.add_argument('--out', type=Path, required=True)
    a = p.parse_args(); print(json.dumps(pack(a.candidate, a.evidence, a.out), indent=2))


if __name__ == '__main__':
    main()
