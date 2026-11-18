"""Bind paired raw-prompt accuracy and separate schema-data preparation."""
from pathlib import Path
import json
import shutil
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from power_grid_env.evidence import package_fingerprint
from power_grid_env.qwen35_adapter_evaluation import audited_predictions
from power_grid_env.qwen35_training import file_sha256
from power_grid_env.study_artifacts import read_json, encoded, verify, seal

ROOTS = {name: Path('reports/qwen35-08b-'+name+'-2026-11-16') for name in (
    'adapter-dev', 'adapter-paired-dev', 'response-curriculum',
    'curriculum-source-processor', 'curriculum-installed-processor', 'adapter-installed-generation',
    'public-numeric-baseline')}
PROFILE = 'cases/design-multimodal/qwen35-08b-adapter-eval-profile.json'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def test_cases(path, expected):
    cases = list(ET.parse(path).getroot().iter('testcase'))
    require(len(cases) == expected and not any(c.find(k) is not None
                for c in cases for k in ('failure', 'error', 'skipped')),
            'passing non-skipped test inventory differs')
    return cases


def deliver(out):
    fp = package_fingerprint()
    profile = read_json(PROFILE)
    anchors = {str(root): verify(root) for root in ROOTS.values()}
    for root, sha in ((profile['base'], profile['base_manifest']),
                      (profile['training'], profile['training_manifest']),
                      (profile['corpus_root'], profile['corpus_manifest']),
                      (profile['collection_root'], profile['collection_manifest'])):
        require(verify(root) == sha, 'historical owner anchor differs')
        anchors[root] = sha
    results = {name: read_json(root/'receipt.json') for name, root in ROOTS.items()}
    require(all(r['passed'] and r.get('code_sha256', r.get('exporter_code_sha256')) == fp
                for r in results.values()), 'incomplete evidence or mixed runtime revisions')
    adapter, pair, curriculum = [results[name] for name in ('adapter-dev', 'adapter-paired-dev', 'response-curriculum')]
    require(adapter['predictions'] == 48 and adapter['split'] == 'dev' and adapter['adapters_loaded'] and
            adapter['plan'] == profile['plan'] and adapter['training_manifest_sha256'] == profile['training_manifest'] and
            adapter['training_code_sha256'] == profile['expected_training_producer'] and
            adapter['explicit_training_revision_pin'] and adapter['model_parameters_unchanged'],
            'raw adapter protocol or historical checkpoint differs')
    _, raw = audited_predictions(ROOTS['adapter-dev'], anchors[str(ROOTS['adapter-dev'])],
                                profile['corpus_root'], profile['corpus_manifest'])
    base, base_rows = audited_predictions(profile['base'], profile['base_manifest'],
        profile['corpus_root'], profile['corpus_manifest'], profile['expected_base_producer'])
    require(pair['base_manifest_sha256'] == profile['base_manifest'] and
            pair['adapter_manifest_sha256'] == anchors[str(ROOTS['adapter-dev'])] and
            pair['observations'] == 48 and pair['base_correct'] == sum(v['prediction']['reward'] for v in base_rows.values()) and
            pair['adapter_correct'] == sum(v['prediction']['reward'] for v in raw.values()) and
            not pair['answers_repaired'] and not pair['examples_excluded'], 'paired accuracy coverage differs')
    for mode, metric in pair['metrics'].items():
        require(metric['examples'] == 12 and metric['adapter'] == adapter['metrics'][mode] and
                metric['base'] == base['metrics'][mode] and
                metric['paired_wins']+metric['paired_regressions']+metric['paired_ties'] == 12,
                'per-modality paired denominator differs')
    require(len(curriculum['exports']) == 8 and not curriculum['test_exported'] and
            not curriculum['images_or_answers_changed'] and not curriculum['model_trained'],
            'response-schema preparation scope differs')
    old_collection = read_json(Path(profile['collection_root'])/'receipt.json')
    original = {e['path']: e for e in old_collection['exports']}
    for entry in curriculum['exports']:
        new_root = ROOTS['response-curriculum']/entry['path']
        old_root = Path(profile['collection_root'])/entry['path']
        require(verify(new_root) == entry['manifest_sha256'], 'enriched export differs')
        old_rows = [json.loads(s) for s in (old_root/'messages.jsonl').read_text().splitlines()]
        new_rows = [json.loads(s) for s in (new_root/'messages.jsonl').read_text().splitlines()]
        require(len(old_rows) == len(new_rows) == entry['rows'] == original[entry['path']]['rows'],
                'curriculum example denominator differs')
        for before, after in zip(old_rows, new_rows, strict=True):
            require(after['messages'][0]['content'][:-1] == before['messages'][0]['content'] and
                    after['messages'][1:] == before['messages'][1:] and
                    all(after['metadata'][k] == v for k, v in before['metadata'].items()),
                    'schema export changed original observations or supervision')
        require({p.name: file_sha256(p) for p in (new_root/'images').glob('*')} ==
                {p.name: file_sha256(p) for p in (old_root/'images').glob('*')}, 'image bytes changed')
    a, b = results['curriculum-source-processor'], results['curriculum-installed-processor']
    for native in (a, b):
        require(native['packs'] == 192 and native['train_loss_masks'] == 144 and native['dev_prompts'] == 48 and
                native['maximum_tokens'] == 2978 and native['native_processor'] == 'Qwen3VLProcessor' and
                native['curriculum_manifest_sha256'] == anchors[str(ROOTS['response-curriculum'])] and
                native['reference_imports_blocked'] and native['runtime_imports_verified'] and
                not native['weights_loaded'] and not native['model_trained'] and not native['test_exported'],
                'complete native curriculum masks/prompts differ')
    require(a['rows'] == b['rows'] and b['replayed_curriculum_manifest_sha256'] == anchors[str(ROOTS['response-curriculum'])],
            'installed curriculum rebuild or native packs differ')
    replay = results['adapter-installed-generation']
    require(replay['replays'] == 4 and replay['source_predictions_manifest_sha256'] == anchors[str(ROOTS['adapter-dev'])] and
            replay['generated_text_tokens_scores_exact'] and replay['reference_imports_blocked'] and
            replay['runtime_imports_verified'], 'bounded installed generation replay differs')
    policy = results['public-numeric-baseline']
    require(policy['predictions'] == 48 and policy['policy_inputs'] == 'public_messages_only' and
            not policy['private_targets_used_by_policy'] and not policy['OCR_implemented'] and
            policy['metrics'] == {m: dict(examples=12, correct=0 if m == 'image' else 12)
                                  for m in ('image', 'text', 'combined', 'schematic')},
            'public numeric arithmetic control differs')
    xml = Path('reports/qwen35-08b-adapter-regression-2026-11-16.xml')
    cases = test_cases(xml, 336)
    require(sum(c.attrib.get('classname', '').endswith(
        ('test_qwen35_adapter_evaluation', 'test_design_response_schema')) for c in cases) == 51,
        'new regression inventory differs')
    native_xml = Path('reports/qwen35-08b-adapter-native-tests-2026-11-16.xml')
    test_cases(native_xml, 90)
    policy_xml = Path('reports/qwen35-08b-public-baseline-tests-2026-11-16.xml')
    extra = test_cases(policy_xml, 4)
    require(not ({(c.get('classname'), c.get('name')) for c in cases} &
                 {(c.get('classname'), c.get('name')) for c in extra}), 'duplicate regression counts')
    previous = Path('reports/qwen35-08b-wheel-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    wheel = Path('reports/qwen35-08b-adapter-wheel-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    require(file_sha256(previous) == '44931a5cc4d6c7bfa7efce84a5c925601340b044c870df5f46a9ba98e9a67e5d' and
            file_sha256(wheel) == '75531b1cceebead06499309b2de8dfb0d7dd078c1a303169b54ab9822ea5eba7',
            'pinned wheel bytes differ')
    with ZipFile(previous) as x, ZipFile(wheel) as y:
        before = {p: x.read(p) for p in x.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
        after = {p: y.read(p) for p in y.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    added = sorted(after.keys()-before.keys())
    require(not before.keys()-after.keys() and all(before[p] == after[p] for p in before) and
            added == ['power_grid_env/design_response_schema.py', 'power_grid_env/qwen35_adapter_evaluation.py'] and
            all(Path(p).read_bytes() == raw for p, raw in after.items()),
            'existing generation, training or solver modules changed')
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    for path in (wheel, xml, native_xml, policy_xml):
        shutil.copyfile(path, root/path.name)
    sources = set(Path('benchmarks').glob('qwen35_*.py')) | set(Path('cases/design-multimodal').glob('*')) | {
        Path('tests/test_qwen35_adapter_evaluation.py'), Path('tests/test_design_response_schema.py'),
        Path('tests/test_qwen35_public_baseline.py'),
        Path('README.md'), Path('README.en.md'), Path('plans/CURRENT_STATUS.md'), Path('plans/agent-designer.yaml'),
        Path('requirements/qwen35-08b-local-target.json'), Path('requirements/qwen35-08b-training-tested.txt')}
    for source in sorted(sources):
        target = root/'sources'/source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    result = dict(passed=True, code_sha256=fp, wheel_sha256=file_sha256(wheel), added_runtime_files=added,
        evidence_manifests=anchors, targeted_tests=340, new_tests=55, native_stack_tests=90,
        raw_base_correct=pair['base_correct'], raw_adapter_correct=pair['adapter_correct'], paired_observations=48,
        paired_metrics=pair['metrics'], installed_generation_replays=4,
        public_numeric_control_metrics=policy['metrics'],
        response_schema_exports=8, response_schema_train_examples=144, response_schema_dev_prompts=48,
        native_curriculum_packs_per_runtime=192, curriculum_rebuild_bitwise_exact=True,
        new_training_steps=0, new_solver_integrations=0, test_evaluated=False,
        independent_host=False, GRPO_qualified=False, general_improvement_claimed=False,
        scope='Observed paired raw-dev diagnostic differences on one authored family; '
              'separate explicit-schema train/dev data preparation, not trained-schema accuracy or engineering qualification.')
    (root/'delivery.json').write_bytes(encoded(result))
    require(fp == package_fingerprint() and anchors == {p: verify(p) for p in anchors}, 'evidence changed')
    seal(root)
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--out', required=True)
    print(deliver(**vars(p.parse_args())))
