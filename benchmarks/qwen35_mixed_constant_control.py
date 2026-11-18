"""Post-hoc static-ID class-prior controls, explicitly without observation reading."""
import argparse
from collections import Counter
from pathlib import Path

from power_grid_env.design_multimodal import IndicationGrader
from power_grid_env.design_response_schema import schema_text, response_schema
from power_grid_env.evidence import package_fingerprint
from power_grid_env.qwen35_adapter_evaluation import audited_predictions, read_json_line, parse_decision
from power_grid_env.qwen35_evaluation import heldout_exports
from power_grid_env.study_artifacts import read_json, encoded, seal, verify

CLASSES = ('at_running_speed', 'below_running_speed', 'disconnected')


def constant_policy(messages, indication):
    """Only public static IDs enter the policy; no pixels or measurements read."""
    if indication not in CLASSES:
        raise ValueError('declared constant indication required')
    if len(messages) != 1 or messages[0]['role'] != 'user':
        raise ValueError('one public prompt required')
    prefix = schema_text(['placeholder']).split('\n', 1)[0]+'\n'
    texts = [b['text'][len(prefix):] for b in messages[0]['content']
             if b['type'] == 'text' and b['text'].startswith(prefix)]
    if len(texts) != 1:
        raise ValueError('one explicit public response contract required')
    schema = read_json_line(texts[0])
    ids = list(schema['properties']['motors']['properties'])
    if schema != response_schema(ids):
        raise ValueError('unsupported public response contract')
    return dict(insufficient_observation=False, motors=dict.fromkeys(ids, indication))


def run(profile, adapter, adapter_manifest, out):
    p = read_json(profile)
    report, indexed = audited_predictions(adapter, adapter_manifest, p['corpus_root'], p['corpus_manifest'])
    if (not report['adapters_loaded'] or report['split'] != 'dev' or
            report['collection_manifest_sha256'] != p['collection_manifest']):
        raise ValueError('complete schema-protocol dev predictions required')
    datasets = heldout_exports(p['collection_root'], p['collection_manifest'], p['corpus_root'], p['corpus_manifest'], 'dev')
    grader = IndicationGrader(p['corpus_root'], p['corpus_manifest'])
    metrics = {c: {} for c in CLASSES}
    matches = 0
    for data in datasets:
        scores = {c: [] for c in CLASSES}
        for i in range(len(data)):
            record = data.record(i)
            task_id = record['metadata']['task_id']
            for c in CLASSES:
                answer = constant_policy(record['messages'], c)
                scores[c].append(grader.score(task_id, answer, data.mode)['reward'])
                if c == 'at_running_speed':
                    actual = parse_decision(indexed[(data.mode, task_id)]['prediction']['completion'])
                    matches += actual is not None and actual.model_dump() == answer
        for c in CLASSES:
            metrics[c][data.mode] = dict(examples=len(scores[c]), correct=int(sum(scores[c])))
    frequencies = Counter(v['prediction']['completion'] for v in indexed.values())
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    result = dict(passed=True, code_sha256=package_fingerprint(), adapter_manifest_sha256=adapter_manifest,
        collection_manifest_sha256=p['collection_manifest'], predictions_per_policy=48,
        constant_metrics=metrics, constant_totals={c:sum(m['correct'] for m in metrics[c].values()) for c in CLASSES},
        unique_adapter_replies=len(frequencies), most_frequent_reply_count=max(frequencies.values()),
        adapter_answers_matching_all_running=matches, post_hoc_control=True,
        policy_inputs='public_static_ID_schema_only', observations_read_by_policy=False,
        private_targets_used_by_policy=False, rewards_changed=False, test_evaluated=False,
        scope='Post-hoc class-prior controls diagnose nearly constant answers. No observation reading, '
              'OCR, neural compute-budget comparison, selected successful subset or engineering capability claim.')
    if verify(adapter) != adapter_manifest or verify(p['collection_root']) != p['collection_manifest']:
        raise ValueError('constant-control evidence changed')
    (root/'receipt.json').write_bytes(encoded(result))
    seal(root)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for key in ('profile', 'adapter', 'adapter-manifest', 'out'):
        parser.add_argument('--'+key, required=True)
    print(run(**vars(parser.parse_args())))
