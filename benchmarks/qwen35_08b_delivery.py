"""Bind local 0.8B SFT, complete base dev evaluation and installed replays."""
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from power_grid_env.study_artifacts import read_json, encoded, digest, verify, seal
from power_grid_env.evidence import package_fingerprint
from power_grid_env.qwen35_training import file_sha256, local_target
from power_grid_env.qwen35_evaluation import summarize

PREFIX = 'reports/qwen35-08b-'
SUFFIX = '-2026-11-16'
ROOT_NAMES = ('adapter-training', 'train-epoch', 'base-dev', 'source-replay',
              'installed-replay', 'epoch-source-replay', 'epoch-installed-replay',
              'installed-processor', 'prediction-audit')
TARGET = 'requirements/qwen35-08b-local-target.json'
TARGET_SHA = 'e763c82b7f4f1d5acea273b43674e45bcdb949fc91d000cb7537ea54b5fa0262'
CORPUS = 'reports/design-multimodal-qualified-current-2026-11-16'
CORPUS_SHA = '79f4eae6580c6318060c16f74c0c9c272b18d5fcf20953665db62f887f74b31b'
COLLECTION = 'reports/design-multimodal-exports-current-2026-11-16'
COLLECTION_SHA = '041263d35e10c94d2be9c211c7d5e69f4d6f4767731c2a7362cb49767b754344'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def deliver(out):
    fp = package_fingerprint()
    target = local_target(TARGET, TARGET_SHA)
    roots = {name: Path(PREFIX+name+SUFFIX) for name in ROOT_NAMES}
    anchors = {str(path): verify(path) for path in roots.values()}
    require(verify(CORPUS) == CORPUS_SHA and verify(COLLECTION) == COLLECTION_SHA,
            'owner data anchors differ')
    anchors.update({CORPUS: CORPUS_SHA, COLLECTION: COLLECTION_SHA})
    receipts = {name: read_json(path/'receipt.json') for name, path in roots.items()}
    require(all(r['passed'] and r.get('code_sha256', r.get('exporter_code_sha256')) == fp
                for r in receipts.values()), 'mixed runtime revisions or unsuccessful evidence')
    export = next(r for r in read_json(Path(COLLECTION)/'receipt.json')['exports']
                  if r['path'] == 'train-image')
    for name, count in (('adapter-training', 2), ('train-epoch', 36)):
        result = receipts[name]
        require(result['model'] == 'Qwen/Qwen3.5-0.8B' and
                result['target_metadata_sha256'] == TARGET_SHA and
                result['export_manifest_sha256'] == export['manifest_sha256'] and
                result['split'] == 'train' and result['mode'] == 'image' and
                result['optimizer_steps'] == count and len(result['rows']) == count and
                result['plan']['indices'] == list(range(count)) and
                result['trainable_parameters'] == 100352 and
                len(result['trainable_modules']) == 7 and
                result['frozen_backbone_unchanged'] and result['checkpoint_logits_bitwise_exact'] and
                result['forward_pass'] and result['backward_pass'] and
                not result['full_model_tuned'] and not result['heldout_evaluated'] and
                file_sha256(roots[name]/'adapter.safetensors') == result['adapter_sha256'],
                'bounded train SFT coverage differs')
    for train, source, installed in (
            ('adapter-training', 'source-replay', 'installed-replay'),
            ('train-epoch', 'epoch-source-replay', 'epoch-installed-replay')):
        a, b = receipts[source], receipts[installed]
        for replay in (a, b):
            require(replay['training_manifest_sha256'] == anchors[str(roots[train])] and
                    replay['adapter_sha256'] == receipts[train]['adapter_sha256'] and
                    replay['loss'] == receipts[train]['last_example_loss_after'] and
                    replay['reference_imports_blocked'] and replay['runtime_imports_verified'],
                    'fresh-process adapter replay differs')
        require(a['logits_sha256'] == b['logits_sha256'] and a['observation'] == b['observation'],
                'source/installed selected logits are not bitwise identical')
    base = receipts['base-dev']
    import json
    predictions = [json.loads(line) for line in (roots['base-dev']/'predictions.jsonl').read_text().splitlines()]
    require(base['predictions'] == 48 and base['split'] == 'dev' and
            base['target_metadata_sha256'] == TARGET_SHA and
            base['collection_manifest_sha256'] == COLLECTION_SHA and
            base['corpus_manifest_sha256'] == CORPUS_SHA and
            base['plan'] == dict(maximum_new_tokens=96, threads=4) and
            base['metrics'] == summarize(predictions, 12) and
            not base['adapters_loaded'] and not base['labels_passed_to_model'],
            'complete unadapted dev protocol differs')
    native = receipts['installed-processor']
    require(native['checks'] == 69 and native['image_observations'] == 60 and
            native['assistant_mask_checks'] == 39 and native['heldout_prompt_checks'] == 30 and
            native['config_sha256'] == target['config_sha256'] and
            native['export_collection_manifest_sha256'] == COLLECTION_SHA,
            'native processor coverage differs')
    audit = receipts['prediction-audit']
    require(audit['evaluation_manifest_sha256'] == anchors[str(roots['base-dev'])] and
            audit['corpus_manifest_sha256'] == CORPUS_SHA and
            audit['predictions'] == 48 and audit['strict_metrics'] == base['metrics'] and
            audit['official_scores_unchanged'], 'failure audit or original scores differ')
    test = Path(PREFIX+'regression-complete'+SUFFIX+'.xml')
    cases = list(ET.parse(test).getroot().iter('testcase'))
    require(len(cases) == 285 and not any(c.find(k) is not None
                for c in cases for k in ('failure', 'error', 'skipped')),
            '285 passing targeted regressions required')
    require(sum(c.attrib.get('classname', '').endswith(
                ('test_qwen35_training', 'test_qwen35_evaluation')) for c in cases) == 39,
            'new test inventory differs')
    native_test = Path(PREFIX+'native-stack-tests'+SUFFIX+'.xml')
    native_cases = list(ET.parse(native_test).getroot().iter('testcase'))
    require(len(native_cases) == 52 and not any(c.find(k) is not None
                for c in native_cases for k in ('failure', 'error', 'skipped')),
            '52 passing native-stack tests required')
    wheel = Path(PREFIX+'wheel'+SUFFIX+'/power_grid_env-0.1.0-py3-none-any.whl')
    old = Path('reports/design-multimodal-wheel-current-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    require(file_sha256(old) == 'b2f26d36c79860237a9c7ba9abba0cf53b9931c83c21042de6805074b1aeef83' and
            file_sha256(wheel) == '44931a5cc4d6c7bfa7efce84a5c925601340b044c870df5f46a9ba98e9a67e5d',
            'qualified wheel bytes differ')
    with ZipFile(old) as x, ZipFile(wheel) as y:
        before = {p: x.read(p) for p in x.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
        after = {p: y.read(p) for p in y.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    require(not before.keys()-after.keys() and all(before[p] == after[p] for p in before) and
            sorted(after.keys()-before.keys()) == ['power_grid_env/qwen35_evaluation.py', 'power_grid_env/qwen35_training.py'] and
            all(Path(p).read_bytes() == raw for p, raw in after.items()),
            'existing solver/data runtime changed')
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(wheel, root/wheel.name)
    shutil.copyfile(test, root/test.name)
    shutil.copyfile(native_test, root/native_test.name)
    for name in ('adapter-training', 'train-epoch'):
        (root/name).mkdir()
        shutil.copyfile(roots[name]/'adapter.safetensors', root/name/'adapter.safetensors')
    sources = set(Path('benchmarks').glob('qwen35_*.py')) | set(Path('cases/design-multimodal').glob('*'))
    sources |= {Path('benchmarks/design_vlm_processor.py'), Path(TARGET),
        Path('requirements/qwen35-08b-training-tested.txt'), Path('pyproject.toml'),
        Path('tests/test_qwen35_training.py'), Path('tests/test_qwen35_evaluation.py'),
        Path('README.md'), Path('README.en.md'), Path('plans/CURRENT_STATUS.md'),
        Path('plans/agent-designer.yaml')}
    for path in sorted(sources):
        dest = root/'sources'/path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    result = dict(passed=True, code_sha256=fp, model=target['model'],
        target_metadata_sha256=TARGET_SHA, wheel_sha256=file_sha256(wheel),
        evidence_manifests=anchors, targeted_tests=285, new_tests=39,
        native_stack_tests=52,
        regression_xml_sha256=digest(test.read_bytes()), native_processor_checks=69,
        qualification_optimizer_steps=2, epoch_optimizer_steps=36,
        trainable_adapter_parameters=100352, frozen_base_unchanged=True,
        source_installed_adapter_replays=4, logits_bitwise_equal=True,
        base_dev_generations=48, base_dev_metrics=base['metrics'],
        base_failure_taxonomy=audit['failure_counts'],
        trained_adapter_dev_accuracy_measured=False, test_evaluated=False,
        optimizer_resume_qualified=False, full_model_tuned=False,
        GPU_qualified=False, GRPO_qualified=False, independent_host=False,
        external_origin_authenticated=False, model_improvement_claimed=False,
        scope='CPU 0.8B final-block adapter SFT on36 train images plus two-step qualification; '
              'complete unadapted four-modality dev diagnosis and exact source/installed adapter replay. '
              'No trained-model improvement, general engineering-agent or simulator-superiority claim.')
    (root/'delivery.json').write_bytes(encoded(result))
    require(fp == package_fingerprint() and anchors == {p: verify(p) for p in anchors},
            'evidence changed during delivery')
    seal(root)
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--out', required=True)
    print(deliver(**vars(p.parse_args())))
