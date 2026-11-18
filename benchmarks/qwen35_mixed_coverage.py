"""Train-only label coverage audit: views are not independent physical cases."""
import argparse
from collections import Counter
import json
from pathlib import Path

from power_grid_env.design_frame import IndicationDecision
from power_grid_env.evidence import package_fingerprint
from power_grid_env.qwen35_mixed_training import MixedData, MODES
from power_grid_env.qwen35_training import file_sha256
from power_grid_env.study_artifacts import encoded, read_json, seal


def run(profile, out):
    p = read_json(profile)
    data = MixedData(p['collection_root'], p['collection_manifest'])
    rows = {}
    answers = {}
    ontology = {'disconnected', 'below_running_speed', 'at_running_speed'}
    for mode in MODES:
        records = [data.datasets[mode].record(i) for i in range(len(data.ids))]
        values = [IndicationDecision.model_validate(json.loads(r['messages'][1]['content']))
                  for r in records]
        answers[mode] = values
        classes = Counter(v for answer in values for v in answer.motors.values())
        rows[mode] = dict(views=len(values), classes=dict(sorted(classes.items())),
            response_motor_counts={str(k): v for k, v in sorted(Counter(len(a.motors) for a in values).items())},
            insufficient_observation=sum(a.insufficient_observation for a in values))
        if mode == 'schematic':
            if any(not a.insufficient_observation or a.motors for a in values):
                raise ValueError('schematic targets must abstain without current indications')
        elif any(a.insufficient_observation or not a.motors for a in values):
            raise ValueError('measured train targets must include motor indications')
    if not answers['image'] == answers['text'] == answers['combined']:
        raise ValueError('same observation has inconsistent targets between measured views')
    present = set(rows['text']['classes'])
    if not present <= ontology:
        raise ValueError('unknown indication class')
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    result = dict(schema='power-grid-env.qwen35-train-coverage/v2', passed=True,
        code_sha256=package_fingerprint(), profile_sha256=file_sha256(profile),
        collection_manifest_sha256=p['collection_manifest'], split='train', modes=rows,
        distinct_observations=len(data.ids), views=len(data.ids)*len(MODES),
        missing_train_indication_classes=sorted(ontology-present), test_read=False,
        new_cases_or_labels_added=False, observations_statistically_independent=False,
        scope='Public SFT answer coverage only; no held-out inspection or physical corpus expansion.')
    data.check_unchanged()
    (root/'receipt.json').write_bytes(encoded(result))
    seal(root)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    parser.add_argument('--out', required=True)
    print(run(**vars(parser.parse_args())))
