"""Full-denominator post-hoc diagnostics; joint rewards remain unchanged."""
import argparse
from pathlib import Path

from jsonschema import Draft202012Validator
from power_grid_env.design_response_schema import response_schema
from power_grid_env.design_multimodal import verify_dataset
from power_grid_env.evidence import package_fingerprint
from power_grid_env.qwen35_adapter_evaluation import audited_predictions, parse_decision, read_json_line
from power_grid_env.study_artifacts import encoded, read_json, verify, seal


def run(profile, base, base_manifest, adapter, adapter_manifest, out):
    p = read_json(profile)
    corpus = verify_dataset(p['corpus_root'], p['corpus_manifest'])
    targets = {r['id']: r for r in corpus['rows'] if r['split'] == 'dev'}
    reports = {}
    for name, path, anchor in (('base', base, base_manifest), ('adapter', adapter, adapter_manifest)):
        report, indexed = audited_predictions(path, anchor, p['corpus_root'], p['corpus_manifest'])
        if (report['split'] != 'dev' or report['collection_manifest_sha256'] != p['collection_manifest'] or
                report['target_metadata_sha256'] != p['target_sha256'] or
                report['plan'] != p['generation_plan'] or
                report['adapters_loaded'] != (name == 'adapter')):
            raise ValueError('same-schema complete dev results required')
        metrics = {}
        for mode in ('image', 'text', 'combined', 'schematic'):
            entries = [(key[1], value['prediction']) for key, value in indexed.items() if key[0] == mode]
            required, correct, contract = 0, 0, 0
            for task_id, row in entries:
                target = targets[task_id]
                frame = read_json(Path(p['corpus_root'])/target['frame'])
                ids = [m['id'] for m in frame['passport']['motors']]
                decision = parse_decision(row['completion'])
                if decision is not None:
                    contract += Draft202012Validator(response_schema(ids)).is_valid(read_json_line(row['completion']))
                if mode != 'schematic':
                    wanted = target['expected']['motors']
                    required += len(wanted)
                    if decision is not None and not decision.insufficient_observation:
                        correct += sum(decision.motors.get(k) == v for k, v in wanted.items())
            metrics[mode] = dict(examples=len(entries), joint_correct=report['metrics'][mode]['correct'],
                generic_decision_valid=report['metrics'][mode]['valid_json_decisions'],
                public_response_contract_valid=contract, required_motor_fields=required,
                correct_required_motor_fields=correct,
                field_match_rate=correct/required if required else None)
            if len(entries) != 12 or not 0 <= correct <= required:
                raise ValueError('complete diagnostic denominator differs')
        reports[name] = metrics
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    result = dict(passed=True, code_sha256=package_fingerprint(), base_manifest_sha256=base_manifest,
        adapter_manifest_sha256=adapter_manifest, collection_manifest_sha256=p['collection_manifest'],
        metrics=reports, rewards_changed=False, test_evaluated=False,
        scope='Generic decision validity, public JSON contract and motor field matches are distinct. '
              'Field matches require insufficient=false, do not forgive extra IDs or change joint0/1 reward; '
              'schematic has no required motor fields. Same correlated dev cases, no statistical superiority claim.')
    if verify(base) != base_manifest or verify(adapter) != adapter_manifest:
        raise ValueError('diagnostic evidence changed')
    (root/'receipt.json').write_bytes(encoded(result))
    seal(root)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for key in ('profile', 'base', 'base-manifest', 'adapter', 'adapter-manifest', 'out'):
        parser.add_argument('--'+key, required=True)
    print(run(**vars(parser.parse_args())))
