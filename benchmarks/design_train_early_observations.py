"""Add train-only early observations from already independently audited traces.

No solver runs or edited labels. Existing dev/test frames and assets stay exact;
only train topology families get new time cutoffs. The original corpus stays
sealed. The resulting dataset and export collection have new owner anchors.
"""
import argparse
import copy
from pathlib import Path
import shutil

from power_grid_env.design_frame import public_frame, diagnosis
from power_grid_env.design_multimodal import (
    source_inventory, verify_dataset, _asset, export,
)
from power_grid_env.design_multimodal_render import waveform_png, passport_data
from power_grid_env.design_response_schema import build_collection
from power_grid_env.evidence import package_fingerprint
from power_grid_env.qwen35_training import file_sha256
from power_grid_env.study_artifacts import read_json, encoded, digest, seal, verify


def augment(corpus, corpus_manifest, source, source_manifest, accepted,
            accepted_manifest, out, cutoffs=(0., .2, .399)):
    original = verify_dataset(corpus, corpus_manifest)
    if (not isinstance(cutoffs, (list, tuple)) or not cutoffs or
            any(type(t) not in (int, float) for t in cutoffs) or
            list(cutoffs) != sorted(set(cutoffs)) or cutoffs[0] < 0 or
            any(t in original['protocol']['cutoffs_s'] for t in cutoffs) or
            len(cutoffs)+len(original['protocol']['cutoffs_s']) > 8):
        raise ValueError('new sorted finite early cutoffs, disjoint from old protocol, required')
    # ObservationProtocol below rejects NaN/inf and defines all supported times.
    from power_grid_env.design_multimodal import ObservationProtocol
    protocol = ObservationProtocol.model_validate(dict(original['protocol'],
        cutoffs_s=sorted([*original['protocol']['cutoffs_s'], *cutoffs])))
    producer = package_fingerprint()
    anchors = {str(Path(source).resolve()): source_manifest,
               str(Path(accepted).resolve()): accepted_manifest}
    if original['source_manifests'] != anchors:
        raise ValueError('augmentation must use the original owner-pinned physics sources')
    physics, designs = source_inventory(source, accepted, source_manifest, accepted_manifest)
    if physics != original['physics_code_sha256']:
        raise ValueError('original physics producer differs')
    root = Path(out).resolve()
    if any(root.is_relative_to(Path(p).resolve()) for p in (source, accepted, corpus)):
        raise ValueError('fresh output outside immutable sources required')
    root.mkdir(parents=True, exist_ok=False)
    for name in ('images', 'frames'):
        shutil.copytree(Path(corpus)/name, root/name)
    result = copy.deepcopy(original)
    result['protocol'] = protocol.model_dump(mode='json')
    result['exporter_code_sha256'] = producer
    result['source_reaudit']['auditor_code_sha256'] = producer
    additions = []
    for d in designs:
        if original['protocol']['family_splits'][d['family_id']] != 'train':
            continue
        for case in d['plan'].cases:
            leaf = d['root']/'studies'/case.id/'grid-2'
            refined = case.model_copy(deep=True)
            refined.study.induction.step_s /= 4
            study_hash = digest(encoded(refined.study.model_dump(mode='json')))
            if read_json(leaf/'study.json') != refined.study.model_dump(mode='json'):
                raise ValueError('refined physical study differs')
            trace = read_json(leaf/'result.json')
            trace_hash = digest((leaf/'result.json').read_bytes())
            matches = [r for r in original['rows'] if r['split'] == 'train' and
                read_json(Path(corpus)/r['frame'])['source']['design_manifest_sha256'] == d['manifest'] and
                read_json(Path(corpus)/r['frame'])['source']['study_sha256'] == study_hash]
            if not matches:
                raise ValueError('source case absent from original train corpus')
            template = read_json(Path(corpus)/matches[0]['frame'])
            if (trace['code_sha256'] != physics or template['graph'] != d['graph'] or
                    template['passport'] != passport_data(d['task'], d['proposal'], case) or
                    template['source']['trace_sha256'] != trace_hash):
                raise ValueError('original static assets or trace provenance differ')
            for cutoff in cutoffs:
                frame = public_frame(refined, trace, cutoff, protocol.side)
                frame.update(graph=template['graph'], passport=template['passport'], source=template['source'])
                id_ = digest(encoded(dict(design=d['manifest'], study=study_hash,
                    cutoff=float(cutoff), side=protocol.side)))[:24]
                if any(r['id'] == id_ for r in result['rows']):
                    raise ValueError('augmentation observation collision')
                wave = _asset(root, lambda path: waveform_png(frame, path))
                name = 'frames/'+id_+'.json'
                raw = encoded(frame)
                (root/name).write_bytes(raw)
                row = dict(id=id_, family_id=d['family_id'], split='train', frame=name,
                    frame_sha256=digest(raw), images=dict(matches[0]['images'], waveform=wave),
                    expected=diagnosis(frame).model_dump(mode='json'))
                additions.append(row)
                result['rows'].append(row)
    if not additions:
        raise ValueError('augmentation produced no new train observations')
    # Recompute targets from original trace prefixes, rather than editing answer
    # text. Confirm every old row, including held-out bytes/assets, is retained.
    for row in original['rows']:
        if row not in result['rows'] or (root/row['frame']).read_bytes() != (Path(corpus)/row['frame']).read_bytes():
            raise ValueError('original observation changed')
        for name in row['images'].values():
            if (root/name).read_bytes() != (Path(corpus)/name).read_bytes():
                raise ValueError('original image changed')
    result['train_augmentation'] = dict(schema='power-grid-env.early-train-observations/v1',
        parent_manifest_sha256=corpus_manifest, cutoffs_s=list(cutoffs),
        added_ids=[r['id'] for r in additions], added_rows=len(additions),
        heldout_rows_changed=False, labels_edited=False, new_solver_integrations=0,
        code_sha256=producer, builder_sha256=file_sha256(__file__))
    (root/'corpus.json').write_bytes(encoded(result))
    if (verify(corpus) != corpus_manifest or verify(source) != source_manifest or
            verify(accepted) != accepted_manifest or package_fingerprint() != producer):
        raise ValueError('owner sources or runtime changed')
    anchor = seal(root)
    verify_dataset(root, anchor)
    return dict(passed=True, manifest_sha256=anchor, added_train_observations=len(additions),
                original_heldout_frames_and_images_unchanged=True)


def run(profile, out):
    p = read_json(profile)
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    result = augment(**{k: p[k] for k in ('corpus', 'corpus_manifest', 'source', 'source_manifest',
                                         'accepted', 'accepted_manifest')}, out=root/'corpus', cutoffs=p.get('cutoffs_s', [0., .2, .399]))
    raw = root/'raw-exports'
    raw.mkdir()
    exports = []
    for split in ('train', 'dev'):
        for mode in ('image', 'text', 'combined', 'schematic'):
            path = raw/(split+'-'+mode)
            export(root/'corpus', path, result['manifest_sha256'], split=split,
                   kind='sft' if split == 'train' else 'prompts', mode=mode)
            receipt = read_json(path/'receipt.json')
            exports.append(dict(path=path.name, split=split, mode=mode, kind=receipt['kind'],
                                rows=receipt['rows'], manifest_sha256=verify(path)))
    (raw/'receipt.json').write_bytes(encoded(dict(dataset_manifest_sha256=result['manifest_sha256'], exports=exports)))
    raw_sha = seal(raw)
    build_collection(root/'corpus', result['manifest_sha256'], raw, raw_sha, root/'curriculum')
    summary = dict(passed=True, code_sha256=package_fingerprint(), profile_sha256=file_sha256(profile),
        corpus_manifest_sha256=result['manifest_sha256'], raw_collection_manifest_sha256=raw_sha,
        curriculum_manifest_sha256=verify(root/'curriculum'), **{k:v for k,v in result.items() if k != 'passed'},
        train_observations=63, train_views=252, dev_prompts=48, test_exported=False,
        model_trained=False, new_solver_integrations=0,
        scope='Three early cutoffs added to original train families from re-audited traces; '
              'held-out frames/pixels preserved, no model training or motor-count expansion.')
    corpus_data = verify_dataset(root/'corpus', result['manifest_sha256'])
    if (sum(r['split'] == 'train' for r in corpus_data['rows']) != 63 or
            any(e['split'] == 'test' for e in exports)):
        raise ValueError('fixed early-observation profile coverage differs')
    (root/'receipt.json').write_bytes(encoded(summary))
    seal(root)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    parser.add_argument('--out', required=True)
    print(run(**vars(parser.parse_args())))
