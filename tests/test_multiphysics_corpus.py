import json
import pytest
from power_grid_env.design_multimodal import verify_dataset,export
from power_grid_env.design_response_schema import enrich_export
from power_grid_env.study_artifacts import read_json,encoded,digest,seal,verify
from power_grid_env.vlm_batches import VLMExportDataset
from tests.test_design_multimodal import corpus


def reseal(root):
    (root/'manifest.json').unlink()
    return seal(root)


def upgrade(root):
    data=read_json(root/'corpus.json')
    data.update(schema='power-grid-env.design-multimodal-corpus/v2',physics_code_sha256s=['a'*64,'b'*64])
    row=data['rows'][0];frame=read_json(root/row['frame'])
    frame['source']['physics_code_sha256']='b'*64
    (root/row['frame']).write_bytes(encoded(frame));row['frame_sha256']=digest(encoded(frame))
    (root/'corpus.json').write_bytes(encoded(data))
    return reseal(root)


@pytest.mark.parametrize('mode',['image','text','combined','schematic'])
def test_mixed_physics_roundtrip_and_schema_preserve_each_source(corpus,tmp_path,mode):
    root,_=corpus;sha=upgrade(root)
    assert verify_dataset(root,sha)['physics_code_sha256s']==['a'*64,'b'*64]
    out=tmp_path/'raw';export(root,out,sha,mode=mode)
    d=VLMExportDataset(out,verify(out))
    assert d.record(0)['metadata']['physics_code_sha256']=='b'*64
    assert read_json(out/'receipt.json')['physics_code_sha256s']==['b'*64]
    enriched=tmp_path/'schema';enrich_export(root,sha,out,verify(out),enriched)
    new=VLMExportDataset(enriched,verify(enriched))
    assert new.record(0)['messages'][1]==d.record(0)['messages'][1]
    assert read_json(enriched/'receipt.json')['schema'].endswith('/v2')
    assert read_json(enriched/'receipt.json')['physics_code_sha256s']==['b'*64]
    dev=tmp_path/'dev';export(root,dev,sha,split='dev',kind='prompts',mode=mode)
    assert read_json(dev/'receipt.json')['physics_code_sha256s']==['a'*64]
    assert VLMExportDataset(dev,verify(dev)).record(0)['metadata']['physics_code_sha256']=='a'*64


@pytest.mark.parametrize('fault',['v1','missing','duplicate','unsorted','unused','primary','invalid','frame'])
def test_revision_inventory_cannot_weaken_old_integrity(corpus,fault):
    root,_=corpus;upgrade(root);data=read_json(root/'corpus.json')
    if fault=='v1':data['schema']='power-grid-env.design-multimodal-corpus/v1'
    elif fault=='missing':data.pop('physics_code_sha256s')
    elif fault=='duplicate':data['physics_code_sha256s'].append('b'*64)
    elif fault=='unsorted':data['physics_code_sha256s'].reverse()
    elif fault=='unused':data['physics_code_sha256s'].append('c'*64)
    elif fault=='primary':data['physics_code_sha256']='c'*64
    elif fault=='invalid':data['physics_code_sha256s']=[True]
    else:
        row=data['rows'][0];frame=read_json(root/row['frame']);frame['source']['physics_code_sha256']='c'*64
        (root/row['frame']).write_bytes(encoded(frame));row['frame_sha256']=digest(encoded(frame))
    (root/'corpus.json').write_bytes(encoded(data));sha=reseal(root)
    with pytest.raises(ValueError):verify_dataset(root,sha)


@pytest.mark.parametrize('fault',['v1','record','unused','primary'])
def test_export_revision_header_and_record_identity_are_both_required(corpus,tmp_path,fault):
    root,_=corpus;sha=upgrade(root);out=tmp_path/'export';export(root,out,sha,mode='text')
    header=read_json(out/'receipt.json')
    if fault=='v1':header['schema']='power-grid-env.design-multimodal-export/v1'
    elif fault=='unused':header['physics_code_sha256s']=['a'*64,'b'*64]
    elif fault=='primary':header['physics_code_sha256']='a'*64
    else:
        row=json.loads((out/'messages.jsonl').read_text());row['metadata']['physics_code_sha256']='c'*64
        raw=encoded(row)+b'\n';(out/'messages.jsonl').write_bytes(raw);header['messages_sha256']=digest(raw)
    (out/'receipt.json').write_bytes(encoded(header));sha=reseal(out)
    with pytest.raises(ValueError):VLMExportDataset(out,sha)
