"""Offline replay of two-cage SI dynamics,physical spectra and label invariance."""
import copy
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from benchmarks.two_cage_winding_cases import jobs,negatives
from benchmarks.two_cage_winding_reference import TwoCageReference
from benchmarks.two_cage_winding_acceptance import assess,producers,permutation_pairs
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,seal,verify


def packet(args):
    j,path=args;path=Path(path);anchor=verify(path);stored=read_json(path/'summary.json')
    if 'reason' in j:
        if read_json(path/'study.json')!=j['study']:raise ValueError('negative owner differs')
        fresh=run_study(StudySpec.model_validate(j['study']));previous=read_json(path/'trace.json')
        if fresh['completed'] or fresh.get('reason')!=j['reason'] or previous['completed'] or previous.get('reason')!=j['reason'] or 'time_s' in fresh:raise ValueError('initial refusal replay differs')
    else:
        refs=[TwoCageReference(j['study']).run(max_step=.002),TwoCageReference(j['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
        for name,ref in zip(['reference','refined-reference'],refs):
            data=read_json(path/(name+'.json'))
            if data['events']!=ref.events or data['initialization']!=ref.initial:raise ValueError('independent root/event replay differs')
        rows=[]
        for method in ['radau','network_dae']:
            for grid in ([0] if j['kind']=='hold' else [0,1]):
                child=path/(method+'-'+str(grid));verify(child)
                owner=copy.deepcopy(j['study']);owner['coupled'].update(integration_method=method,step_s=.02 if j['kind']=='hold' else .002/2**grid)
                spec=StudySpec.model_validate(owner);r=read_json(child/'trace.json')
                if read_json(child/'study.json')!=spec.model_dump(mode='json') or r['settings']!=spec.coupled.model_dump(mode='json') or r['network_sha256']!=canonical_hash(spec.network) or r['code_sha256']!=package_fingerprint():raise ValueError('native owner/settings/network/runtime differs')
                result,checks=assess(j,owner,r,refs)
                if checks!=read_json(child/'comparisons.json') or not result['passed']:raise ValueError('independent numerical gate replay differs')
                rows.append(dict(method=method,grid=grid,**result))
        if rows!=stored['rows'] or any(stored[k]!=j[k] for k in ['id','mode','mutual','kind','permuted']):raise ValueError('derived summary differs')
    if not stored['passed'] or verify(path)!=anchor:raise ValueError('packet changed/failed')
    print('verified two cage',j['id'],flush=True)
    return dict(id=j['id'],passed=True,manifest_sha256=anchor)


def verify_corpus(source,out,workers=4):
    if not 1<=workers<=16:raise ValueError('workers must be1..16')
    source=Path(source);anchor=verify(source);s=read_json(source/'summary.json');hashes=producers();fp=package_fingerprint();declared=list(jobs())+list(negatives())
    if not s['passed'] or s['code_sha256']!=fp or s['producers']!=hashes:raise ValueError('producer/outcome differs')
    if len(s['rows'])!=45 or {r['id'] for r in s['rows']}!={j['id'] for j in declared} or {p.name for p in source.iterdir()}!={j['id'] for j in declared}|{'summary.json','manifest.json'}:raise ValueError('coverage/layout differs')
    for j in declared:
        item=next(r for r in s['rows'] if r['id']==j['id'])
        if verify(source/j['id'])!=item['manifest_sha256'] or any(item[k]!=v for k,v in read_json(source/j['id']/'summary.json').items()):raise ValueError('index/packet binding differs')
    with ProcessPoolExecutor(max_workers=workers) as pool:
        rows=[future.result() for future in as_completed([pool.submit(packet,(j,str(source/j['id']))) for j in declared])]
    pairs=permutation_pairs(source)
    if pairs!=s['permutation_pairs'] or not all(p['passed'] for p in pairs):raise ValueError('cage label invariance differs')
    if verify(source)!=anchor or package_fingerprint()!=fp or producers()!=hashes:raise ValueError('evidence/producer changed')
    result=dict(passed=True,code_sha256=fp,producers=hashes,source_manifest_sha256=anchor,verified_packets=45,
        stored_native_trajectories=120,independent_reference_replays=66,native_refusal_replays=12,rows=sorted(rows,key=lambda r:r['id']),
        scope='Offline all two-SI-flux reference root/trajectory replays;initial refusals rerun;owner/producer/inventory gates and all240 numerical comparisons,8-state spectra,hold/cage-loss identities and event-sided label permutation gates recomputed.No positive native rerun or source authentication/OEM/another-host proof.')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);(root/'summary.json').write_bytes(encoded(result));seal(root);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=4)
    r=verify_corpus(**vars(p.parse_args()));print(dict(passed=r['passed'],verified_packets=r['verified_packets']))
