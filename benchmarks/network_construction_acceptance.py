"""Construct real candidate networks, run every mandatory study and verify."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from benchmarks.network_construction_cases import example,proposal
from benchmarks.network_construction_reference import reference
from power_grid_env.network_design import compile_network,run_network_design,verify_network_design
from power_grid_env.network_design_view import export_view
from power_grid_env.study_artifacts import encoded,read_json,digest,seal,verify
from power_grid_env.evidence import package_fingerprint


def variants():
    for motors,kind in ((2,'star'),(2,'chain'),(2,'long'),(3,'star'),(3,'long')):
        yield dict(id=str(motors)+'-'+kind,task=example(motors),proposal=proposal(kind,motors),expected=kind!='long')


def producers():
    paths=[Path('benchmarks/network_construction_'+name+'.py') for name in ('cases','reference','acceptance')]
    return {str(p):digest(p.read_bytes()) for p in paths}


def execute(job,root):
    task,proposal_,plan,c=compile_network(job['task'],job['proposal'])
    result=run_network_design(task,proposal_,root)
    checked=verify_network_design(root,expected_task=job['task'],expected_proposal=job['proposal'])
    if not result['completed'] or not result['numerically_qualified'] or result['meets_declared_requirements']!=job['expected']:raise ValueError('unexpected design verdict: '+job['id']+' '+str(result))
    ref=reference(task.model_dump(mode='json'),proposal_.model_dump(mode='json'),c,plan.model_dump(mode='json'))
    if not ref['passed']:raise ValueError('independent reference failed: '+job['id'])
    print(job['id'],result['status'],flush=True)
    return dict(id=job['id'],status=result['status'],reward=result['reward'],dynamic_pass=result['dynamic_checks_passed'],static_pass=result['static_checks_passed'],design_manifest=checked['manifest_sha256'],reference=ref)


def run(out):
    root=Path(out);root.mkdir(parents=True,exist_ok=False);fp=package_fingerprint();hashes=producers();declared=list(variants())
    with ProcessPoolExecutor(max_workers=2) as pool:rows=list(pool.map(execute,declared,[root/j['id'] for j in declared]))
    for job in declared:export_view(job['task'],job['proposal'],root/(job['id']+'-view'))
    result=dict(passed=True,code_sha256=fp,producers=hashes,designs=5,dynamic_runs=45,mandatory_cases_per_design=3,
                independent_fault_solutions=sum(len(r['reference']['fault_solutions']) for r in rows),rows=rows,
                manufacturer_authenticated=False,independent_dynamic_reference=False,normative_approval=False)
    if fp!=package_fingerprint() or hashes!=producers():raise ValueError('producer changed')
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result


def accept(source,out):
    root=Path(source);anchor=verify(root);s=read_json(root/'summary.json');fp=package_fingerprint()
    if not s['passed'] or s['code_sha256']!=fp or s['producers']!=producers():raise ValueError('source/runtime differs')
    if [r['id'] for r in s['rows']]!=[j['id'] for j in variants()]:raise ValueError('design inventory differs')
    for job,row in zip(variants(),s['rows']):
        task,p,plan,c=compile_network(job['task'],job['proposal'])
        v=verify_network_design(root/job['id'],expected_task=job['task'],expected_proposal=job['proposal'])
        if not v['numerically_qualified'] or v['meets_declared_requirements']!=job['expected'] or row['design_manifest']!=v['manifest_sha256']:raise ValueError('design verdict differs')
        ref=reference(task.model_dump(mode='json'),p.model_dump(mode='json'),c,plan.model_dump(mode='json'))
        if not ref['passed'] or encoded(ref)!=encoded(row['reference']):raise ValueError('independent replay differs')
        from power_grid_env.network_design_view import observation,single_line_svg
        view=root/(job['id']+'-view');verify(view)
        if read_json(view/'observation.json')!=observation(task,p) or (view/'single-line.svg').read_text()!=single_line_svg(task,p):raise ValueError('public observation differs')
    if verify(root)!=anchor:raise ValueError('source changed')
    target=Path(out);target.mkdir(parents=True,exist_ok=False)
    result=dict(passed=True,code_sha256=fp,source_manifest_sha256=anchor,designs=5,verified_dynamic_runs=45,independent_fault_solutions=17,public_views=5,independent_host=False)
    (target/'receipt.json').write_bytes(encoded(result));seal(target);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--source');p.add_argument('--out',required=True);args=p.parse_args()
    r=accept(args.source,args.out) if args.source else run(args.out)
    print({k:v for k,v in r.items() if k not in ('rows','producers')})
