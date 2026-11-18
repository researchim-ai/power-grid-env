"""Bind the physics, negative learning diagnostics and new v2 data evidence."""
from pathlib import Path
import shutil
import json
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,verify,seal,digest
from power_grid_env.qwen35_training import file_sha256
from power_grid_env.design_multimodal import verify_dataset

OLD='3c4c6e217ac5979f49e3fe0eb947d6d3f2923dede281ae48a58efeea24c26e16'
ROOTS={k:Path('reports/'+v+'-2026-11-17') for k,v in dict(
    physics='three-motor-train',si='three-motor-si-reference',accepted='three-motor-accepted',
    replay='three-motor-installed-matched',dataset='three-motor-curriculum-final',
    source_processor='three-motor-source-processor',installed_processor='three-motor-installed-processor',
    coverage='three-motor-coverage',train_probes='qwen35-train-probes',likelihood='qwen35-likelihood-probes').items()}


def need(value,message):
    if not value:raise ValueError(message)


def tests(path,count):
    items=list(ET.parse(path).iter('testcase'))
    need(len(items)==count and all(all(c.find(k) is None for k in ('failure','error','skipped')) for c in items),'test inventory differs')
    return {(c.get('classname'),c.get('name')) for c in items}


def deliver(out):
    fp=package_fingerprint();anchors={str(v):verify(v) for v in ROOTS.values()}
    r={k:read_json(v/('summary.json' if k=='physics' else 'receipt.json')) for k,v in ROOTS.items()}
    for k,v in r.items():
        need(v['passed'] and v['code_sha256']==(OLD if k in ('physics','si','accepted','train_probes','likelihood') else fp),'producer or completeness differs: '+k)
    need(r['physics']['designs']==2 and r['physics']['dynamic_runs']==36 and
         [v['status'] for v in r['physics']['rows']]==['accepted','rejected'],'engineering verdicts changed')
    need(all(len(v['running_openings'])==3 and {p['motor_id'] for p in v['running_openings']}=={'m','m2','m3'} and
             all(p['speed_before_open']>=.9 and p['current_after_open']==0 for p in v['running_openings']) for v in r['physics']['rows']), 'running motor opening missing')
    need(r['si']['source_manifest_sha256']==anchors[str(ROOTS['physics'])] and r['si']['reference_trajectories']==12 and r['si']['grid_comparisons']==36 and
         r['si']['reference_sha256']==file_sha256('benchmarks/three_motor_si_reference.py'),'independent reference identity differs')
    errors={k:max(g['maximum_errors'][m][k] for c in r['si']['rows'] for g in c['rows'] for m in g['maximum_errors']) for k in r['si']['limits']}
    need(all(errors[k]<=r['si']['limits'][k] for k in errors),'independent trajectory error exceeded')
    need(r['accepted']['independent_si_reference_manifest_sha256']==anchors[str(ROOTS['si'])] and r['accepted']['independent_dynamic_reference'],'independent acceptance differs')
    replay=r['replay'];need(replay['records']==36 and replay['channels_and_events_bitwise_exact'] and replay['source_physics_code_sha256']==OLD,'installed replay differs')
    for row in replay['batches']:
        need(verify(row['path'])==row['manifest_sha256'],'installed batch changed')
        report=read_json(Path(row['path'])/'receipt.json')
        need(report['code_sha256']==fp and report['reference_imports_blocked'] and len(report['rows'])==6 and all(v['compared'] and v['common_channels_bitwise_exact'] for v in report['rows']),'batch not fully exact')
        anchors[row['path']]=row['manifest_sha256']
    p=read_json('cases/design-multimodal/qwen35-08b-three-motor-profile.json')
    data=verify_dataset(p['corpus_root'],p['corpus_manifest'])
    old_p=read_json('cases/design-multimodal/qwen35-08b-early-profile.json')
    old_data=verify_dataset(old_p['corpus_root'],old_p['corpus_manifest'])
    need(data['schema'].endswith('/v2') and len(data['physics_code_sha256s'])==2 and data['train_extension']['added_rows']==96,'versioned corpus scope differs')
    for row in old_data['rows']:
        need(row in data['rows'],'old row or target changed')
        for name in (row['frame'],*row['images'].values()):
            need((Path(old_p['corpus_root'])/name).read_bytes()==(Path(p['corpus_root'])/name).read_bytes(),'old frame/image changed')
    need({row['split'] for row in data['rows'] if row not in old_data['rows']}=={'train'},'held-out augmentation detected')
    for mode in ('image','text','combined','schematic'):
        left=Path(old_p['collection_root'])/('dev-'+mode);right=Path(p['collection_root'])/('dev-'+mode)
        a=[json.loads(v) for v in (left/'messages.jsonl').read_text().splitlines()]
        b=[json.loads(v) for v in (right/'messages.jsonl').read_text().splitlines()]
        need(len(a)==len(b)==12 and all(x['messages']==y['messages'] and x['metadata']['task_id']==y['metadata']['task_id'] for x,y in zip(a,b,strict=True)),'public dev conversations changed')
    a,b=r['source_processor'],r['installed_processor']
    need(a['packs']==b['packs']==684 and a['rows']==b['rows'] and a['train_loss_masks']==b['train_loss_masks']==636 and
         b['rebuilt_manifest_sha256']==anchors[str(ROOTS['dataset'])] and not a['model_weights_loaded'] and not b['model_weights_loaded'],'native packs or full rebuild differs')
    c=r['coverage'];need(c['modes']['text']['response_motor_counts']=={'2':63,'3':96} and c['missing_train_indication_classes']==[],'motor count/class coverage differs')
    probe=r['train_probes'];need(len(probe['rows'])==12 and probe['seen_train'] and not probe['labels_passed_to_model'] and not probe['test_evaluated'],'probe scope differs')
    need({m:v['correct'] for m,v in probe['metrics'].items()}==dict(image=1.,text=1.,combined=1.,schematic=0.),'seen training failure measurement differs')
    like=r['likelihood'];need(len(like['rows'])==4 and not like['model_updated'] and
         all(v['causal_prefix_max_abs_error']<=like['fp32_absolute_tolerance'] and v['two_step_cache_max_abs_error']<=like['fp32_absolute_tolerance'] and v['two_step_greedy_tokens_equal'] for v in like['rows']),'prefix/cache control failed')
    main=Path('reports/three-motor-regression-2026-11-17.xml');native=Path('reports/three-motor-native-tests-2026-11-17.xml')
    need(tests(native,160)<=tests(main,425),'native subset differs')
    wheel=Path('reports/three-motor-wheel-2026-11-17/power_grid_env-0.1.0-py3-none-any.whl')
    before=Path('reports/qwen35-mixed-wheel-2026-11-17/power_grid_env-0.1.0-py3-none-any.whl')
    with ZipFile(before) as x,ZipFile(wheel) as y:
        names={n for n in x.namelist() if n.startswith('power_grid_env/') and n.endswith('.py')}
        need(names=={n for n in y.namelist() if n.startswith('power_grid_env/') and n.endswith('.py')},'unexpected runtime module addition')
        changed=sorted(n for n in names if x.read(n)!=y.read(n))
        need(changed==['power_grid_env/design_multimodal.py','power_grid_env/design_response_schema.py','power_grid_env/vlm_batches.py'],'solver/neural runtime changed')
        need(all(y.read(n)==Path(n).read_bytes() for n in names),'wheel/source differ')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    for path in (wheel,main,native):shutil.copyfile(path,root/path.name)
    sources=[*Path('benchmarks').glob('three_motor*.py'),Path('benchmarks/qwen35_train_probe.py'),Path('benchmarks/qwen35_likelihood_probe.py'),
        Path('tests/test_multiphysics_corpus.py'),Path('tests/test_three_motor_training.py'),
        Path('cases/design-multimodal/three-motor-data-profile.json'),Path('cases/design-multimodal/qwen35-08b-three-motor-profile.json'),
        Path('cases/design-multimodal/THREE_MOTOR.md'),Path('plans/CURRENT_STATUS.md'),Path('plans/agent-designer.yaml'),Path('README.md'),Path('README.en.md')]
    hashes={}
    for path in sources:
        dest=root/'sources'/path;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dest);hashes[str(path)]=file_sha256(path)
    result=dict(passed=True,code_sha256=fp,source_physics_code_sha256=OLD,evidence_manifests=anchors,
        sources_sha256=hashes,wheel_sha256=file_sha256(wheel),changed_runtime_modules=changed,
        targeted_tests=425,new_tests=25,native_stack_tests=160,new_grid_runs=36,independent_si_trajectories=12,
        independent_grid_comparisons=36,independent_maximum_errors=errors,installed_bitwise_physics_replays=36,
        prepared_train_views=636,distinct_train_prefixes=159,study_traces=21,native_packs_per_runtime=684,
        public_dev_inputs_unchanged=True,model_updated=False,semantic_quality_gate_passed=False,test_evaluated=False,
        seen_train_probe_metrics=probe['metrics'],independent_host=False,
        scope='Bounded new motor physics and multi-revision data,negative seen-training diagnosis;no new model quality or universal simulator claim.')
    (root/'delivery.json').write_bytes(encoded(result))
    need(fp==package_fingerprint() and all(verify(p)==sha for p,sha in anchors.items()) and all(file_sha256(p)==sha for p,sha in hashes.items()),'evidence/source changed')
    seal(root)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);print(deliver(**vars(p.parse_args())))
