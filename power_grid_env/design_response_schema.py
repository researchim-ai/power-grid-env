"""Explicit public response contracts for a separate train/dev curriculum.

Only static motor IDs and the existing class ontology enter the added text.
Images and SFT answers stay byte-identical. No test export is created. A new
collection anchor distinguishes this prompt protocol from the raw benchmark.
"""
from pathlib import Path
import json
import shutil

from power_grid_env.design_multimodal import verify_dataset
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import digest, encoded, read_json, seal, verify
from power_grid_env.vlm_batches import VLMExportDataset

PROTOCOL = 'power-grid-env.public-indication-response-schema/v1'


def response_schema(motor_ids):
    if (not isinstance(motor_ids, list) or not 1 <= len(motor_ids) <= 8 or
            any(not isinstance(k, str) or not 1 <= len(k) <= 128 for k in motor_ids) or
            len(set(motor_ids)) != len(motor_ids)):
        raise ValueError('one to eight distinct public motor IDs required')
    ids = sorted(motor_ids)
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object', 'additionalProperties': False,
        'required': ['insufficient_observation', 'motors'],
        'properties': {
            'insufficient_observation': {'type': 'boolean'},
            'motors': {'type': 'object', 'additionalProperties': False,
                'properties': {k: {'enum': ['disconnected', 'at_running_speed',
                                          'below_running_speed']} for k in ids}}},
        'allOf': [{'if': {'properties': {'insufficient_observation': {'const': True}}},
                   'then': {'properties': {'motors': {'maxProperties': 0}}},
                   'else': {'properties': {'motors': {'required': ids}}}}]}


def schema_text(motor_ids):
    return ('Response contract. Return exactly one compact JSON object, without '
            'Markdown fences, explanation or copied measurements. Use the existing '
            'indication rule, not connection alone as a speed class. If current '
            'indications are absent, set insufficient_observation=true and motors={}; '
            'otherwise set it false and classify every listed motor ID. '
            'The following JSON Schema specifies output shape and allowed classes; '
            'it contains no observed state or correct indication.\n' +
            encoded(response_schema(motor_ids)).decode())


def enrich_export(corpus, corpus_manifest, source, source_manifest, out):
    root = Path(out)
    if root.exists():
        raise ValueError('fresh response-schema export required')
    producer = package_fingerprint()
    data = verify_dataset(corpus, corpus_manifest)
    original = VLMExportDataset(source, source_manifest)
    receipt = read_json(Path(source)/'receipt.json')
    if 'response_protocol' in receipt:
        raise ValueError('response-schema protocol cannot be stacked on an enriched export')
    if original.split == 'test':
        raise ValueError('test response protocol stays closed until recipe freeze')
    if receipt['dataset_manifest_sha256'] != corpus_manifest:
        raise ValueError('source export and private owner corpus differ')
    rows = {r['id']: r for r in data['rows'] if r['split'] == original.split}
    if {original.record(i)['metadata']['task_id'] for i in range(len(original))} != rows.keys():
        raise ValueError('complete source split required')
    result = []
    assets = set()
    from jsonschema import Draft202012Validator
    for i in range(len(original)):
        record = original.record(i)
        task = rows[record['metadata']['task_id']]
        frame = read_json(Path(corpus)/task['frame'])
        ids = [m['id'] for m in frame['passport']['motors']]
        schema = response_schema(ids)
        Draft202012Validator.check_schema(schema)
        # Supervision is inherited unchanged, not synthesized from the schema.
        if original.kind == 'sft':
            answer = json.loads(record['messages'][1]['content'])
            Draft202012Validator(schema).validate(answer)
        record['messages'][0]['content'].append(dict(type='text', text=schema_text(ids)))
        record['metadata']['response_protocol'] = PROTOCOL
        record['metadata']['response_schema_sha256'] = digest(encoded(schema))
        assets.update(b['image'] for b in record['messages'][0]['content'] if b['type'] == 'image')
        result.append(record)
    raw = b''.join(encoded(r)+b'\n' for r in result)
    root.mkdir(parents=True, exist_ok=False)
    if assets:
        (root/'images').mkdir()
    for asset in sorted(assets):
        shutil.copyfile(Path(source)/asset, root/asset)
    (root/'messages.jsonl').write_bytes(raw)
    summary = dict(schema=receipt['schema'], passed=True,
        exporter_code_sha256=producer, response_protocol=PROTOCOL,
        dataset_manifest_sha256=corpus_manifest, source_export_manifest_sha256=source_manifest,
        split=original.split, kind=original.kind, mode=original.mode, rows=len(result),
        messages_sha256=digest(raw), static_motor_ids_added_to_text=True,
        numeric_state_added=False, answers_changed=False, images_changed=False)
    if receipt['schema'].endswith('/v2'):
        summary.update(physics_code_sha256=receipt['physics_code_sha256'],
                       physics_code_sha256s=receipt['physics_code_sha256s'])
    (root/'receipt.json').write_bytes(encoded(summary))
    if (verify(source) != source_manifest or verify(corpus) != corpus_manifest or
            package_fingerprint() != producer):
        raise ValueError('source data or schema exporter changed')
    seal(root)
    return summary


def build_collection(corpus, corpus_manifest, source, source_manifest, out):
    root = Path(out)
    if root.exists():
        raise ValueError('fresh response-schema collection required')
    if verify(source) != source_manifest:
        raise ValueError('owner raw collection differs')
    original = read_json(Path(source)/'receipt.json')
    if original['dataset_manifest_sha256'] != corpus_manifest:
        raise ValueError('owner corpus and raw collection disagree')
    selected = [r for r in original['exports'] if r['split'] in ('train', 'dev')]
    expected = {s+'-'+m for s in ('train', 'dev') for m in ('image', 'text', 'combined', 'schematic')}
    if len(selected) != 8 or {r['path'] for r in selected} != expected:
        raise ValueError('eight complete train/dev modality exports required')
    for row in selected:
        if row['path'] != row['split']+'-'+row['mode']:
            raise ValueError('unexpected source export path')
        data = VLMExportDataset(Path(source)/row['path'], row['manifest_sha256'])
        if (data.split != row['split'] or data.mode != row['mode'] or
                data.kind != ('sft' if row['split'] == 'train' else 'prompts') or
                len(data) != row['rows']):
            raise ValueError('source export header differs from its conversation data')
    root.mkdir(parents=True, exist_ok=False)
    exports = []
    for row in selected:
        target = root/row['path']
        receipt = enrich_export(corpus, corpus_manifest, Path(source)/row['path'],
                                row['manifest_sha256'], target)
        exports.append(dict(path=row['path'], manifest_sha256=verify(target),
            rows=receipt['rows'], split=receipt['split'], mode=receipt['mode'],
            kind=receipt['kind'], messages_sha256=receipt['messages_sha256']))
    summary = dict(schema='power-grid-env.response-schema-collection/v1', passed=True,
        exporter_code_sha256=package_fingerprint(), response_protocol=PROTOCOL,
        source_collection_manifest_sha256=source_manifest,
        dataset_manifest_sha256=corpus_manifest, exports=exports, test_exported=False,
        images_or_answers_changed=False, model_trained=False,
        scope='Explicit static-ID response schema; separate prompt protocol, no image-only state text or test inputs.')
    (root/'receipt.json').write_bytes(encoded(summary))
    if verify(source) != source_manifest or verify(corpus) != corpus_manifest:
        raise ValueError('response-schema collection sources changed')
    seal(root)
    return summary
