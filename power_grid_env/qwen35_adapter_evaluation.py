"""Owner-anchored adapter inference and paired, complete diagnostic evaluation.

Historical training and base receipts require explicit producer pins. No
historical Python code executes. Existing generation inputs and rewards stay
unchanged; pairing never repairs, drops or completes a model answer.
"""
from pathlib import Path
import importlib.metadata
import json
import math
import re
import time

from power_grid_env.design_frame import IndicationDecision
from power_grid_env.design_multimodal import IndicationGrader, verify_dataset
from power_grid_env.evidence import package_fingerprint
from power_grid_env.qwen35_evaluation import GenerationPlan, heldout_exports, summarize
from power_grid_env.qwen35_training import (
    AdapterPlan, adapter_state, attach_adapters, file_sha256, frozen_fingerprint,
    load_native, local_target, restore_adapters,
)
from power_grid_env.study_artifacts import encoded, read_json, seal, verify
from power_grid_env.vlm_batches import VLMExportDataset


def producer_pin(value):
    result = package_fingerprint() if value is None else value
    if not isinstance(result, str) or not re.fullmatch('[0-9a-f]{64}', result):
        raise ValueError('explicit producer SHA256 required')
    return result


def parse_decision(reply):
    if not isinstance(reply, str):
        return None
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate reply key')
            result[key] = value
        return result
    def reject(value):
        raise ValueError('nonfinite JSON constant')
    try:
        if len(reply.encode('utf8')) > 32768:
            return None
        answer = json.loads(reply, object_pairs_hook=pairs, parse_constant=reject)
        return IndicationDecision.model_validate(answer)
    except (ValueError, UnicodeError, RecursionError):
        return None


def training_checkpoint(training, training_manifest, target, target_sha256,
                        train_export, train_export_manifest, corpus_root,
                        corpus_manifest, expected_producer=None):
    """Data-only preflight before model load; pin producer explicitly if old."""
    root = Path(training)
    if verify(root) != training_manifest:
        raise ValueError('owner training artifact differs')
    report = read_json(root/'receipt.json')
    pin = producer_pin(expected_producer)
    if (report.get('schema') != 'power-grid-env.qwen35-adapter-training/v1' or
            report.get('passed') is not True or report.get('code_sha256') != pin or
            report.get('model') != target['model'] or
            report.get('target_metadata_sha256') != target_sha256 or
            report.get('config_sha256') != target['config_sha256'] or
            report.get('weights') != target['weights'] or
            read_json(root/'target.json') != target or
            report.get('export_manifest_sha256') != train_export_manifest or
            report.get('split') != 'train' or report.get('device') != 'cpu' or
            report.get('dtype') != 'float32' or
            report.get('full_model_tuned') is not False or
            report.get('frozen_backbone_unchanged') is not True or
            report.get('checkpoint_logits_bitwise_exact') is not True or
            report.get('forward_pass') is not True or report.get('backward_pass') is not True):
        raise ValueError('training revision, base checkpoint, data or qualification differs')
    plan = AdapterPlan.model_validate(report['plan'])
    data = VLMExportDataset(train_export, train_export_manifest)
    public = read_json(Path(train_export)/'receipt.json')
    corpus = verify_dataset(corpus_root, corpus_manifest)
    allowed = {r['id'] for r in corpus['rows'] if r['split'] == 'train'}
    ids = [data.record(i)['metadata']['task_id'] for i in range(len(data))]
    if (data.kind != 'sft' or data.split != 'train' or data.mode != report.get('mode') or
            public['dataset_manifest_sha256'] != corpus_manifest or
            not set(ids) <= allowed or report['optimizer_steps'] != len(plan.indices) or
            len(report['rows']) != len(plan.indices) or
            any(i < 0 or i >= len(data) for i in plan.indices)):
        raise ValueError('training split, step inventory or corpus lineage differs')
    for step, (index, row) in enumerate(zip(plan.indices, report['rows'], strict=True)):
        if (row['step'] != step or row['task_id'] != ids[index] or
                row['split'] != 'train' or row['kind'] != 'sft' or row['mode'] != data.mode or
                row['assistant_tokens'] <= 0 or not math.isfinite(row['loss']) or
                not math.isfinite(row['gradient_norm']) or row['gradient_norm'] <= 0):
            raise ValueError('training row is not a finite step on the pinned train export')
    adapter = root/'adapter.safetensors'
    if not 0 < adapter.stat().st_size <= 8_000_000 or file_sha256(adapter) != report['adapter_sha256']:
        raise ValueError('adapter bytes differ or exceed budget')
    return report, plan


def load_adapter_model(target, report, plan, training, threads):
    import torch
    from safetensors.torch import load_file
    torch.manual_seed(plan.seed)
    processor, model = load_native(target, threads)
    names = attach_adapters(model, plan)
    if (names != report['trainable_modules'] or
            sum(p.numel() for p in model.parameters() if p.requires_grad) != report['trainable_parameters'] or
            frozen_fingerprint(model) != report['frozen_backbone_sha256']):
        raise ValueError('adapter architecture or frozen native backbone differs')
    restore_adapters(model, load_file(str(Path(training)/'adapter.safetensors')))
    return processor, model


def generate_prediction(model, processor, dataset, index, plan, grader):
    """Same raw prompt and native greedy generation arguments as R06.63 base."""
    import torch
    batch, meta = dataset.pack(processor, index)
    if dataset.kind != 'prompts' or 'labels' in batch:
        raise ValueError('held-out labels cannot enter generation')
    tick = time.monotonic()
    with torch.no_grad():
        ids = model.generate(**batch, do_sample=False, num_beams=1,
            max_new_tokens=plan.maximum_new_tokens, use_cache=True,
            logits_to_keep=1, pad_token_id=processor.tokenizer.pad_token_id,
            eos_token_id=processor.tokenizer.eos_token_id)
    completion = ids[0, batch['input_ids'].shape[1]:]
    if not 0 < len(completion) <= plan.maximum_new_tokens or not torch.equal(
            ids[0, :batch['input_ids'].shape[1]], batch['input_ids'][0]):
        raise ValueError('native generation changed prompt or exceeded output budget')
    reply = processor.tokenizer.decode(completion, skip_special_tokens=True)
    reward = grader.reward_batch([reply], [meta['task_id']], dataset.mode)[0]
    return dict(**meta, completion=reply, generated_tokens=len(completion),
        token_budget_exhausted=len(completion) >= plan.maximum_new_tokens and
            int(completion[-1]) != processor.tokenizer.eos_token_id,
        reward=reward, valid=parse_decision(reply) is not None,
        elapsed_s=time.monotonic()-tick)


def evaluate_adapter(target_path, target_sha256, collection_root, collection_manifest,
                     corpus_root, corpus_manifest, training, training_manifest,
                     train_export, train_export_manifest, out, split='dev', plan=None,
                     expected_training_producer=None):
    start = time.monotonic()
    root = Path(out)
    if root.exists():
        raise ValueError('fresh adapter evaluation output required')
    producer = package_fingerprint()
    generation = GenerationPlan.model_validate(plan or {})
    target = local_target(target_path, target_sha256)
    report, adapter_plan = training_checkpoint(training, training_manifest, target,
        target_sha256, train_export, train_export_manifest, corpus_root, corpus_manifest,
        expected_training_producer)
    datasets = heldout_exports(collection_root, collection_manifest, corpus_root, corpus_manifest, split)
    grader = IndicationGrader(corpus_root, corpus_manifest)
    processor, model = load_adapter_model(target, report, adapter_plan, training, generation.threads)
    state = adapter_state(model)
    rows = []
    root.mkdir(parents=True, exist_ok=False)
    with (root/'predictions.jsonl').open('wb') as stream:
        for dataset in datasets:
            for index in range(len(dataset)):
                row = generate_prediction(model, processor, dataset, index, generation, grader)
                rows.append(row)
                stream.write(encoded(row)+b'\n')
                stream.flush()
                print('adapter evaluation', dataset.mode, index+1, '/', len(dataset),
                      'reward', row['reward'], 'valid', row['valid'], flush=True)
    metrics = summarize(rows, len(datasets[0]))
    import torch
    if (frozen_fingerprint(model) != report['frozen_backbone_sha256'] or
            any(not torch.equal(state[k], value) for k, value in adapter_state(model).items())):
        raise ValueError('inference modified model parameters')
    if (package_fingerprint() != producer or verify(collection_root) != collection_manifest or
            verify(corpus_root) != corpus_manifest or verify(training) != training_manifest or
            verify(train_export) != train_export_manifest or local_target(target_path, target_sha256) != target):
        raise ValueError('evaluation inputs or runtime changed')
    result = dict(schema='power-grid-env.qwen35-adapter-evaluation/v1', passed=True,
        code_sha256=producer, model=target['model'], target_metadata_sha256=target_sha256,
        collection_manifest_sha256=collection_manifest, corpus_manifest_sha256=corpus_manifest,
        training_manifest_sha256=training_manifest, training_code_sha256=report['code_sha256'],
        explicit_training_revision_pin=expected_training_producer is not None,
        train_export_manifest_sha256=train_export_manifest, adapter_sha256=report['adapter_sha256'],
        split=split, plan=generation.model_dump(), metrics=metrics, predictions=len(rows),
        device='cpu', dtype='float32', decoding='greedy', labels_passed_to_model=False,
        adapters_loaded=True, model_parameters_unchanged=True,
        versions={p: importlib.metadata.version(p) for p in ('transformers', 'torch', 'safetensors')},
        elapsed_s=time.monotonic()-start, model_improvement_claimed=False,
        scope='Complete raw-prompt adapter diagnosis; unchanged greedy budget and strict rewards. '
              'No test tuning, answer repair, general engineering or simulator superiority claim.')
    (root/'receipt.json').write_bytes(encoded(result))
    seal(root)
    return result


def audited_predictions(root, expected_manifest, corpus, corpus_manifest, expected_producer=None):
    root = Path(root)
    if verify(root) != expected_manifest:
        raise ValueError('owner prediction anchor differs')
    report = read_json(root/'receipt.json')
    if (report.get('schema') not in ('power-grid-env.qwen35-base-evaluation/v1',
                                     'power-grid-env.qwen35-adapter-evaluation/v1') or
            report.get('passed') is not True or report.get('code_sha256') != producer_pin(expected_producer) or
            report.get('corpus_manifest_sha256') != corpus_manifest or
            report.get('labels_passed_to_model') is not False or report.get('split') not in ('dev', 'test')):
        raise ValueError('prediction revision, split or labels differ')
    data = verify_dataset(corpus, corpus_manifest)
    targets = {r['id']: r['expected'] for r in data['rows'] if r['split'] == report['split']}
    grader = IndicationGrader(corpus, corpus_manifest)
    rows = [read_json_line(line) for line in (root/'predictions.jsonl').read_text().splitlines()]
    indexed = {}
    for row in rows:
        key = row['mode'], row['task_id']
        if key in indexed or row['task_id'] not in targets:
            raise ValueError('duplicate or foreign held-out prediction')
        decision = parse_decision(row['completion'])
        reward = grader.reward_batch([row['completion']], [row['task_id']], row['mode'])[0]
        if (type(row['valid']) is not bool or row['valid'] != (decision is not None) or
                type(row['reward']) not in (int, float) or row['reward'] != reward or
                row['kind'] != 'prompts' or row['split'] != report['split'] or
                row['assistant_tokens'] != 0 or type(row['token_budget_exhausted']) is not bool or
                type(row['generated_tokens']) is not int or
                not 1 <= row['generated_tokens'] <= report['plan']['maximum_new_tokens'] or
                (row['token_budget_exhausted'] and row['generated_tokens'] != report['plan']['maximum_new_tokens'])):
            raise ValueError('prediction reward, validity, output budget or supervision differs')
        expected = dict(motors={}, insufficient_observation=True) if row['mode'] == 'schematic' else targets[row['task_id']]
        diagnostic = dict(schema_valid=decision is not None,
                          missing_motors=[], unexpected_motors=[], wrong_indications=[],
                          insufficient_mismatch=False)
        if decision is not None:
            wanted = expected['motors']
            diagnostic.update(missing_motors=sorted(wanted.keys()-decision.motors.keys()),
                unexpected_motors=sorted(decision.motors.keys()-wanted.keys()),
                wrong_indications=sorted(k for k in wanted.keys() & decision.motors.keys()
                                         if wanted[k] != decision.motors[k]),
                insufficient_mismatch=decision.insufficient_observation != expected['insufficient_observation'])
        indexed[key] = dict(prediction=row, diagnosis=diagnostic)
    metrics = summarize(rows, len(targets))
    if (report['predictions'] != len(rows) or report['metrics'] != metrics or
            any({k[1] for k in indexed if k[0] == mode} != targets.keys() for mode in metrics)):
        raise ValueError('incomplete or selectively scored prediction set')
    return report, indexed


def read_json_line(line):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('duplicate prediction field')
            result[k] = v
        return result
    def reject(value):
        raise ValueError('nonfinite prediction constant')
    return json.loads(line, object_pairs_hook=pairs, parse_constant=reject)


def compare_evaluations(base, base_manifest, adapter, adapter_manifest, corpus,
                        corpus_manifest, out, expected_base_producer=None,
                        expected_adapter_producer=None):
    """Paired differences over every observation/mode, never selected successes."""
    a, left = audited_predictions(base, base_manifest, corpus, corpus_manifest, expected_base_producer)
    b, right = audited_predictions(adapter, adapter_manifest, corpus, corpus_manifest, expected_adapter_producer)
    protocol = ('model', 'target_metadata_sha256', 'collection_manifest_sha256',
                'corpus_manifest_sha256', 'split', 'plan', 'device', 'dtype', 'decoding')
    if (a['adapters_loaded'] is not False or b['adapters_loaded'] is not True or
            any(a[key] != b[key] for key in protocol) or left.keys() != right.keys()):
        raise ValueError('paired protocol, model identity or observation sets differ')
    rows = []
    for key in sorted(left):
        x, y = left[key]['prediction'], right[key]['prediction']
        if any(x[k] != y[k] for k in ('tokens', 'prompt_tokens', 'images', 'assistant_tokens', 'kind', 'split')):
            raise ValueError('paired prompt tokenization or modality differs')
        rows.append(dict(mode=key[0], task_id=key[1], base_reward=x['reward'], adapter_reward=y['reward'],
            base_valid=x['valid'], adapter_valid=y['valid'],
            base_diagnosis=left[key]['diagnosis'], adapter_diagnosis=right[key]['diagnosis']))
    metrics = {}
    for mode in a['metrics']:
        selected = [r for r in rows if r['mode'] == mode]
        diagnostics = [r['adapter_diagnosis'] for r in selected]
        metrics[mode] = dict(examples=len(selected), base=a['metrics'][mode], adapter=b['metrics'][mode],
            correct_delta=b['metrics'][mode]['correct']-a['metrics'][mode]['correct'],
            paired_wins=sum(r['adapter_reward'] > r['base_reward'] for r in selected),
            paired_regressions=sum(r['adapter_reward'] < r['base_reward'] for r in selected),
            paired_ties=sum(r['adapter_reward'] == r['base_reward'] for r in selected),
            adapter_missing_motor_answers=sum(bool(d['missing_motors']) for d in diagnostics),
            adapter_unexpected_motor_answers=sum(bool(d['unexpected_motors']) for d in diagnostics),
            adapter_wrong_indication_answers=sum(bool(d['wrong_indications']) for d in diagnostics),
            adapter_insufficient_mismatches=sum(d['insufficient_mismatch'] for d in diagnostics))
    result = dict(schema='power-grid-env.qwen35-paired-evaluation/v1', passed=True,
        code_sha256=package_fingerprint(), base_manifest_sha256=base_manifest,
        adapter_manifest_sha256=adapter_manifest, corpus_manifest_sha256=corpus_manifest,
        base_code_sha256=a['code_sha256'], adapter_code_sha256=b['code_sha256'],
        protocol={k: a[k] for k in protocol}, observations=len(rows), metrics=metrics, rows=rows,
        base_correct=sum(r['base_reward'] for r in rows), adapter_correct=sum(r['adapter_reward'] for r in rows),
        answers_repaired=False, examples_excluded=False, general_improvement_claimed=False,
        scope='Paired small authored dev diagnosis; topology/motor-count confounded, shared equipment. '
              'Observed differences do not qualify an engineering agent, test generalization or simulator superiority.')
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    (root/'receipt.json').write_bytes(encoded(result))
    if verify(base) != base_manifest or verify(adapter) != adapter_manifest or verify(corpus) != corpus_manifest:
        raise ValueError('paired evidence changed')
    seal(root)
    return {k: v for k, v in result.items() if k != 'rows'}
