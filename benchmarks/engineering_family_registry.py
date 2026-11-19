"""Freeze new topology families before generating descendants.

Novelty is relative to explicitly anchored historical corpora plus legacy
families, not a claim about every dataset or the foundation model's pretraining.
"""
import argparse
import hashlib
import json
import random
from pathlib import Path

from benchmarks import engineering_agent as legacy
from benchmarks.engineering_factory import public_family, write_json
from benchmarks.engineering_scenarios import family_pool, generate_scenarios
from power_grid_env.study_artifacts import encoded, read_json, seal, verify


FILES = ('engineering_family_registry.py', 'engineering_scenarios.py', 'engineering_factory.py', 'engineering_agent.py')


def sources():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in FILES}


def parents_key(parents):
    if (not isinstance(parents, list) or not 4 <= len(parents) <= 7 or type(parents[0]) is not int or parents[0] != -1 or
            any(type(p) is not int or not 0 <= p < i for i, p in enumerate(parents[1:], 1))):
        raise ValueError('qualified ordered rooted tree with4..7 nodes required')
    return legacy.family_key(parents)


def build(history, out, seed=20261119, counts=(4, 2, 2), pool_size=48):
    if (type(seed) is not int or not 0 <= seed < 2**31 or len(counts) != 3 or
            any(type(n) is not int or not 1 <= n <= 16 for n in counts) or
            type(pool_size) is not int or not sum(counts) <= pool_size <= 48):
        raise ValueError('bounded seed, positive three-way family counts and pool required')
    root = Path(out)
    if root.exists() or not history:
        raise ValueError('fresh registry and explicit history required')
    historical = {}
    anchors = []
    for item in history:
        path = Path(item['path'])
        if verify(path) != item['manifest_sha256']:
            raise ValueError('historical corpus manifest differs')
        summary = read_json(path / 'summary.json')
        if summary.get('schema') != 'power-grid-env.procedural-data/v1' or summary.get('admission_passed') is not True:
            raise ValueError('admitted procedural history required')
        rows = [json.loads(x) for x in (path / 'scenarios.jsonl').read_text().splitlines()]
        if len(rows) != summary['scenarios'] or not rows:
            raise ValueError('complete historical scenario inventory required')
        for row in rows:
            key = row['family_sha256']
            if public_family(row['requirements']) != key or legacy.family_key(row['parents']) != key:
                raise ValueError('historical declared topology differs')
            if row['split'] not in ('train', 'dev', 'test'):
                raise ValueError('historical split required')
            old = historical.setdefault(key, dict(split=row['split'], bases=set()))
            if old['split'] != row['split']:
                raise ValueError('historical family crosses splits')
            old['bases'].add(row['base_project_id'])
        anchors.append(dict(path=str(path.resolve()), manifest_sha256=item['manifest_sha256']))
    excluded = set(historical) | {legacy.family_key(value[1]) for value in legacy.FAMILIES.values()}
    pool = family_pool(seed, (pool_size, 0, 0))
    candidates = sorted((r for r in pool if r['family_sha256'] not in excluded), key=lambda r: r['family_sha256'])
    random.Random(seed).shuffle(candidates)
    if len(candidates) < sum(counts):
        raise ValueError('insufficient unseen families; do not silently recycle historical ones')
    assignments, cursor = [], 0
    for split, count in zip(('train', 'dev', 'test'), counts):
        for candidate in candidates[cursor:cursor+count]:
            key = parents_key(candidate['parents'])
            if key != candidate['family_sha256']:
                raise ValueError('candidate family signature differs')
            assignments.append(dict(family_sha256=key, split=split, parents=candidate['parents']))
        cursor += count
    summary = dict(schema='power-grid-env.new-family-registry/v1', seed=seed, counts=list(counts), pool_size=pool_size,
                   historical_sources=anchors, historical_families={k: dict(split=v['split'], bases=sorted(v['bases'])) for k, v in sorted(historical.items())},
                   excluded_family_sha256=sorted(excluded), assignments=assignments,
                   source_sha256=sources(), frozen_before_parameter_generation=True,
                   optimizer_run=False, scope='Novel relative to listed historical corpora and legacy families only; no pretraining novelty or learned generalization claim.')
    root.mkdir(parents=True)
    write_json(root / 'summary.json', summary)
    seal(root)
    return summary


def load(root, anchor):
    if verify(root) != anchor:
        raise ValueError('registry manifest differs')
    value = read_json(Path(root) / 'summary.json')
    if value.get('schema') != 'power-grid-env.new-family-registry/v1' or value.get('source_sha256') != sources():
        raise ValueError('registry implementation/schema differs')
    assignments = value['assignments']
    keys = [r['family_sha256'] for r in assignments]
    if len(set(keys)) != len(keys) or set(keys) & set(value['excluded_family_sha256']):
        raise ValueError('registry family reuse')
    for row in assignments:
        if parents_key(row['parents']) != row['family_sha256'] or row['split'] not in ('train', 'dev', 'test'):
            raise ValueError('invalid family assignment')
    if [sum(r['split'] == split for r in assignments) for split in ('train', 'dev', 'test')] != value['counts']:
        raise ValueError('registry split counts differ')
    return value


def scenarios(root, anchor, variants=2):
    registry = load(root, anchor)
    assignments = {r['family_sha256']: r for r in registry['assignments']}
    # The registry was sealed before this parameter factory is invoked. Its
    # internal provisional train tag is replaced before any example is exported.
    output = []
    for row in generate_scenarios(registry['seed'], variants, (registry['pool_size'], 0, 0)):
        if row['family_sha256'] not in assignments or row['kind'] != 'repair' or row['truth']['cause'] != 'ampacity':
            continue
        row['split'] = assignments[row['family_sha256']]['split']
        if public_family(row['requirements']) != row['family_sha256']:
            raise ValueError('generated topology differs from frozen assignment')
        output.append(row)
    if len(output) != len(assignments) * variants:
        raise ValueError('complete parameter variants required')
    return output


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--history', required=True, help='JSON list of path/manifest_sha256 objects')
    p.add_argument('--out', required=True)
    p.add_argument('--seed', type=int, default=20261119)
    a = p.parse_args()
    print(json.dumps(build(read_json(a.history), a.out, a.seed)))
