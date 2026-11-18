"""Bind historical physics, multimodal exports, native processor and wheel."""
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import digest, encoded, read_json, seal, verify

CORPUS = 'reports/design-multimodal-qualified-current-2026-11-16'
SOURCE = 'reports/design-multimodal-exports-current-2026-11-16'
INSTALLED = 'reports/design-multimodal-exports-installed-current-2026-11-16'
NATIVE = 'reports/design-multimodal-native-processor-2026-11-16'
CORPUS_SHA = '79f4eae6580c6318060c16f74c0c9c272b18d5fcf20953665db62f887f74b31b'
EXPORTER_SHA = 'a3e547c23eb57401534a3d3c500fc3d77a761e1558259fcfcac94aa7b8a8e30b'
PHYSICS_SHA = '5ec69ae0f530223b1e527336270ee413e38a462d1399a5c8898dc08fd78613c1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def deliver(out):
    source_anchors = read_json('cases/design-multimodal/sources.json')
    roots = [CORPUS, SOURCE, INSTALLED, NATIVE,
             source_anchors['source'], source_anchors['accepted']]
    anchors = {root: verify(root) for root in roots}
    require(anchors[CORPUS] == CORPUS_SHA and package_fingerprint() == EXPORTER_SHA,
            'qualified corpus/runtime revision differs')
    require(anchors[source_anchors['source']] == source_anchors['source_manifest'] and
            anchors[source_anchors['accepted']] == source_anchors['accepted_manifest'],
            'historical physics anchors differ')
    corpus = read_json(Path(CORPUS) / 'corpus.json')
    source, installed, native = [read_json(Path(root) / 'receipt.json')
                                 for root in (SOURCE, INSTALLED, NATIVE)]
    require(corpus['physics_code_sha256'] == PHYSICS_SHA and
            corpus['exporter_code_sha256'] == EXPORTER_SHA,
            'physics/exporter provenance differs')
    require(len(corpus['rows']) == 60 and len(corpus['families']) == 3 and
            len(list((Path(CORPUS) / 'images').glob('*.png'))) == 62,
            'observation/asset inventory differs')
    require(corpus['source_reaudit']['verified_grid_runs'] == 45,
            'historical three-grid re-audit incomplete')
    for result in (source, installed):
        require(result['passed'] and result['exporter_code_sha256'] == EXPORTER_SHA and
                result['physics_code_sha256'] == PHYSICS_SHA and
                result['dataset_manifest_sha256'] == CORPUS_SHA and
                result['replayed_corpus_manifest_sha256'] == CORPUS_SHA and
                result['observations'] == 60 and result['families'] == 3 and
                result['split_rows'] == dict(train=36, dev=12, test=12) and
                result['positive_grade_checks'] == 240 and
                result['negative_grade_checks'] == 480 and not result['model_trained'],
                'export/replay coverage differs')
    require(installed['reference_imports_blocked'] and
            source['exports'] == installed['exports'] and len(source['exports']) == 12,
            'installed export bytes or isolation checks differ')
    for root, result in ((SOURCE, source), (INSTALLED, installed)):
        require(verify(Path(root) / 'replayed-corpus') == CORPUS_SHA,
                'replayed corpus bytes differ')
        for export in result['exports']:
            require(verify(Path(root) / export['path']) == export['manifest_sha256'],
                    'child export anchor differs')
    require(native['passed'] and native['exporter_code_sha256'] == EXPORTER_SHA and
            native['export_collection_manifest_sha256'] == anchors[SOURCE] and
            native['checks'] == 69 and native['image_observations'] == 60 and
            native['assistant_mask_checks'] == 39 and
            native['heldout_prompt_checks'] == 30 and len(native['rows']) == 69 and
            native['processor'] == 'Qwen3VLProcessor' and
            native['maximum_tokens'] == 2757 and native['maximum_images'] == 3 and
            not any(native[k] for k in ('weights_loaded', 'forward_pass', 'model_trained')),
            'native processor qualification differs')
    target = read_json('requirements/qwen35-local-target.json')
    require(native['config_sha256'] == target['config_sha256'],
            'owner checkpoint config differs')
    test = Path('reports/design-multimodal-regression-current-2026-11-16.xml')
    cases = list(ET.parse(test).getroot().iter('testcase'))
    require(len(cases) == 246 and not any(c.find(k) is not None
            for c in cases for k in ('failure', 'error', 'skipped')),
            '246 passing, non-skipped targeted regressions required')
    require(sum(c.attrib.get('classname', '').endswith(
            ('test_design_multimodal', 'test_vlm_batches')) for c in cases) == 40,
            'new test inventory differs')
    old = Path('reports/network-construction-delivery-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    wheel = Path('reports/design-multimodal-wheel-current-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    old_sha, wheel_sha = digest(old.read_bytes()), digest(wheel.read_bytes())
    require(old_sha == '5bcb091c71b004544469a1feda992abf73e4a3721e782423eea52e0196ef9c73' and
            wheel_sha == 'b2f26d36c79860237a9c7ba9abba0cf53b9931c83c21042de6805074b1aeef83',
            'qualified wheel bytes differ')
    with ZipFile(old) as before_zip, ZipFile(wheel) as after_zip:
        before = {p: before_zip.read(p) for p in before_zip.namelist()
                  if p.startswith('power_grid_env/') and p.endswith('.py')}
        after = {p: after_zip.read(p) for p in after_zip.namelist()
                 if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed = sorted(p for p in before if before[p] != after.get(p))
    added = sorted(after.keys() - before.keys())
    require(changed == ['power_grid_env/motor_engineering.py',
                        'power_grid_env/motor_trace.py', 'power_grid_env/network_design.py'] and
            added == ['power_grid_env/design_frame.py', 'power_grid_env/design_multimodal.py',
                      'power_grid_env/design_multimodal_render.py', 'power_grid_env/vlm_batches.py'] and
            not before.keys() - after.keys() and
            all(Path(p).read_bytes() == raw for p, raw in after.items()),
            'runtime boundary differs from qualified wheel')
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(wheel, root / wheel.name)
    shutil.copyfile(test, root / test.name)
    sources = set(Path('benchmarks').glob('design_multimodal*.py')) | {
        Path('benchmarks/design_vlm_processor.py'), Path('tests/test_design_multimodal.py'),
        Path('tests/test_vlm_batches.py'), Path('pyproject.toml'),
        Path('requirements/qwen35-local-target.json'), Path('plans/CURRENT_STATUS.md'),
        Path('plans/agent-designer.yaml'), Path('README.md'), Path('README.en.md')}
    sources |= set(Path('cases/design-multimodal').glob('*'))
    for path in sorted(sources):
        dest = root / 'sources' / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    result = dict(passed=True, exporter_code_sha256=EXPORTER_SHA,
        physics_code_sha256=PHYSICS_SHA, wheel_sha256=wheel_sha,
        previous_wheel_sha256=old_sha, changed_runtime_files=changed,
        added_runtime_files=added, evidence_manifests=anchors,
        regression_xml_sha256=digest(test.read_bytes()), targeted_tests=246, new_tests=40,
        observations=60, png_assets=62, topology_families=3,
        split_rows=dict(train=36, dev=12, test=12), portable_exports=12,
        positive_grade_checks_per_runtime=240, negative_grade_checks_per_runtime=480,
        source_reaudited_grids=45, new_solver_integrations=0,
        bitwise_source_installed_corpus=True, bitwise_source_installed_exports=True,
        native_processor_checks=69, assistant_mask_checks=39, heldout_prompt_checks=30,
        processor_versions=native['versions'], checkpoint_config_sha256=native['config_sha256'],
        weights_loaded=False, model_trained=False, independent_host=False,
        external_origin_authenticated=False, closed_loop_GRPO_qualified=False,
        model_accuracy_measured=False,
        scope='Past-only displayed-indication observations on three small topology families; '
              'historical three-grid physics re-audit, portable exports, strict diagnostic rewards '
              'and native Qwen processor/loss-mask checks. No new physics qualification or model improvement claim.')
    (root / 'delivery.json').write_bytes(encoded(result))
    require(package_fingerprint() == EXPORTER_SHA and
            anchors == {path: verify(path) for path in roots}, 'evidence changed during delivery')
    seal(root)
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    print(deliver(**vars(parser.parse_args())))
