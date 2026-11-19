"""Replayed text SFT candidates with exact observations and explicit alternatives.

Uses existing TRAIN ancestry only. Does not manufacture a clean held-out split.
No model weights, optimizer or teacher answers enter public requests.
"""
import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

from benchmarks import engineering_agent as judge
from benchmarks.engineering_constraint_tasks import assess
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_reference import compare_reference
from benchmarks.engineering_sft_contract_v2 import ExactEpisode, feedback, request, structure_assessment
from benchmarks.grid_editor import solve
from power_grid_env.study_artifacts import encoded, read_json, seal, verify


def sha(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def checked_study(project):
    result = solve(project)
    return result, compare_reference(project, result)


def non_dominant(case):
    """Higher ampacity can still fail voltage; labels come from actual studies."""
    value = copy.deepcopy(case)
    safe = max(case['requirements']['catalog'], key=lambda k: case['requirements']['catalog'][k]['rating_a'])
    passport = value['requirements']['catalog'][safe]
    distractor = dict(passport, rating_a=passport['rating_a'] * 2, r_ohm_km=20.)
    name = 'option-' + sha(case['id'])[:6]
    value['requirements']['catalog'][name] = distractor
    # No unique "distractor" prefix or ordinal may reveal the answer.
    value['requirements']['catalog'] = {
        'part-' + sha([case['id'], key])[:10]: passport
        for key, passport in value['requirements']['catalog'].items()}
    value['id'] = 'non-dominant-' + case['id']
    before, before_check = checked_study(value['project'])
    overloaded = [b for b in before['branches'] if b['loading_pct'] is not None and b['loading_pct'] > value['requirements']['maximum_branch_loading_pct']]
    if len(overloaded) != 1:
        raise ValueError('non-dominant case requires one localized overload')
    wrong = copy.deepcopy(value['project'])
    next(x for x in wrong['lines'] if x['id'] == overloaded[0]['id']).update(distractor)
    result, wrong_check = checked_study(wrong)
    if 'minimum_voltage' not in judge.physical_errors(result, value['requirements']):
        raise ValueError('high-rating distractor does not actually violate voltage')
    good, good_check = checked_study(value['healthy_project'])
    if judge.structure_errors(value['healthy_project'], value['requirements']) or judge.physical_errors(good, value['requirements']):
        raise ValueError('intended feasible alternative failed')
    return value, dict(before=before_check, higher_rating=wrong_check, feasible=good_check,
                       higher_rating_violations=judge.physical_errors(result, value['requirements']))


def feasible_catalogues(episode, line):
    """Owner-side branch search using legal actions, full judge and actual budgets."""
    accepted = []
    for entry in episode.observe()[0]['scene']['catalog']:
        branch = copy.deepcopy(episode)
        actions = [dict(name='set_line_catalog', arguments=dict(equipment_id=line, catalog_id=entry['id'])),
                   dict(name='simulate', arguments={}), dict(name='submit', arguments={})]
        ok = True
        for action in actions:
            response, _ = branch.apply(action)
            ok = ok and response.get('ok') is True
            if not ok:
                break
            if action['name'] == 'simulate':
                compare_reference(branch.engine.state['project'], branch.engine.state['last_result'])
        if ok and branch.engine.state['terminal'].get('success') is True:
            accepted.append(actions[0]['arguments'])
    return sorted(accepted, key=encoded)


def observations(episode, base, last_feedback, source):
    card, _ = episode.observe()
    public_structure = structure_assessment(card)
    owner_ok = not judge.structure_errors(episode.engine.state['project'], episode.engine.requirements)
    if public_structure['structure_matches'] != owner_ok:
        raise ValueError('public structural assessment differs from owner judge')
    labels = dict(state={k: card[k] for k in ('revision', 'validated', 'has_fresh_measurements', 'calls_remaining', 'simulations_remaining')},
                  measurement=assess(card), structure=public_structure)
    if card['has_fresh_measurements']:
        compare_reference(episode.engine.state['project'], episode.engine.state['last_result'])
        if labels['measurement']['study_ready'] and labels['measurement']['measurement_feasible'] != (not judge.physical_errors(episode.engine.state['last_result'], episode.engine.requirements)):
            raise ValueError('measurement label differs from owner physics')
    return [dict(**base, skill=skill, messages=request(card, skill, last_feedback), answer=answer,
                 acceptable_answers=[answer], provenance=source) for skill, answer in labels.items()]


def trajectory(case, validate_first):
    episode = ExactEpisode(case)
    rows, events = [], []
    previous = None
    while not episode.engine.state['finished'] and episode.engine.state['calls'] < 20:
        card, aliases = episode.observe()
        base = dict(split=case['split'], family_sha256=case['family_sha256'], base_project_id=case['base_project_id'],
                    task_id=case['id'], revision=card['revision'], turn=len(events),
                    trajectory_id=sha([case['id'], validate_first]), modality='text',
                    source_kind='replayed_expert')
        rows.extend(observations(episode, base, previous, 'public_state_and_independent_reference'))
        alternatives = []
        if validate_first and not card['validated']:
            action = dict(name='validate', arguments={})
        elif not card['has_fresh_measurements']:
            action = dict(name='simulate', arguments={})
        elif assess(card)['measurement_feasible']:
            action = dict(name='submit', arguments={})
        else:
            overloaded = [x for x in card['study']['branches'] if x['loading_pct'] is not None and x['loading_pct'] > card['goal']['maximum_branch_loading_pct']]
            if not overloaded:
                raise ValueError('this teacher supports localized overload repair only')
            line = max(overloaded, key=lambda x: x['loading_pct'])['id']
            alternatives = feasible_catalogues(episode, line)
            if not alternatives:
                raise ValueError('no replayed feasible catalogue continuation')
            action = dict(name='set_line_catalog', arguments=alternatives[0])
        rows.append(dict(**base, skill='operation', messages=request(card, 'operation', previous),
                         answer=dict(operation=action['name']), acceptable_answers=[dict(operation=action['name'])],
                         provenance='successful_trajectory_preferred_not_exclusive'))
        if action['arguments']:
            rows.append(dict(**base, skill='arguments', messages=request(card, 'arguments', previous, action['name']),
                             answer=action['arguments'], acceptable_answers=alternatives or [action['arguments']],
                             provenance='physically_replayed_catalogue_continuations'))
        response, _ = episode.apply(action)
        if not response.get('ok'):
            raise ValueError('expert action rejected')
        events.append(dict(card=card, previous_feedback=previous, action=action, response=response))
        previous = feedback(response, action, aliases)
    if (episode.engine.state['terminal'] or {}).get('success') is not True:
        raise ValueError('only fully successful expert traces may supervise actions')
    return rows, dict(case=case, validate_first=validate_first, events=events, terminal=episode.engine.state['terminal'])


def merge_rows(rows):
    """Preserve successful alternatives while removing repeated identical inputs."""
    merged = {}
    for row in rows:
        key = sha(dict(messages=row['messages'], skill=row['skill']))
        if key not in merged:
            merged[key] = copy.deepcopy(row)
            merged[key]['input_sha256'] = key
            merged[key]['occurrences'] = []
        current = merged[key]
        if (current['split'], current['family_sha256'], current['base_project_id']) != (row['split'], row['family_sha256'], row['base_project_id']):
            raise ValueError('identical visible input has ambiguous ancestry or crosses splits')
        if row['skill'] not in ('operation', 'arguments') and encoded(current['answer']) != encoded(row['answer']):
            raise ValueError('conflicting factual labels for identical public input')
        current['acceptable_answers'] = sorted({encoded(a): a for a in current['acceptable_answers'] + row['acceptable_answers']}.values(), key=encoded)
        current['occurrences'].append({k: row[k] for k in ('task_id', 'trajectory_id', 'turn')})
    result = []
    for key, row in sorted(merged.items()):
        row['id'] = sha({k: v for k, v in row.items() if k != 'id'})
        result.append(row)
    return result


def export(source, anchor, out):
    if verify(source) != anchor:
        raise ValueError('source manifest differs')
    if read_json(Path(source) / 'summary.json').get('schema') != 'power-grid-env.catalog-intervention-corpus/v1':
        raise ValueError('qualified catalogue source required')
    root = Path(out)
    if root.exists():
        raise ValueError('fresh SFT release required')
    rows, traces, evidence = [], [], []
    for path in sorted((Path(source) / 'cases').glob('*.json')):
        case = read_json(path)['case']
        if case['split'] != 'train':
            raise ValueError('historical train ancestry must not be reassigned')
        harder, checks = non_dominant(case)
        healthy = copy.deepcopy(case)
        healthy.update(id='healthy-' + case['id'], project=copy.deepcopy(case['healthy_project']))
        evidence.append(dict(task_id=harder['id'], **checks))
        for variant in (case, healthy, harder):
            for validate_first in (True, False):
                examples, trace = trajectory(variant, validate_first)
                rows.extend(examples)
                traces.append(trace)
        # Assessment-only actual-parameter counterexamples; no unsupported repair labels.
        for kind in ('line_precision', 'installed_load_power'):
            altered = copy.deepcopy(healthy)
            altered['id'] = kind + '-' + case['id']
            if kind == 'line_precision':
                altered['project']['lines'][0]['r_ohm_km'] += 1e-8
            else:
                altered['project']['loads'][0]['p_kw'] += .25
            episode = ExactEpisode(altered)
            checked_study(episode.engine.state['project'])
            base = dict(split='train', family_sha256=case['family_sha256'], base_project_id=case['base_project_id'],
                        task_id=altered['id'], revision=0, turn=0, trajectory_id=sha(altered['id']),
                        modality='text', source_kind='assessment_only_counterexample')
            rows.extend(observations(episode, base, None, 'constructed_public_parameter_assessment'))
    if not rows:
        raise ValueError('nonempty corpus required')
    raw_count = len(rows)
    rows = merge_rows(rows)
    root.mkdir(parents=True)
    for name, values in (('records.jsonl', rows), ('traces.jsonl', traces)):
        (root / name).write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n' for r in values))
    write_json(root / 'physics.json', evidence)
    summary = dict(schema='power-grid-env.exact-sft-candidates/v2', source_manifest_sha256=anchor,
                   records=len(rows), raw_records=raw_count, successful_traces=len(traces),
                   skills=dict(Counter(r['skill'] for r in rows)),
                   bases=len({r['base_project_id'] for r in rows}), families=len({r['family_sha256'] for r in rows}),
                   records_with_alternative_answers=sum(len(r['acceptable_answers']) > 1 for r in rows),
                   higher_rating_voltage_failures=len(evidence), native_admission_passed=False,
                   heldout_available=False, eligible_for_gold_sft=False, optimizer_run=False,
                   source_sha256={name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                  for name in ('engineering_sft_contract_v2.py', 'engineering_sft_release_v2.py')},
                   scope='Replayed synthetic balanced constant-Z TRAIN candidates; text only; no clean heldout or model improvement claim.')
    write_json(root / 'summary.json', summary)
    seal(root)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for key in ('source', 'manifest-sha256', 'out'):
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.source, args.manifest_sha256, args.out)))
