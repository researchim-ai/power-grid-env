"""Validate every modality/split export and optionally reproduce the corpus."""
import argparse
import importlib.abc
import json
from pathlib import Path
import sys


def run(dataset,expected_manifest,out,installed_dir=None,rebuild=False):
    if installed_dir:
        installed=Path(installed_dir).resolve();sys.path.insert(0,str(installed))
        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self,fullname,path=None,target=None):
                if fullname.split('.')[0] in {'benchmarks','tests','andes','motulator'}:raise ImportError('reference imports blocked')
        sys.meta_path.insert(0,Block())
    import power_grid_env.design_multimodal as api
    from power_grid_env.design_frame import diagnosis
    from power_grid_env.study_artifacts import read_json,encoded,verify,seal
    from power_grid_env.evidence import package_fingerprint
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    data=api.verify_dataset(dataset,expected_manifest);grader=api.IndicationGrader(dataset,expected_manifest)
    if installed_dir and not Path(api.__file__).resolve().is_relative_to(installed):raise ValueError('wrong installed runtime')
    if len(data['rows'])!=60 or len(data['families'])!=3 or data['source_reaudit']['verified_grid_runs']!=45:raise ValueError('qualified observation inventory differs')
    expected_splits=dict(train=36,dev=12,test=12)
    if {s:sum(r['split']==s for r in data['rows']) for s in expected_splits}!=expected_splits:raise ValueError('family split counts differ')
    exports=[];positive=negative=0
    for split in ('train','dev','test'):
        kind='sft' if split=='train' else 'prompts'
        for mode in api.MODES:
            name=split+'-'+mode;target=root/name
            receipt=api.export(dataset,target,expected_manifest,split,kind,mode)
            rows=[json.loads(line) for line in (target/'messages.jsonl').read_text().splitlines()]
            selected=[r for r in data['rows'] if r['split']==split]
            for original,record in zip(selected,rows,strict=True):
                frame=read_json(Path(dataset)/original['frame']);expected=api.messages(frame,original['images'],mode)
                answer=diagnosis(frame,mode).model_dump(mode='json')
                if kind=='sft':expected.append(dict(role='assistant',content=encoded(answer).decode()))
                if record['messages']!=expected or record['metadata']['task_id']!=original['id']:raise ValueError('public export differs')
                if kind=='prompts' and len(record['messages'])!=1:raise ValueError('held-out target leaked')
                if mode=='image' and 'speed_indication_pu' in json.dumps(record['messages'][0]):raise ValueError('numeric state leaked into image-only input')
                if not grader.score(original['id'],answer,mode)['reward']==1.:raise ValueError('oracle diagnostic grade differs')
                for wrong in (dict(motors={},insufficient_observation=False),answer|dict(threshold=0.)):
                    if grader.score(original['id'],wrong,mode)['reward']!=0.:raise ValueError('invalid answer earned reward')
                    negative+=1
                positive+=1
            if (target/'corpus.json').exists() or (target/'frames').exists():raise ValueError('private grading data shipped')
            exports.append(dict(path=name,manifest_sha256=verify(target),rows=receipt['rows'],mode=mode,split=split,kind=kind,messages_sha256=receipt['messages_sha256']))
    try:api.export(dataset,root/'forbidden-heldout',expected_manifest,split='test',kind='sft')
    except ValueError:pass
    else:raise ValueError('held-out SFT was allowed')
    if (root/'forbidden-heldout').exists():raise ValueError('forbidden export wrote artifacts')
    replay=None
    if rebuild:
        sources=data['source_manifests'];source=next(p for p in sources if (Path(p)/'summary.json').exists());accepted=next(p for p in sources if (Path(p)/'receipt.json').exists())
        replay=api.build(source,accepted,sources[source],sources[accepted],data['protocol'],root/'replayed-corpus')
        if replay['manifest_sha256']!=expected_manifest:raise ValueError('replayed observations/images differ')
    if installed_dir and any(not Path(m.__file__).resolve().is_relative_to(installed) for name,m in sys.modules.items() if name.startswith('power_grid_env') and getattr(m,'__file__',None)):raise ValueError('mixed source/installed runtime')
    result=dict(passed=True,exporter_code_sha256=package_fingerprint(),physics_code_sha256=data['physics_code_sha256'],dataset_manifest_sha256=expected_manifest,
        observations=60,families=3,split_rows=expected_splits,exports=exports,positive_grade_checks=positive,negative_grade_checks=negative,
        replayed_corpus_manifest_sha256=replay['manifest_sha256'] if replay else None,reference_imports_blocked=bool(installed_dir),
        model_trained=False,independent_host=False,scope='Public data/diagnostic grading checks; no VLM accuracy or closed-loop reward qualification')
    if positive!=240 or negative!=480 or verify(dataset)!=expected_manifest:raise ValueError('export coverage/source changed')
    (root/'receipt.json').write_bytes(encoded(result));seal(root);return {k:v for k,v in result.items() if k!='exports'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',required=True);p.add_argument('--expected-manifest',required=True);p.add_argument('--out',required=True);p.add_argument('--installed-dir');p.add_argument('--rebuild',action='store_true')
    print(run(**vars(p.parse_args())))
