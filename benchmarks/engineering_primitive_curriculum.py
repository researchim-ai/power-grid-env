"""Family-frozen primitive skills: public state, API effects and exact comparisons.

Analytic comparisons are labelled as arithmetic exercises, not new PF results.
Held-out answers live in an owner-only file and never in prompt-only exports.
"""
import argparse
import copy
import hashlib
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from benchmarks.engineering_catalog_challenges import generate
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_family_registry import load as load_registry, scenarios
from benchmarks.engineering_reference import compare_reference
from benchmarks.engineering_sft_contract_v2 import ExactEpisode
from benchmarks.engineering_sft_release_v2 import checked_study, sha
from power_grid_env.study_artifacts import read_json, seal, verify


FILES = ('engineering_primitive_curriculum.py', 'engineering_family_registry.py',
         'engineering_sft_contract_v2.py', 'engineering_catalog_challenges.py', 'engineering_reference.py')
UNITS = {'A': ('current', Decimal(1)), 'kA': ('current', Decimal(1000)),
         'V': ('voltage', Decimal(1)), 'kV': ('voltage', Decimal(1000)),
         'pu': ('relative_voltage', Decimal(1)), '%voltage': ('relative_voltage', Decimal('.01')),
         '%loading': ('loading', Decimal(1))}


def sources():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in FILES}


def decimal_value(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError('bounded exact decimal string required')
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('invalid decimal') from exc
    if not number.is_finite() or abs(number) > Decimal('1e12') or number.as_tuple().exponent < -12:
        raise ValueError('finite bounded precision required')
    return number


def comparison(problem):
    if problem['direction'] not in ('maximum', 'minimum'):
        raise ValueError('explicit limit direction required')
    limit = problem['limit']
    if limit['unit'] not in UNITS:
        raise ValueError('unsupported unit')
    threshold = decimal_value(limit['value']) * UNITS[limit['unit']][1]
    observed = problem['observed']
    if observed is None:
        return dict(computable=False, relation='unknown', violation=None)
    if observed['unit'] not in UNITS or UNITS[observed['unit']][0] != UNITS[limit['unit']][0]:
        raise ValueError('incompatible units')
    value = decimal_value(observed['value']) * UNITS[observed['unit']][1]
    relation = 'below' if value < threshold else 'above' if value > threshold else 'equal'
    return dict(computable=True, relation=relation,
                violation=bool(value > threshold if problem['direction'] == 'maximum' else value < threshold))


def messages(skill, problem, reverse=False):
    instructions = {
        'threshold': 'Compare observed with limit using exact decimals and units. Return computable(boolean), relation(below/equal/above/unknown), violation(boolean or null). Equality never violates the limit. Missing observed means unknown/null. Multiply a numeric kA value by1000 to obtain A, likewise kV to V; divide a numeric %voltage value by100 to obtain pu. %loading is a separate dimension.',
        'state_reading': 'Read only public_state. Return exactly revision, validated and has_fresh_measurements. Reading a flag is not electrical certification.',
        'tool_effect': 'Predict the effect of the given tool call using before, installed_line_ids, tool_schema and tool_effect. Return accepted, revision_delta, fresh_after, simulations_spent, finished. A rejected call changes neither project revision nor fresh measurements. Assume study execution completes successfully when arguments and budgets are valid. This is an API-effect exercise, not a convergence forecast or a request to choose an action.',
    }
    if skill not in instructions:
        raise ValueError('unknown primitive skill')
    public = copy.deepcopy(problem)
    if reverse:
        public = dict(reversed(list(public.items())))
    return [dict(role='system', content='ENGINEERING-PRIMITIVE/V1. ' + instructions[skill] + ' Return JSON only.'),
            dict(role='user', content=json.dumps(public, separators=(',', ':'), allow_nan=False))]


def row(case, skill, group, member, problem, answer, evidence):
    for reverse in (False, True):
        m = messages(skill, problem, reverse)
        value = dict(split=case['split'], family_sha256=case['family_sha256'], base_project_id=case['base_project_id'],
                     task_id=case['id'], skill=skill, pair_group=sha([case['base_project_id'], skill, group]),
                     member=member, nuisance_variant='reordered_keys' if reverse else 'original',
                     messages=m, answer=answer, evidence=evidence, input_sha256=sha(m), modality='text')
        value['id'] = sha(value)
        yield value


def state(card):
    return {k: card[k] for k in ('revision', 'validated', 'has_fresh_measurements')}


def context(card):
    return dict(source_parameters=card['scene']['source_parameters'],
                nominal_ll_v=card['scene']['nominal_ll_v'], node_count=len(card['scene']['nodes']))


def case_rows(case):
    rows = []
    initial = ExactEpisode(case)
    current = initial
    snapshots = [current.observe()[0]]
    current.apply(dict(name='validate', arguments={}))
    snapshots.append(current.observe()[0])
    current.apply(dict(name='simulate', arguments={}))
    snapshots.append(current.observe()[0])
    compare_reference(current.engine.state['project'], current.engine.state['last_result'])
    fresh = copy.deepcopy(current)
    card = current.observe()[0]
    action = dict(name='set_line_catalog', arguments=dict(equipment_id=card['scene']['lines'][0]['id'], catalog_id=card['scene']['catalog'][0]['id']))
    current.apply(action)
    snapshots.append(current.observe()[0])
    for i, snapshot in enumerate(snapshots):
        problem = dict(public_state=state(snapshot), network=context(snapshot), scope='Exact public API flags only; not a full network observation.')
        rows.extend(row(case, 'state_reading', 'revision_freshness', str(i), problem, state(snapshot), 'public_snapshot'))
    fresh_card = fresh.observe()[0]
    effect_calls = [
        (ExactEpisode(case), dict(name='validate', arguments={}), 'initial_validate'),
        (ExactEpisode(case), dict(name='simulate', arguments={}), 'initial_simulate'),
        (copy.deepcopy(fresh), dict(name='validate', arguments={}), 'fresh_validate'),
        (copy.deepcopy(fresh), action, 'fresh_mutation'),
        (copy.deepcopy(fresh), dict(name='set_line_catalog', arguments=dict(equipment_id=fresh_card['scene']['lines'][0]['id'], catalog_id='not-an-allowed-part')), 'rejected_mutation'),
        (copy.deepcopy(fresh), dict(name='inspect_project', arguments={}), 'fresh_inspection'),
    ]
    for episode, action, member in effect_calls:
        before = episode.observe()[0]
        problem = dict(before=dict(state(before), calls_remaining=before['calls_remaining'], simulations_remaining=before['simulations_remaining']), network=context(before),
                       installed_line_ids=[line['id'] for line in before['scene']['lines']],
                       action=action, tool_schema=next(t['function']['parameters'] for t in before['tools'] if t['function']['name'] == action['name']),
                       tool_effect=before['tool_effects'][action['name']])
        reply, _ = episode.apply(action)
        after = episode.observe()[0]
        answer = dict(accepted=reply.get('ok') is True, revision_delta=after['revision']-before['revision'],
                      fresh_after=after['has_fresh_measurements'], simulations_spent=before['simulations_remaining']-after['simulations_remaining'],
                      finished=episode.engine.state['finished'])
        if action['name'] == 'simulate' and reply.get('ok'):
            compare_reference(episode.engine.state['project'], episode.engine.state['last_result'])
        rows.extend(row(case, 'tool_effect', 'api_effects', member, problem, answer, 'executed_owner_engine_transition'))
    # Numeric exercises vary independently of the physical passports. Their
    # family/base lineage controls splitting, not a claim of physical simulation.
    seed = int(case['base_project_id'][:12], 16)
    for index, (unit, other, direction) in enumerate((('A', 'kA', 'maximum'), ('V', 'kV', 'minimum'),
                                                    ('%loading', '%loading', 'maximum'), ('pu', '%voltage', 'minimum'))):
        limit = Decimal(20000 + (seed + index*7919) % 150000) / Decimal(1000)
        if unit == 'pu':
            limit /= 100
        quantum = Decimal('.00001') if unit == 'pu' else Decimal('.001')
        for member, offset in (('below', -quantum), ('equal', Decimal(0)), ('above', quantum), ('missing', None)):
            # Alternate units inside each semantic pair without changing the
            # underlying value. Equality is exact, including 1A == .001kA.
            target_unit = other if (seed + index) % 2 else unit
            observed = None if offset is None else dict(value=str((limit+offset)*UNITS[unit][1]/UNITS[target_unit][1]), unit=target_unit)
            problem = dict(observed=observed, limit=dict(value=str(limit), unit=unit), direction=direction,
                           scope='Analytic decimal comparison; not a changed network or a solved power-flow result.')
            rows.extend(row(case, 'threshold', str(index), member, problem, comparison(problem), 'analytic_microtask_not_power_flow'))
    return rows


def validate_rows(rows, registry):
    assignments = {r['family_sha256']: r['split'] for r in registry['assignments']}
    ids, inputs, bases, pairs = set(), {}, {}, {}
    for r in rows:
        if r['id'] != sha({k: v for k, v in r.items() if k != 'id'}) or r['id'] in ids:
            raise ValueError('primitive identity differs or duplicates')
        ids.add(r['id'])
        if assignments.get(r['family_sha256']) != r['split']:
            raise ValueError('primitive split differs from registry')
        if bases.setdefault(r['base_project_id'], (r['split'], r['family_sha256'])) != (r['split'], r['family_sha256']):
            raise ValueError('base lineage crosses split/family')
        if r['input_sha256'] != sha(r['messages']):
            raise ValueError('public primitive input differs')
        if r['input_sha256'] in inputs:
            raise ValueError('duplicate primitive input; do not inflate counts')
        inputs[r['input_sha256']] = r['split']
        problem = json.loads(r['messages'][1]['content'])
        expected = comparison(problem) if r['skill'] == 'threshold' else problem['public_state'] if r['skill'] == 'state_reading' else None
        if expected is not None and sha(expected) != sha(r['answer']):
            raise ValueError('primitive factual label differs')
        key = (r['pair_group'], r['member'])
        pair = pairs.setdefault(key, [])
        pair.append(r)
    for pair in pairs.values():
        if len(pair) != 2 or {r['nuisance_variant'] for r in pair} != {'original', 'reordered_keys'} or len({sha(r['answer']) for r in pair}) != 1:
            raise ValueError('nuisance pair incomplete or target changed')
        if len({sha(json.loads(r['messages'][1]['content'])) for r in pair}) != 1:
            raise ValueError('nuisance transformation changed meaning')
    threshold_groups = {}
    for r in rows:
        if r['skill'] == 'threshold' and r['nuisance_variant'] == 'original':
            threshold_groups.setdefault(r['pair_group'], {})[r['member']] = r['answer']
    for group in threshold_groups.values():
        if set(group) != {'below', 'equal', 'above', 'missing'} or len({sha(a) for a in group.values()}) != 4:
            raise ValueError('semantic boundary group incomplete')
    if set(assignments) != {r['family_sha256'] for r in rows}:
        raise ValueError('missing frozen family coverage')
    return dict(records=len(rows), skills=dict(Counter(r['skill'] for r in rows)), splits=dict(Counter(r['split'] for r in rows)),
                families=len(assignments), bases=len(bases), nuisance_pairs=len(pairs), boundary_groups=len(threshold_groups),
                split_leakage=False)


def build(registry_root, anchor, out, variants=2):
    root = Path(out)
    if root.exists():
        raise ValueError('fresh primitive release required')
    registry = load_registry(registry_root, anchor)
    rows, cases, checks, quarantine = [], [], [], []
    for base in scenarios(registry_root, anchor, variants):
        try:
            case, evidence = generate(base, int(base['base_project_id'][-1], 16) % 2)
            checked_study(case['healthy_project'])
            values = case_rows(case)
            rows.extend(values)
            cases.append(case)
            checks.append(dict(task_id=case['id'], **evidence))
        except (ValueError, RuntimeError) as exc:
            quarantine.append(dict(base_project_id=base['base_project_id'], family_sha256=base['family_sha256'], error=str(exc)))
    stats = validate_rows(rows, registry)
    root.mkdir(parents=True)
    exports = {'train.jsonl': [r for r in rows if r['split'] == 'train'],
               'owner-evaluation.jsonl': [r for r in rows if r['split'] != 'train'],
               'cases.jsonl': cases}
    for split in ('dev', 'test'):
        exports[split+'-prompts.jsonl'] = [dict(id=r['id'], split=split, skill=r['skill'], messages=r['messages']) for r in rows if r['split'] == split]
    for filename, values in exports.items():
        (root/filename).write_text(''.join(json.dumps(x, allow_nan=False, ensure_ascii=False)+'\n' for x in values))
    write_json(root/'physics.json', checks)
    summary = dict(schema='power-grid-env.primitive-curriculum/v1', registry=str(Path(registry_root).resolve()), registry_manifest_sha256=anchor,
                   parameter_variants=variants, counts=stats, quarantined_bases=quarantine,
                   source_sha256=sources(), native_admission_passed=False, optimizer_run=False,
                   gold_sft_eligible=False, scope='New-family public primitives; heldout answers owner-only. Arithmetic tasks are not additional physical studies. No learned score.')
    write_json(root/'summary.json', summary)
    seal(root)
    return summary


def load(root, anchor):
    if verify(root) != anchor:
        raise ValueError('primitive release manifest differs')
    root = Path(root)
    summary = read_json(root/'summary.json')
    if summary.get('schema') != 'power-grid-env.primitive-curriculum/v1' or summary.get('source_sha256') != sources():
        raise ValueError('primitive source/schema differs')
    registry = load_registry(summary['registry'], summary['registry_manifest_sha256'])
    train = [json.loads(line) for line in (root/'train.jsonl').read_text().splitlines()]
    owner = [json.loads(line) for line in (root/'owner-evaluation.jsonl').read_text().splitlines()]
    if any(r['split'] != 'train' for r in train) or any(r['split'] not in ('dev', 'test') for r in owner):
        raise ValueError('heldout labels cannot enter train export')
    rows = train+owner
    if sha(validate_rows(rows, registry)) != sha(summary['counts']):
        raise ValueError('primitive counts differ')
    # Recompute API-effect labels from real engine transitions, not a language teacher.
    rebuilt = []
    for line in (root/'cases.jsonl').read_text().splitlines():
        case = json.loads(line)
        checked_study(case['healthy_project'])
        rebuilt.extend(case_rows(case))
    if {r['id'] for r in rebuilt} != {r['id'] for r in rows} or len(rebuilt) != len(rows):
        raise ValueError('primitive engine replay differs')
    for split in ('dev', 'test'):
        prompts = [json.loads(line) for line in (root/(split+'-prompts.jsonl')).read_text().splitlines()]
        expected = [dict(id=r['id'], split=split, skill=r['skill'], messages=r['messages']) for r in owner if r['split'] == split]
        if sha(prompts) != sha(expected):
            raise ValueError('heldout prompt projection differs')
    return train, owner, summary


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for key in ('registry', 'manifest-sha256', 'out'):
        p.add_argument('--'+key, required=True)
    a = p.parse_args()
    print(json.dumps(build(a.registry, a.manifest_sha256, a.out)))
