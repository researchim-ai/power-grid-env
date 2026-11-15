"""Offline consumer: replay independent equations and recompute every comparison."""
from pathlib import Path
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.mixed_winding_cases import jobs
from benchmarks.mixed_winding_reference import MixedReference
from benchmarks.mixed_winding_acceptance import compare


def accept(source,out):
    root=Path(source);anchor=verify(root);summary=read_json(root/'summary.json');declared=list(jobs())
    if not summary['passed'] or summary['code_sha256']!=package_fingerprint():raise ValueError('runtime/qualification mismatch')
    if any(digest(Path(p).read_bytes())!=h for p,h in summary['producers'].items()):raise ValueError('producer mismatch')
    if [r['id'] for r in summary['rows']]!=[r['id'] for r in declared]:raise ValueError('owner inventory mismatch')
    comparisons=0;replays=0;max_power_refinement=0.
    for job in declared:
        child=root/job['id'];verify(child)
        refs=[MixedReference(job['study']).run(max_step=.002),MixedReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
        for name,ref in zip(['reference','refined-reference'],refs):
            stored=read_json(child/(name+'.json'))
            if encoded(stored['events'])!=encoded(ref.events) or encoded(stored['initialization'])!=encoded(ref.initial):raise ValueError('independent event/init replay differs')
            for point in stored['regular_values']:
                if encoded(point['channels'])!=encoded(ref.sample(point['time_s'])):raise ValueError('independent numerical replay differs')
            replays+=1
        for point in read_json(child/'reference.json')['regular_values']:
            a,b=[r.sample(point['time_s']) for r in refs]
            for k in a:
                error=abs(a[k]-b[k]);limit=1e-4 if k.endswith(('_w','_var','.current_a','.measurement')) else 1e-7
                if error>limit:raise ValueError('independent refinement failed: '+k)
                if k.endswith(('_w','_var')):max_power_refinement=max(max_power_refinement,error)
        for method in ['radau','network_dae']:
            for grid in [0,1]:
                leaf=child/(method+'-'+str(grid));verify(leaf);study=read_json(leaf/'study.json');owner=job['study'].copy()
                import copy
                owner=copy.deepcopy(owner);owner['coupled'].update(integration_method=method,step_s=.002/2**grid)
                from power_grid_env.study_spec import StudySpec
                if study!=StudySpec.model_validate(owner).model_dump(mode='json'):raise ValueError('native study owner mismatch')
                report=read_json(leaf/'trace.json');checks=[compare(r,report) for r in refs]
                if not report['completed'] or not all(c['passed'] for c in checks) or encoded(checks)!=encoded(read_json(leaf/'comparison.json')):raise ValueError('comparison differs')
                comparisons+=2
    if comparisons!=64 or replays!=16 or verify(root)!=anchor:raise ValueError('coverage/evidence mismatch')
    target=Path(out);target.mkdir(parents=True,exist_ok=False)
    result=dict(passed=True,source_manifest_sha256=anchor,code_sha256=package_fingerprint(),independent_reference_replays=replays,recomputed_comparisons=comparisons,maximum_power_refinement_error_w_var=max_power_refinement,independent_host=False)
    (target/'summary.json').write_bytes(encoded(result));seal(target);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',required=True);print(accept(**vars(p.parse_args())))
