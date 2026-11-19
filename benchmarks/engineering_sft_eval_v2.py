"""Generated-answer metrics and full-workflow evaluation for the exact profile.

Works with any callable policy, including separately labelled scripted controls.
TRAIN diagnostics cannot become held-out results by renaming a report.
"""
import copy
from collections import defaultdict

from benchmarks.engineering_sft_admission_v2 import answer_class
from benchmarks.engineering_sft_release_v2 import sha
from benchmarks.engineering_sft_runtime_v2 import run
from benchmarks.engineering_staged_agent import bounded_json


def evaluate(policy, rows, cases, candidate):
    if not isinstance(candidate, str) or not candidate.strip() or not rows or not cases:
        raise ValueError('named candidate, rows and cases required')
    results, episodes = [], []
    metrics = defaultdict(lambda: [0, 0])
    for row in rows:
        raw, error = None, None
        try:
            raw = policy(dict(stage=row['skill'], messages=copy.deepcopy(row['messages']), maximum_completion_tokens=128))
            answer = bounded_json(raw)
            correct = sha(answer) in {sha(a) for a in row['acceptable_answers']}
        except (ValueError, TypeError, RuntimeError) as exc:
            correct, error = False, type(exc).__name__ + ':' + str(exc)[:500]
        results.append(dict(row_id=row['id'], split=row['split'], skill=row['skill'],
                            completion=raw, correct=correct, error=error))
        for group in ('all', 'skill:' + row['skill'], 'family:' + row['family_sha256'],
                      'class:' + row['skill'] + ':' + answer_class(row)):
            metrics[group][0] += int(correct)
            metrics[group][1] += 1
    for case in cases:
        episodes.append(run(case, policy))
    splits = sorted({r['split'] for r in rows} | {c['split'] for c in cases})
    return dict(schema='power-grid-env.exact-sft-evaluation/v2', candidate=candidate,
                splits=splits, train_diagnostic_only=splits == ['train'],
                rows=results, episodes=episodes,
                answer_metrics={k: dict(correct=v[0], total=v[1], rate=v[0]/v[1]) for k, v in sorted(metrics.items())},
                workflow_successes=sum(e['success'] for e in episodes), workflow_cases=len(episodes),
                workflow_base_networks=len({c['base_project_id'] for c in cases}),
                workflow_families=len({c['family_sha256'] for c in cases}),
                simulations=sum(e['decision']['action'] is not None and e['decision']['action']['name'] == 'simulate'
                                for episode in episodes for e in episode['events']),
                mutations=sum(e['decision']['action'] is not None and e['decision']['action']['name'] in
                              ('set_line_catalog', 'remove_line', 'add_line', 'add_load')
                              for episode in episodes for e in episode['events']),
                quality_gate_promoted=False,
                scope='Generated answers accept replayed alternatives; full episodes use physical terminal. '
                      'No confidence or generalization claim from TRAIN diagnostics.')
