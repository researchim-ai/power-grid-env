"""Explain complete base-model failures without changing any official score."""
import argparse
import json
from pathlib import Path

from power_grid_env.design_multimodal import IndicationGrader, verify_dataset
from power_grid_env.qwen35_evaluation import summarize
from power_grid_env.study_artifacts import encoded, read_json, seal, verify
from power_grid_env.evidence import package_fingerprint


def strict_reply(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    def reject(value):
        raise ValueError('nonfinite number')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=reject)


def run(evaluation, evaluation_manifest, corpus, corpus_manifest, out):
    root = Path(evaluation)
    if verify(root) != evaluation_manifest:
        raise ValueError('owner evaluation anchor differs')
    report = read_json(root/'receipt.json')
    rows = [json.loads(s) for s in (root/'predictions.jsonl').read_text().splitlines()]
    source = verify_dataset(corpus, corpus_manifest)
    ids = {r['id'] for r in source['rows'] if r['split'] == report['split']}
    if (not report['passed'] or report['code_sha256'] != package_fingerprint() or
            report['corpus_manifest_sha256'] != corpus_manifest or
            report['metrics'] != summarize(rows, len(ids)) or
            any({r['task_id'] for r in rows if r['mode'] == mode} != ids
                for mode in report['metrics'])):
        raise ValueError('incomplete evaluation or mixed corpus')
    grader = IndicationGrader(corpus, corpus_manifest)
    audited = []
    for row in rows:
        text = row['completion']
        reward = grader.reward_batch([text], [row['task_id']], row['mode'])[0]
        if reward != row['reward']:
            raise ValueError('official prediction reward does not replay')
        recovered = False
        if reward == 1.:
            reason = 'correct'
        else:
            try:
                decision = strict_reply(text)
                grade = grader.score(row['task_id'], decision, row['mode'])
                reason = 'wrong_indication' if grade['valid'] else 'response_schema'
            except (ValueError, RecursionError):
                reason = 'token_budget_exhausted' if row['token_budget_exhausted'] else 'invalid_json'
                fenced = text.strip()
                if fenced.startswith('```'):
                    reason = 'fenced_or_truncated_json'
                    if fenced.endswith('```') and '\n' in fenced:
                        body = fenced.split('\n', 1)[1].rsplit('```', 1)[0]
                        try:
                            recovered = grader.score(row['task_id'], strict_reply(body), row['mode'])['reward'] == 1.
                        except (ValueError, RecursionError):
                            pass
        audited.append(dict(task_id=row['task_id'], mode=row['mode'], reason=reason,
                            official_reward=reward, would_match_after_fence_removal=recovered))
    counts = {mode: {reason: sum(r['mode'] == mode and r['reason'] == reason for r in audited)
              for reason in sorted({r['reason'] for r in audited})} for mode in report['metrics']}
    output = Path(out)
    output.mkdir(parents=True, exist_ok=False)
    result = dict(passed=True, code_sha256=package_fingerprint(),
        evaluation_manifest_sha256=evaluation_manifest, corpus_manifest_sha256=corpus_manifest,
        predictions=len(rows), strict_metrics=report['metrics'], failure_counts=counts,
        rows=audited, official_scores_unchanged=True,
        scope='Host-only failure taxonomy; fence removal is diagnostic only, never used for official rewards.')
    (output/'receipt.json').write_bytes(encoded(result))
    if verify(root) != evaluation_manifest or verify(corpus) != corpus_manifest:
        raise ValueError('audit evidence changed')
    seal(output)
    return {k: v for k, v in result.items() if k != 'rows'}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for name in ('evaluation', 'evaluation-manifest', 'corpus', 'corpus-manifest', 'out'):
        p.add_argument('--'+name, required=True)
    print(run(**vars(p.parse_args())))
