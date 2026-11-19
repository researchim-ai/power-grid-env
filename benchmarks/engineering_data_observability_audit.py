"""Read-only data-contract audit; constructed counterexamples are NOT SFT gold.

Preserves historical observation/judge implementations and sealed corpora.
Checks initial observations, not indistinguishability after a fresh simulation.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from benchmarks import engineering_agent as judge
from benchmarks.engineering_factory import write_json
from benchmarks.engineering_operation_probe import messages
from benchmarks.engineering_replacement_challenges import ReplacementEpisode
from benchmarks.engineering_choice_episode import run, catalog_control
from power_grid_env.study_artifacts import read_json, seal, verify


def counterexamples(case):
    healthy = copy.deepcopy(case)
    healthy['project'] = copy.deepcopy(case['healthy_project'])
    baseline = ReplacementEpisode(healthy)
    card = baseline.observe()[0]
    errors = judge.structure_errors(baseline.engine.state['project'], baseline.engine.requirements)
    if errors:
        raise ValueError('counterexample requires structurally valid baseline')
    rows = []
    for kind in ('rounded_line_resistance', 'omitted_installed_load_power'):
        altered = copy.deepcopy(healthy)
        if kind == 'rounded_line_resistance':
            altered['project']['lines'][0]['r_ohm_km'] += 1e-8
        else:
            altered['project']['loads'][0]['p_kw'] += 0.25
        episode = ReplacementEpisode(altered)
        changed = episode.observe()[0]
        violations = judge.structure_errors(episode.engine.state['project'], episode.engine.requirements)
        same = card == changed
        if not same or not violations:
            raise ValueError('expected initial-observation collision was not reproduced')
        rows.append(dict(kind=kind, initial_public_cards_equal=same,
                         public_card_sha256=hashlib.sha256(json.dumps(card, sort_keys=True).encode()).hexdigest(),
                         baseline_structure_errors=errors, altered_structure_errors=violations,
                         constructed=True, current_corpus_frequency_not_measured=True,
                         scope='Initial card only; fresh simulation may distinguish states.'))
    # A legal successful alternative to the teacher-preferred validate first.
    reply, _ = baseline.apply(dict(name='simulate', arguments={}))
    terminal, _ = baseline.apply(dict(name='submit', arguments={}))
    direct_success = reply.get('ok') is True and terminal.get('result', {}).get('success') is True
    if not direct_success:
        raise ValueError('direct simulate/submit alternative no longer succeeds')
    return dict(collisions=rows, healthy_simulate_submit_success=direct_success,
                teacher_preferred_operation=ReplacementEpisode(healthy).teacher()['name'])


def audit(source, anchor, out):
    if verify(source) != anchor:
        raise ValueError('source manifest differs')
    root = Path(out)
    if root.exists():
        raise ValueError('fresh audit directory required')
    records = []
    for path in sorted((Path(source) / 'cases').glob('*.json')):
        case = read_json(path)['case']
        result = counterexamples(case)
        control = run(case, catalog_control, episode_factory=ReplacementEpisode)
        card = ReplacementEpisode(case).observe()[0]
        projected = json.loads(messages(card)[1]['content'])
        result.update(task_id=case['id'], base_project_id=case['base_project_id'],
                      family_sha256=case['family_sha256'], max_rating_public_control_success=control['success'],
                      operation_tools_removed='tools' not in projected,
                      operation_last_allowed_name=projected['allowed_operations'][-1])
        records.append(result)
    if not records:
        raise ValueError('nonempty source required')
    root.mkdir(parents=True)
    write_json(root / 'cases.json', records)
    summary = dict(schema='power-grid-env.data-observability-audit/v1', source_manifest_sha256=anchor,
                   cases=len(records), base_networks=len({r['base_project_id'] for r in records}),
                   families=len({r['family_sha256'] for r in records}),
                   constructed_initial_collisions=sum(len(r['collisions']) for r in records),
                   max_rating_public_control_successes=sum(r['max_rating_public_control_success'] for r in records),
                   healthy_direct_simulate_submit_successes=sum(r['healthy_simulate_submit_success'] for r in records),
                   all_operation_prompts_remove_tool_schemas=all(r['operation_tools_removed'] for r in records),
                   all_operation_menus_end_validate=all(r['operation_last_allowed_name']=='validate' for r in records),
                   source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   optimizer_run=False, eligible_for_gold_sft=False,
                   scope='Synthetic TRAIN corpus and constructed observation counterexamples; no model score, '
                         'no proof of training failure causality or ambiguity in all complete histories.')
    write_json(root / 'summary.json', summary)
    seal(root)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for key in ('source', 'manifest-sha256', 'out'):
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.source, args.manifest_sha256, args.out)))
