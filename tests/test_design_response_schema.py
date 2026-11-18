import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from power_grid_env.design_multimodal import export
from power_grid_env.design_response_schema import response_schema, schema_text, enrich_export, build_collection
from power_grid_env.study_artifacts import read_json, seal, verify
from power_grid_env.vlm_batches import VLMExportDataset
from tests.test_design_multimodal import corpus


def test_schema_requires_exact_ids_classes_and_explicit_insufficiency():
    validator = Draft202012Validator(response_schema(['m2', 'm']))
    validator.validate(dict(motors=dict(m='disconnected', m2='at_running_speed'), insufficient_observation=False))
    validator.validate(dict(motors={}, insufficient_observation=True))
    for answer in (dict(motors={}, insufficient_observation=False),
                   dict(motors=dict(m='connected', m2='at_running_speed'), insufficient_observation=False),
                   dict(motors=dict(m='disconnected'), insufficient_observation=True),
                   dict(motors={}, insufficient_observation=True, reward=1)):
        with pytest.raises(ValidationError): validator.validate(answer)
    assert schema_text(['m', 'm2']) == schema_text(['m2', 'm'])
    assert 'speed_indication_pu' not in schema_text(['m', 'm2'])


@pytest.mark.parametrize('ids', [[], ['m', 'm'], ['m']*9, [''], [1]])
def test_public_motor_inventory_is_bounded(ids):
    with pytest.raises(ValueError): response_schema(ids)


@pytest.mark.parametrize('split,kind', [('train', 'sft'), ('dev', 'prompts')])
@pytest.mark.parametrize('mode', ['image', 'text', 'combined', 'schematic'])
def test_curriculum_only_adds_static_contract_and_preserves_assets_and_labels(corpus, tmp_path, split, kind, mode):
    private, private_sha = corpus
    source = tmp_path/'source'
    export(private, source, private_sha, split=split, kind=kind, mode=mode)
    out = tmp_path/'schema'
    result = enrich_export(private, private_sha, source, verify(source), out)
    original = VLMExportDataset(source, verify(source)).record(0)
    enriched = VLMExportDataset(out, verify(out)).record(0)
    content = enriched['messages'][0]['content']
    assert content[:-1] == original['messages'][0]['content']
    assert content[-1]['type'] == 'text' and 'JSON Schema' in content[-1]['text']
    assert enriched['messages'][1:] == original['messages'][1:]
    for asset in (source/'images').glob('*'):
        assert (out/'images'/asset.name).read_bytes() == asset.read_bytes()
    assert result['answers_changed'] is False and result['numeric_state_added'] is False
    assert not (out/'frames').exists() and not (out/'corpus.json').exists()
    if mode == 'image': assert 'speed_indication_pu' not in json.dumps(enriched['messages'])
    with pytest.raises(ValueError, match='stacked'):
        enrich_export(private, private_sha, out, verify(out), tmp_path/'double')
    assert not (tmp_path/'double').exists()


def test_test_protocol_cannot_be_opened_by_schema_curriculum(corpus, tmp_path):
    private, private_sha = corpus
    source = tmp_path/'test'
    export(private, source, private_sha, split='test', kind='prompts')
    with pytest.raises(ValueError, match='stays closed'):
        enrich_export(private, private_sha, source, verify(source), tmp_path/'forbidden')
    assert not (tmp_path/'forbidden').exists()
