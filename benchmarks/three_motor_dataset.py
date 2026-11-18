"""Append newly qualified train trajectories without altering held-out data.

Corpus v2 keeps exact per-frame physics revisions; it never attributes historic
traces to a newer runtime merely to make provenance uniform.
"""
import argparse
import copy
from pathlib import Path
import shutil

from power_grid_env.design_multimodal import verify_dataset,source_inventory,_asset,export
from power_grid_env.design_frame import public_frame,diagnosis
from power_grid_env.design_multimodal_render import passport_data,passport_png,waveform_png,rasterize_generated_svg
from power_grid_env.network_design_view import single_line_svg
from power_grid_env.design_response_schema import build_collection
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,digest,seal,verify

CUTS=[0.,.2,.399,.4,1.5,1.6,3.,4.]


def build(profile,out):
    p=read_json(profile)
    original=verify_dataset(p['corpus'],p['corpus_manifest'])
    fp,designs=source_inventory(p['source'],p['accepted'],p['source_manifest'],p['accepted_manifest'])
    accepted=read_json(Path(p['accepted'])/'receipt.json')
    if (verify(p['si'])!=p['si_manifest'] or
            accepted.get('independent_si_reference_manifest_sha256')!=p['si_manifest'] or
            accepted.get('independent_dynamic_reference') is not True):
        raise ValueError('independent SI acceptance must be owner-pinned')
    root=Path(out)
    if any(root.resolve().is_relative_to(Path(p[k]).resolve()) for k in ('corpus','source','accepted','si')):
        raise ValueError('new output outside immutable sources required')
    root.mkdir(parents=True,exist_ok=False)
    corpus=root/'corpus';corpus.mkdir()
    for name in ('images','frames'):shutil.copytree(Path(p['corpus'])/name,corpus/name)
    data=copy.deepcopy(original);producer=package_fingerprint()
    data.update(schema='power-grid-env.design-multimodal-corpus/v2',
        physics_code_sha256s=sorted({data['physics_code_sha256'],fp}),exporter_code_sha256=producer)
    data['protocol']['cutoffs_s']=CUTS
    if not set(original['protocol']['cutoffs_s'])<=set(CUTS):raise ValueError('old cutoff removed')
    for family in data['families']:
        family['designs']=sorted(set(family.get('designs',[]))|{d['id'] for d in designs if d['family_id']==family['id']})
    additions=[]
    for d in designs:
        if data['protocol']['family_splits'].get(d['family_id'])!='train':raise ValueError('new held-out family cannot enter training')
        graph=d['graph'];t,proposal=d['task'],d['proposal']
        sld=_asset(corpus,lambda path:rasterize_generated_svg(single_line_svg(t,proposal),path))
        for case in d['plan'].cases:
            refined=case.model_copy(deep=True);refined.study.induction.step_s/=4
            leaf=d['root']/'studies'/case.id/'grid-2'
            if read_json(leaf/'study.json')!=refined.study.model_dump(mode='json'):raise ValueError('refined source differs')
            trace=read_json(leaf/'result.json');passport=passport_data(t,proposal,case)
            image=_asset(corpus,lambda path:passport_png(passport,path))
            study=digest(encoded(refined.study.model_dump(mode='json')))
            for cutoff in CUTS:
                frame=public_frame(refined,trace,cutoff,'post_event')
                frame.update(graph=graph,passport=passport,source=dict(physics_code_sha256=fp,
                    design_manifest_sha256=d['manifest'],study_sha256=study,trace_sha256=digest((leaf/'result.json').read_bytes())))
                id_=digest(encoded(dict(design=d['manifest'],study=study,cutoff=cutoff,side='post_event')))[:24]
                if any(r['id']==id_ for r in data['rows']):raise ValueError('observation collision')
                wave=_asset(corpus,lambda path:waveform_png(frame,path))
                name='frames/'+id_+'.json';raw=encoded(frame);(corpus/name).write_bytes(raw)
                row=dict(id=id_,family_id=d['family_id'],split='train',frame=name,frame_sha256=digest(raw),
                    images=dict(sld=sld,passport=image,waveform=wave),expected=diagnosis(frame).model_dump(mode='json'))
                data['rows'].append(row);additions.append(row)
    if len(additions)!=96:raise ValueError('two designs six scenarios eight cutoffs required')
    for row in original['rows']:
        if row not in data['rows'] or (corpus/row['frame']).read_bytes()!=(Path(p['corpus'])/row['frame']).read_bytes():
            raise ValueError('existing observation or target changed')
        for asset in row['images'].values():
            if (corpus/asset).read_bytes()!=(Path(p['corpus'])/asset).read_bytes():raise ValueError('existing image changed')
    data['train_extension']=dict(parent_manifest_sha256=p['corpus_manifest'],added_rows=96,
        source_manifest_sha256=p['source_manifest'],acceptance_manifest_sha256=p['accepted_manifest'],
        independent_si_manifest_sha256=p['si_manifest'],physics_code_sha256=fp,
        added_ids=[r['id'] for r in additions],cutoffs_s=CUTS,old_rows_unchanged=True,
        new_grid_runs=36,heldout_relabelled=False)
    (corpus/'corpus.json').write_bytes(encoded(data));corpus_sha=seal(corpus)
    verify_dataset(corpus,corpus_sha)
    raw=root/'raw-exports';raw.mkdir();exports=[]
    for split in ('train','dev'):
        for mode in ('image','text','combined','schematic'):
            dest=raw/(split+'-'+mode)
            receipt=export(corpus,dest,corpus_sha,split=split,kind='sft' if split=='train' else 'prompts',mode=mode)
            exports.append(dict(path=dest.name,split=split,mode=mode,kind=receipt['kind'],rows=receipt['rows'],manifest_sha256=verify(dest)))
    (raw/'receipt.json').write_bytes(encoded(dict(dataset_manifest_sha256=corpus_sha,exports=exports)))
    raw_sha=seal(raw)
    build_collection(corpus,corpus_sha,raw,raw_sha,root/'curriculum')
    summary=dict(passed=True,code_sha256=producer,profile_sha256=digest(Path(profile).read_bytes()),
        corpus_manifest_sha256=corpus_sha,curriculum_manifest_sha256=verify(root/'curriculum'),
        raw_manifest_sha256=raw_sha,added_train_observations=96,train_observations=159,
        train_views=636,dev_prompts=48,physics_code_sha256s=data['physics_code_sha256s'],
        source_manifest_sha256=p['source_manifest'],independent_si_manifest_sha256=p['si_manifest'],
        test_exported=False,model_trained=False,heldout_frames_unchanged=True,
        scope='Two and three motors in train P3 family; rotating openings for each ID. '
              '159 correlated time prefixes,21 study traces,not159 independent networks.')
    for key,expected in (('corpus',p['corpus_manifest']),('source',p['source_manifest']),('accepted',p['accepted_manifest']),('si',p['si_manifest'])):
        if verify(p[key])!=expected:raise ValueError('source changed')
    if package_fingerprint()!=producer:raise ValueError('exporter changed')
    (root/'receipt.json').write_bytes(encoded(summary));seal(root)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--profile',required=True);p.add_argument('--out',required=True)
    print(build(**vars(p.parse_args())))
