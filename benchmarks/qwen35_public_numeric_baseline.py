"""Public text-only arithmetic control; image inputs are deliberately abstained.

The policy receives messages only. Private frames, targets, IDs and topology
metadata are used solely by the host grader after the answer is constructed.
This is not an OCR baseline or an engineering-design agent.
"""
import argparse
import json
from pathlib import Path

from power_grid_env.design_frame import MotorIndication, indication_rule
from power_grid_env.design_multimodal import IndicationGrader
from power_grid_env.evidence import package_fingerprint
from power_grid_env.qwen35_adapter_evaluation import read_json_line
from power_grid_env.qwen35_evaluation import heldout_exports
from power_grid_env.study_artifacts import encoded, read_json, seal, verify


def public_numeric_policy(messages):
    if len(messages) != 1 or messages[0]['role'] != 'user':
        raise ValueError('public prompt-only conversation required')
    prefix = 'Instrument observation: '
    blocks = [b['text'][len(prefix):] for b in messages[0]['content']
              if b['type'] == 'text' and b['text'].startswith(prefix)]
    if not blocks:
        return dict(motors={}, insufficient_observation=True)
    if len(blocks) != 1:
        raise ValueError('one public instrument block required')
    payload = read_json_line(blocks[0])
    if set(payload) != {'motors', 'rule'} or payload['rule'] != indication_rule():
        raise ValueError('unsupported public indication rule')
    motors = [MotorIndication.model_validate(m) for m in payload['motors']]
    if not 1 <= len(motors) <= 8 or len({m.motor_id for m in motors}) != len(motors):
        raise ValueError('bounded unique public motor inventory required')
    threshold = payload['rule']['running_speed_pu']
    return dict(motors={m.motor_id: 'disconnected' if not m.connected else
        'at_running_speed' if m.speed_indication_pu >= threshold else 'below_running_speed'
        for m in motors}, insufficient_observation=False)


def run(profile, out):
    p = read_json(profile)
    datasets = heldout_exports(p['collection_root'], p['collection_manifest'],
                              p['corpus_root'], p['corpus_manifest'], p['split'])
    grader = IndicationGrader(p['corpus_root'], p['corpus_manifest'])
    rows = []
    for data in datasets:
        for i in range(len(data)):
            record = data.record(i)
            answer = public_numeric_policy(record['messages'])
            grade = grader.score(record['metadata']['task_id'], answer, data.mode)
            rows.append(dict(mode=data.mode, task_id=record['metadata']['task_id'],
                             answer=answer, **grade))
    metrics = {mode: dict(examples=len([r for r in rows if r['mode'] == mode]),
        correct=sum(r['reward'] == 1. for r in rows if r['mode'] == mode))
        for mode in ('image', 'text', 'combined', 'schematic')}
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    result = dict(passed=True, code_sha256=package_fingerprint(),
        collection_manifest_sha256=p['collection_manifest'], corpus_manifest_sha256=p['corpus_manifest'],
        split=p['split'], predictions=len(rows), metrics=metrics, rows=rows,
        policy_inputs='public_messages_only', private_targets_used_by_policy=False,
        OCR_implemented=False, neural_weights_loaded=False, physical_integrations=0,
        scope='Arithmetic control for numeric text/combined observations; image abstention is a declared capability limit. '
              'No neural compute-budget, OCR, visual-accuracy or engineering-superiority comparison.')
    (root/'receipt.json').write_bytes(encoded(result))
    if verify(p['collection_root']) != p['collection_manifest'] or verify(p['corpus_root']) != p['corpus_manifest']:
        raise ValueError('public-policy evidence changed')
    seal(root)
    return {k: v for k, v in result.items() if k != 'rows'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    parser.add_argument('--out', required=True)
    print(run(**vars(parser.parse_args())))
