"""Fail-closed corpus/replay/native-label admission and bounded SFT schedule.

No weights are loaded. Processor admission is distinct from learned quality.
"""
import argparse
import copy
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from jsonschema import Draft202012Validator
from benchmarks.engineering_constraint_tasks import assess
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_reference import compare_reference
from benchmarks.engineering_sft_contract_v2 import ExactEpisode, feedback, structure_assessment
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.engineering_staged_data import pack
from power_grid_env.study_artifacts import read_json, seal, verify


def validate_rows(rows):
    if not rows:
        raise ValueError('nonempty rows required')
    groups = {'family_sha256': {}, 'base_project_id': {}, 'input_sha256': {}}
    ids = set()
    for row in rows:
        if row['split'] not in ('train', 'dev', 'test') or row['modality'] != 'text':
            raise ValueError('unsupported split or modality')
        if row['id'] != sha({k: v for k, v in row.items() if k != 'id'}) or row['id'] in ids:
            raise ValueError('row identity changed or duplicated')
        ids.add(row['id'])
        for field, mapping in groups.items():
            value = row[field]
            if not isinstance(value, str) or not value:
                raise ValueError('nonempty lineage required')
            if mapping.setdefault(value, row['split']) != row['split']:
                raise ValueError('split leakage: ' + field)
        if row['input_sha256'] != sha(dict(messages=row['messages'], skill=row['skill'])):
            raise ValueError('input hash differs')
        if not row['acceptable_answers'] or sha(row['answer']) not in {sha(a) for a in row['acceptable_answers']}:
            raise ValueError('preferred answer missing from acceptable set')
        if len({sha(a) for a in row['acceptable_answers']}) != len(row['acceptable_answers']):
            raise ValueError('duplicate acceptable answer')
        if len(row['messages']) != 2 or [m['role'] for m in row['messages']] != ['system', 'user']:
            raise ValueError('two-message public request required')
        card = json.loads(row['messages'][1]['content'])
        if card['profile'] != 'exact-editor-sft/v2':
            raise ValueError('observation profile differs')
        if row['skill'] == 'state':
            expected = {k: card[k] for k in ('revision', 'validated', 'has_fresh_measurements', 'calls_remaining', 'simulations_remaining')}
        elif row['skill'] == 'structure':
            expected = structure_assessment(card)
        elif row['skill'] == 'measurement':
            expected = assess(card)
        else:
            expected = None
        if expected is not None and sha(row['acceptable_answers']) != sha([expected]):
            raise ValueError('factual label differs from visible evidence')
        if row['skill'] in ('operation', 'arguments') and row['source_kind'] != 'replayed_expert':
            raise ValueError('nonexpert positive action supervision')
        allowed = {x['function']['name']: x['function']['parameters'] for x in card['tools']}
        for answer in row['acceptable_answers']:
            if row['skill'] == 'operation':
                if set(answer) != {'operation'} or answer['operation'] not in allowed:
                    raise ValueError('invalid operation target')
            elif row['skill'] == 'arguments':
                name = card['given_operation']
                if name not in allowed or not Draft202012Validator(allowed[name]).is_valid(answer):
                    raise ValueError('invalid argument target')
            elif row['skill'] not in ('state', 'structure', 'measurement'):
                raise ValueError('unsupported skill')
    return dict(records=len(rows), skills=dict(Counter(r['skill'] for r in rows)),
                splits=dict(Counter(r['split'] for r in rows)), families=len(groups['family_sha256']),
                bases=len(groups['base_project_id']), split_leakage=False)


def load_release(root, anchor):
    root = Path(root)
    if verify(root) != anchor:
        raise ValueError('release manifest differs')
    summary = read_json(root / 'summary.json')
    if summary.get('schema') != 'power-grid-env.exact-sft-candidates/v2':
        raise ValueError('exact v2 candidates required')
    for name, expected in summary['source_sha256'].items():
        if hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() != expected:
            raise ValueError('release implementation changed: ' + name)
    rows = [json.loads(line) for line in (root / 'records.jsonl').read_text().splitlines()]
    validate_rows(rows)
    if len(rows) != summary['records']:
        raise ValueError('release row count differs')
    traces = [json.loads(line) for line in (root / 'traces.jsonl').read_text().splitlines()]
    if len(traces) != summary['successful_traces'] or not traces:
        raise ValueError('complete nonempty traces required')
    # Reconstruct public requests and labels rather than trusting edited targets.
    from benchmarks.engineering_sft_release_v2 import trajectory, merge_rows, observations, checked_study
    rebuilt = []
    cases = {}
    for trace in traces:
        examples, replay = trajectory(trace['case'], trace['validate_first'])
        if sha(replay) != sha(trace):
            raise ValueError('full physics trajectory replay differs')
        rebuilt.extend(examples)
        if trace['case']['id'].startswith('healthy-'):
            cases[trace['case']['id']] = trace['case']
    for case in cases.values():
        for kind in ('line_precision', 'installed_load_power'):
            altered = copy.deepcopy(case)
            altered['id'] = kind + '-' + case['id'][len('healthy-'):]
            if kind == 'line_precision':
                altered['project']['lines'][0]['r_ohm_km'] += 1e-8
            else:
                altered['project']['loads'][0]['p_kw'] += .25
            episode = ExactEpisode(altered)
            checked_study(episode.engine.state['project'])
            base = dict(split=case['split'], family_sha256=case['family_sha256'], base_project_id=case['base_project_id'],
                        task_id=altered['id'], revision=0, turn=0, trajectory_id=sha(altered['id']),
                        modality='text', source_kind='assessment_only_counterexample')
            rebuilt.extend(observations(episode, base, None, 'constructed_public_parameter_assessment'))
    if sha(merge_rows(rebuilt)) != sha(rows):
        raise ValueError('supervised rows do not reconstruct from replayed source')
    return rows, summary


def answer_class(row):
    value = row['answer']
    if row['skill'] == 'operation':
        return value['operation']
    if row['skill'] == 'arguments':
        return ','.join(sorted(value))
    if row['skill'] == 'state':
        return str([value['validated'], value['has_fresh_measurements']])
    return json.dumps(value, sort_keys=True)


def schedule(rows, stages, seed):
    """Round robin skill → answer class → family, seeded cycling within a bucket."""
    if type(seed) is not int or not 0 <= seed < 2**31:
        raise ValueError('bounded integer seed required')
    rng = random.Random(seed)
    order, exposures = [], Counter()
    for stage in stages:
        if type(stage.get('steps')) is not int or not 1 <= stage['steps'] <= 10000:
            raise ValueError('bounded positive stage steps required')
        skills = stage['skills']
        if not skills or len(set(skills)) != len(skills):
            raise ValueError('distinct nonempty stage skills required')
        buckets = defaultdict(lambda: defaultdict(dict))
        for i, row in enumerate(rows):
            if row['split'] == 'train' and row['skill'] in skills:
                bucket = buckets[row['skill']][answer_class(row)].setdefault(row['family_sha256'], [])
                bucket.append(i)
        if set(buckets) != set(skills):
            raise ValueError('each stage skill requires train examples')
        for classes in buckets.values():
            for families in classes.values():
                for indices in families.values():
                    rng.shuffle(indices)
        counters = Counter()
        for step in range(stage['steps']):
            skill = skills[step % len(skills)]
            classes = sorted(buckets[skill])
            category = classes[counters[skill] % len(classes)]
            counters[skill] += 1
            families = sorted(buckets[skill][category])
            family = families[counters[skill, category] % len(families)]
            counters[skill, category] += 1
            indices = buckets[skill][category][family]
            key = (skill, category, family)
            index = indices[counters[key] % len(indices)]
            counters[key] += 1
            row = rows[index]
            alternative = exposures[index] % len(row['acceptable_answers'])
            exposures[index] += 1
            order.append(dict(stage=stage['name'], index=index, row_id=row['id'], alternative=alternative))
    if not order:
        raise ValueError('nonempty finite schedule required')
    return order


def qualify(source, anchor, target, target_sha, out):
    root = Path(out)
    if root.exists():
        raise ValueError('fresh native admission required')
    rows, source_summary = load_release(source, anchor)
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    import torch
    torch.set_num_threads(4)
    target_spec = local_target(target, target_sha)
    processor = local_qwen35_processor(target_spec['local_checkpoint'], target_spec['config_sha256'])
    checks, failures = [], []
    for i, row in enumerate(rows):
        for alternative, answer in enumerate(row['acceptable_answers']):
            try:
                _, meta = pack(processor, dict(row, answer=answer))
                checks.append(dict(index=i, row_id=row['id'], alternative=alternative, **meta))
            except (ValueError, RuntimeError) as exc:
                failures.append(dict(index=i, alternative=alternative, error=str(exc)[:500]))
        if (i+1) % 128 == 0:
            print(json.dumps(dict(checked=i+1, total=len(rows), failures=len(failures))), flush=True)
    stages = [dict(name='foundation', steps=128, skills=['state', 'structure', 'measurement']),
              dict(name='grounding', steps=128, skills=['arguments', 'state', 'measurement']),
              dict(name='policy', steps=128, skills=['operation', 'arguments', 'state', 'structure', 'measurement'])]
    order = schedule(rows, stages, 20261118)
    root.mkdir(parents=True)
    write_json(root / 'checks.json', checks)
    summary = dict(schema='power-grid-env.exact-sft-admission/v2', source=str(Path(source).resolve()), source_manifest_sha256=anchor,
                   target_sha256=target_sha, passed=not failures, failures=failures,
                   records=len(rows), alternatives_checked=len(checks),
                   maximum_prompt_tokens=max((x['prompt_tokens'] for x in checks), default=0),
                   replay_passed=True, heldout_available=False, eligible_for_production_training=False,
                   eligible_for_train_only_calibration=not failures,
                   optimizer_run=False, weights_loaded=False,
                   recipe=dict(seed=20261118, stages=stages, schedule=order,
                               sampling='skill_then_answer_class_then_family_seeded_cycle',
                               target_alternatives='cycle_replayed_acceptable_answers',
                               learning_rate=.0001, gradient_clip=1., weight_decay=0.,
                               lora=dict(rank=16, alpha=32., last_blocks=2),
                               maximum_context_tokens=8192, maximum_completion_tokens=128,
                               loss='assistant_only_uniform_cross_entropy',
                               warm_start='must_be_explicit_and_source_pinned',
                               stop_rule='finite_384_step_ceiling_not_claimed_optimal',
                               automatic_training=False),
                   counts=validate_rows(rows),
                   source_sha256={name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                  for name in ('engineering_sft_admission_v2.py', 'engineering_sft_contract_v2.py',
                                               'engineering_sft_release_v2.py', 'engineering_staged_data.py')},
                   scope='Native admission and proposed finite TRAIN calibration schedule; not trained weights, evaluation, or clean heldout qualification.')
    write_json(root / 'summary.json', summary)
    seal(root)
    return {k: v for k, v in summary.items() if k != 'recipe'}


class AdmittedDataset:
    """Consumer boundary: failed/partial admission cannot reach an optimizer."""
    def __init__(self, admission, anchor, target_sha):
        root = Path(admission)
        if verify(root) != anchor:
            raise ValueError('admission manifest differs')
        plan = read_json(root / 'summary.json')
        if (plan.get('schema') != 'power-grid-env.exact-sft-admission/v2' or
                plan.get('target_sha256') != target_sha or plan.get('passed') is not True or
                plan.get('failures') or plan.get('replay_passed') is not True or
                plan.get('eligible_for_train_only_calibration') is not True):
            raise ValueError('passing exact native admission required')
        for name, expected in plan['source_sha256'].items():
            if hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() != expected:
                raise ValueError('admitted implementation changed: ' + name)
        self.rows, _ = load_release(plan['source'], plan['source_manifest_sha256'])
        recipe = plan['recipe']
        self.schedule = schedule(self.rows, recipe['stages'], recipe['seed'])
        if sha(self.schedule) != sha(recipe['schedule']):
            raise ValueError('admitted schedule differs')
        checks = read_json(root / 'checks.json')
        expected = {(i, a, row['id']) for i, row in enumerate(self.rows) for a in range(len(row['acceptable_answers']))}
        actual = {(c['index'], c['alternative'], c['row_id']) for c in checks}
        if expected != actual or len(checks) != len(expected) or plan['alternatives_checked'] != len(expected):
            raise ValueError('complete native alternative inventory required')
        self.plan = plan

    def example(self, step):
        entry = self.schedule[step]
        row = copy.deepcopy(self.rows[entry['index']])
        if row['split'] != 'train':
            raise ValueError('held-out row cannot enter optimizer')
        row['answer'] = row['acceptable_answers'][entry['alternative']]
        return row

    def batch(self, processor, step):
        row = self.example(step)
        batch, meta = pack(processor, row)
        return batch, dict(**meta, row_id=row['id'], stage=self.schedule[step]['stage'],
                           alternative=self.schedule[step]['alternative'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for key in ('source', 'manifest-sha256', 'target', 'target-sha256', 'out'):
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    print(json.dumps(qualify(args.source, args.manifest_sha256, args.target, args.target_sha256, args.out)))
