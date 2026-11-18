"""Seal full mixed-SFT, native continuation and same-schema dev evidence."""
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from power_grid_env.evidence import package_fingerprint
from power_grid_env.qwen35_adapter_evaluation import audited_predictions
from power_grid_env.qwen35_mixed_training import MixedData, MixedPlan, MODES, read_checkpoint
from power_grid_env.qwen35_training import file_sha256
from power_grid_env.study_artifacts import encoded, read_json, seal, verify

PROFILE = Path('cases/design-multimodal/qwen35-08b-mixed-profile.json')
ROOTS = {name: Path('reports/qwen35-mixed-'+name+'-2026-11-17')
         for name in ('train', 'base-dev', 'adapter-dev', 'paired-dev', 'installed-resume', 'installed-generation', 'public-numeric', 'coverage', 'diagnosis', 'constant-control')}


ROOTS['coverage'] = Path('reports/qwen35-mixed-coverage-final-2026-11-17')
ROOTS.update({name: Path('reports/qwen35-'+name+'-final-2026-11-17') for name in (
    'early-train', 'early-coverage', 'early-source-processor', 'early-installed-processor')})


def require(value, message):
    if not value:
        raise ValueError(message)


def passing_tests(paths):
    unique = set()
    for path in paths:
        cases = list(ET.parse(path).getroot().iter('testcase'))
        require(cases and all(all(c.find(k) is None for k in ('failure', 'error', 'skipped'))
                            for c in cases), 'tests failed, skipped or absent')
        unique.update((c.attrib['classname'], c.attrib['name']) for c in cases)
    return unique


def deliver(out, wheel_sha256):
    producer = package_fingerprint()
    p = read_json(PROFILE)
    anchors = {str(root): verify(root) for root in ROOTS.values()}
    require(verify(p['collection_root']) == p['collection_manifest'] and
            verify(p['corpus_root']) == p['corpus_manifest'] and
            file_sha256(p['target_path']) == p['target_sha256'], 'owner input anchors differ')
    anchors[p['collection_root']] = p['collection_manifest']
    anchors[p['corpus_root']] = p['corpus_manifest']
    r = {name: read_json(root/'receipt.json') for name, root in ROOTS.items()}
    require(all(v['passed'] is True and v['code_sha256'] == producer for v in r.values()),
            'mixed producers or incomplete artifacts')
    training, pair, resume = r['train'], r['paired-dev'], r['installed-resume']
    require(training['complete'] is True and training['completed_updates'] == 36 and
            training['consumed_examples'] == 144 and training['frozen_backbone_unchanged'] and
            not training['full_model_tuned'] and not training['heldout_evaluated'] and
            training['plan'] == p['training_plan'] and training['trainable_parameters'] == 100352,
            'complete fixed training recipe differs')
    data = MixedData(p['collection_root'], p['collection_manifest'])
    plan = MixedPlan.model_validate(p['training_plan'])
    schedule = data.schedule(plan)
    final = ROOTS['train']/training['last_checkpoint']
    report = read_json(final/'receipt.json')
    extra = {'rows', 'cursor', 'completed_updates', 'adapter_sha256', 'optimizer_sha256', 'rng_sha256'}
    header = {k: v for k, v in report.items() if k not in extra}
    directories = sorted((ROOTS['train']/'checkpoints').iterdir())
    require(len(directories) == 36, 'per-update checkpoint inventory differs')
    for n, path in enumerate(directories, 1):
        require(path.name == f'update-{n:06d}', 'checkpoint cursor path differs')
        ck = read_checkpoint(path, verify(path), header, schedule, plan)
        require(ck['completed_updates'] == n, 'checkpoint history differs')
    require({m: sum(x['mode'] == m for row in report['rows'] for x in row['examples']) for m in MODES}
            == dict.fromkeys(MODES, 36), 'modality training coverage differs')
    require(resume['history_exact'] and resume['same_host'] and resume['reference_imports_blocked'] and
            not resume['test_used'] and all(v['tensors_bitwise_exact'] and v['files_byte_exact']
            for v in resume['comparisons'].values()) and
            resume['reference_manifest_sha256'] == verify(ROOTS['train']/'checkpoints/update-000002') and
            resume['start_manifest_sha256'] == verify(ROOTS['train']/'checkpoints/update-000001'),
            'native installed continuation proof differs')
    generation = r['installed-generation']
    require(generation['replays'] == 4 and generation['text_tokens_scores_exact'] and
            generation['selection'] == 'first_index_0_of_each_modality' and
            generation['source_predictions_manifest_sha256'] == anchors[str(ROOTS['adapter-dev'])] and
            generation['training_manifest_sha256'] == anchors[str(ROOTS['train'])] and
            generation['reference_imports_blocked'] and generation['same_host'] and
            not generation['test_evaluated'], 'bounded installed generation replay differs')
    coverage = r['coverage']
    require(coverage['split'] == 'train' and coverage['views'] == 144 and
            coverage['distinct_observations'] == 36 and not coverage['test_read'] and
            coverage['collection_manifest_sha256'] == p['collection_manifest'] and
            coverage['missing_train_indication_classes'] == ['disconnected'],
            'train coverage audit differs')
    numeric = r['public-numeric']
    require(numeric['collection_manifest_sha256'] == p['collection_manifest'] and
            numeric['predictions'] == 48 and numeric['policy_inputs'] == 'public_messages_only' and
            not numeric['private_targets_used_by_policy'] and not numeric['OCR_implemented'] and
            numeric['metrics'] == {m: dict(examples=12, correct=0 if m == 'image' else 12)
                                   for m in MODES}, 'same-schema numeric control differs')
    predictions = {}
    for name in ('base-dev', 'adapter-dev'):
        receipt, rows = audited_predictions(ROOTS[name], anchors[str(ROOTS[name])],
                                            p['corpus_root'], p['corpus_manifest'])
        require(receipt['split'] == 'dev' and receipt['predictions'] == 48 and
                receipt['collection_manifest_sha256'] == p['collection_manifest'] and
                receipt['plan'] == p['generation_plan'], 'same-schema full dev protocol differs')
        predictions[name] = rows
    require(pair['base_manifest_sha256'] == anchors[str(ROOTS['base-dev'])] and
            pair['adapter_manifest_sha256'] == anchors[str(ROOTS['adapter-dev'])] and
            pair['observations'] == 48 and not pair['answers_repaired'] and not pair['examples_excluded'],
            'paired comparison anchors or denominator differ')
    for key, name in (('base_correct', 'base-dev'), ('adapter_correct', 'adapter-dev')):
        require(pair[key] == sum(v['prediction']['reward'] for v in predictions[name].values()),
                'paired score differs from independent grading')
    a, b = predictions['base-dev'], predictions['adapter-dev']
    require(a.keys() == b.keys() and all(all(a[k]['prediction'][v] == b[k]['prediction'][v]
            for v in ('tokens', 'prompt_tokens', 'images')) for k in a), 'paired inputs differ')
    diagnosis = r['diagnosis']
    require(diagnosis['base_manifest_sha256'] == anchors[str(ROOTS['base-dev'])] and
            diagnosis['adapter_manifest_sha256'] == anchors[str(ROOTS['adapter-dev'])] and
            not diagnosis['rewards_changed'] and not diagnosis['test_evaluated'],
            'post-hoc diagnosis anchors differ')
    for label, name in (('base', 'base-dev'), ('adapter', 'adapter-dev')):
        require(all(diagnosis['metrics'][label][m]['joint_correct'] == r[name]['metrics'][m]['correct']
                    for m in MODES), 'diagnostic joint rewards changed')
    constant = r['constant-control']
    require(constant['adapter_manifest_sha256'] == anchors[str(ROOTS['adapter-dev'])] and
            constant['predictions_per_policy'] == 48 and constant['post_hoc_control'] and
            constant['policy_inputs'] == 'public_static_ID_schema_only' and
            not constant['observations_read_by_policy'] and not constant['private_targets_used_by_policy'] and
            not constant['rewards_changed'] and not constant['test_evaluated'] and
            constant['constant_totals'] == dict(at_running_speed=9, below_running_speed=15, disconnected=0) and
            constant['adapter_answers_matching_all_running'] == 46 and
            constant['unique_adapter_replies'] == 2, 'class-prior failure control differs')
    early = r['early-train']
    early_profile = read_json('cases/design-multimodal/qwen35-08b-early-profile.json')
    require(early['added_train_observations'] == 27 and early['train_views'] == 252 and
            early['dev_prompts'] == 48 and early['original_heldout_frames_and_images_unchanged'] and
            not early['test_exported'] and not early['model_trained'] and
            early_profile['collection_manifest'] == early['curriculum_manifest_sha256'] and
            early['new_solver_integrations'] == 0, 'early train augmentation scope differs')
    from power_grid_env.design_multimodal import verify_dataset
    old_corpus = verify_dataset(p['corpus_root'], p['corpus_manifest'])
    new_corpus = verify_dataset(early_profile['corpus_root'], early_profile['corpus_manifest'])
    for row in old_corpus['rows']:
        require(row in new_corpus['rows'] and
                (Path(p['corpus_root'])/row['frame']).read_bytes() ==
                (Path(early_profile['corpus_root'])/row['frame']).read_bytes(),
                'old private observation changed in augmented corpus')
    new_rows = [row for row in new_corpus['rows'] if row not in old_corpus['rows']]
    require(len(new_rows) == 27 and all(row['split'] == 'train' for row in new_rows),
            'held-out augmentation or new observation count differs')
    require(new_corpus['train_augmentation']['cutoffs_s'] == [0., .2, .399],
            'early sample display-time protocol differs')
    for mode in MODES:
        old_root = Path(p['collection_root'])/('dev-'+mode)
        new_root = Path(early_profile['collection_root'])/('dev-'+mode)
        import json
        old_records = [json.loads(line) for line in (old_root/'messages.jsonl').read_text().splitlines()]
        new_records = [json.loads(line) for line in (new_root/'messages.jsonl').read_text().splitlines()]
        require(len(old_records) == len(new_records) == 12, 'dev denominator changed')
        for a, b in zip(old_records, new_records, strict=True):
            require(a['metadata']['task_id'] == b['metadata']['task_id'] and
                    a['messages'] == b['messages'], 'public dev inputs changed')
            for block in a['messages'][0]['content']:
                if block['type'] == 'image':
                    require((old_root/block['image']).read_bytes() ==
                            (new_root/block['image']).read_bytes(), 'public dev pixels changed')
    ea, eb = r['early-source-processor'], r['early-installed-processor']
    require(ea['rows'] == eb['rows'] and ea['packs'] == eb['packs'] == 300 and
            ea['train_loss_masks'] == eb['train_loss_masks'] == 252 and
            ea['dev_prompts'] == eb['dev_prompts'] == 48 and
            eb['rebuilt_manifest_sha256'] == anchors[str(ROOTS['early-train'])] and
            not ea['model_weights_loaded'] and not eb['model_weights_loaded'] and
            not ea['test_exported'] and not eb['test_exported'] and
            ea['reference_imports_blocked'] and eb['reference_imports_blocked'],
            'complete early native packs or installed rebuild differ')
    require(r['early-coverage']['missing_train_indication_classes'] == [] and
            r['early-coverage']['distinct_observations'] == 63 and
            r['early-coverage']['modes']['text']['classes']['disconnected'] == 9,
            'new train class coverage differs')
    regression = [Path('reports/qwen35-mixed-regression-2026-11-17.xml'),
                  Path('reports/qwen35-mixed-unit-final-2026-11-17.xml'),
                  Path('reports/qwen35-early-tests-final-2026-11-17.xml'),
                  Path('reports/qwen35-mixed-constant-tests-2026-11-17.xml')]
    native = [Path('reports/qwen35-mixed-native-tests-2026-11-17.xml'),
              Path('reports/qwen35-mixed-native-final-2026-11-17.xml'),
              Path('reports/qwen35-early-native-tests-final-2026-11-17.xml'),
              Path('reports/qwen35-mixed-constant-native-tests-2026-11-17.xml')]
    tests, native_tests = passing_tests(regression), passing_tests(native)
    require(len(tests) == 400 and len(native_tests) == 135 and native_tests <= tests,
            'unique regression inventory differs')
    wheel = Path('reports/qwen35-mixed-wheel-2026-11-17/power_grid_env-0.1.0-py3-none-any.whl')
    previous = Path('reports/qwen35-08b-adapter-wheel-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    require(file_sha256(wheel) == wheel_sha256 and file_sha256(previous) ==
            '75531b1cceebead06499309b2de8dfb0d7dd078c1a303169b54ab9822ea5eba7', 'wheel bytes differ')
    with ZipFile(previous) as x, ZipFile(wheel) as y:
        old = {n: x.read(n) for n in x.namelist() if n.startswith('power_grid_env/') and n.endswith('.py')}
        new = {n: y.read(n) for n in y.namelist() if n.startswith('power_grid_env/') and n.endswith('.py')}
    require(not old.keys()-new.keys() and all(old[n] == new[n] for n in old) and
            sorted(new.keys()-old.keys()) == ['power_grid_env/qwen35_mixed_training.py'] and
            all(Path(n).read_bytes() == raw for n, raw in new.items()),
            'solver or existing generation/training runtime changed')
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    for path in [wheel, *regression, *native]:
        shutil.copyfile(path, root/path.name)
    sources = [PROFILE, Path('cases/design-multimodal/qwen35-08b-schema-numeric-profile.json'), Path('cases/design-multimodal/QWEN35_MIXED.md'),
        Path('tests/test_qwen35_mixed_training.py'), Path('tests/test_design_train_early.py'), Path('tests/test_qwen35_constant_control.py'),
        Path('cases/design-multimodal/early-train-profile.json'),
        Path('cases/design-multimodal/qwen35-08b-early-profile.json'),
        Path('benchmarks/design_train_early_observations.py'), Path('benchmarks/qwen35_early_processor.py'), Path('plans/CURRENT_STATUS.md'),
        Path('plans/agent-designer.yaml'), Path('README.md'), Path('README.en.md'),
        *Path('benchmarks').glob('qwen35_mixed*.py')]
    hashes = {}
    for path in sources:
        dest = root/'sources'/path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        hashes[str(path)] = file_sha256(path)
    result = dict(schema='power-grid-env.qwen35-mixed-delivery/v1', passed=True,
        code_sha256=producer, wheel_sha256=wheel_sha256, evidence_manifests=anchors,
        public_numeric_control_metrics=numeric['metrics'],
        constant_control_totals=constant['constant_totals'],
        adapter_answers_matching_all_running=constant['adapter_answers_matching_all_running'],
        semantic_quality_gate_passed=False, public_schema_diagnostics=diagnosis['metrics'],
        missing_train_indication_classes=coverage['missing_train_indication_classes'],
        source_sha256=hashes, targeted_tests=len(tests), native_stack_tests=len(native_tests), new_tests=60,
        optimizer_updates=36, train_views=144, distinct_train_observations=36,
        complete_dev_generations_per_model=48, base_correct=pair['base_correct'],
        adapter_correct=pair['adapter_correct'], paired_metrics=pair['metrics'],
        same_host_installed_optimizer_resume_bitwise_exact=True, installed_generation_replays=4,
        prepared_early_train_views=252, prepared_early_observations=63,
        early_models_trained=False, early_native_packs_per_runtime=300,
        early_data_installed_rebuild_exact=True, public_dev_inputs_unchanged=True,
        new_solver_integrations=0, test_evaluated=False, independent_host=False,
        closed_loop_GRPO_qualified=False, general_improvement_claimed=False,
        scope='Complete bounded mixed-view SFT and same-schema dev evidence, not an engineering agent.')
    (root/'delivery.json').write_bytes(encoded(result))
    require(package_fingerprint() == producer and all(verify(p) == s for p, s in anchors.items()) and
            all(file_sha256(p) == s for p, s in hashes.items()), 'evidence or source changed')
    seal(root)
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--wheel-sha256', required=True)
    print(deliver(**vars(parser.parse_args())))
