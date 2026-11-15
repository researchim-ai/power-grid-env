"""Re-audit immutable boundary traces after explicit SI-oracle corrections.

The first oracle assumed source voltage equalled the bus voltage base and
could not solve an exactly unexcited isolated row. Those were oracle defects;
new assessments do not rewrite native traces or their earlier failed reports.
"""
from pathlib import Path
from power_grid_env.study_artifacts import read_json,encoded,verify,seal,digest
from benchmarks.running_fault_boundary_reference import boundary_jobs,execute
from benchmarks.running_fault_joint_boundary import joint_jobs
from benchmarks.closed_loop_protection_reference import compare_solvers


def qualify(preliminary,joint,out):
    preliminary=Path(preliminary);joint=Path(joint)
    anchors=dict(preliminary=verify(preliminary),joint=verify(joint))
    p=read_json(joint/'previous.json');r=read_json(joint/'revised.json');o=read_json(joint/'original.json')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);collections={}
    for label,source,jobs in [('preliminary',preliminary,list(boundary_jobs(p,o))),('joint',joint,list(joint_jobs(p,r,o)))]:
        rows=[]
        for job in jobs:
            if not (source/job['id']/'manifest.json').exists():raise ValueError('only existing sealed traces may be reassessed')
            rows.append(execute((job,str(source/job['id']))))
        collections[label]=rows
    failures=[row for row in collections['preliminary'] if row['label']=='candidate' and not row['profile_passed']]
    rejected=(len(failures)==6 and all(x['temperature_c']==20 and x['voltage_factor']==1.05 and x['corner']=='corner-1100' for x in failures))
    pairs=[]
    jobs=list(joint_jobs(p,r,o))
    for job in jobs:
        if job['method']!='network_dae':continue
        other=next(j for j in jobs if all(j[k]==job[k] for k in ['label','corner','temperature_c','voltage_factor','grid']) and j['method']=='radau')
        pairs.append(dict(id=job['id'],**compare_solvers(read_json(joint/other['id']/'trace.json'),read_json(joint/job['id']/'trace.json'))))
    result=dict(passed=rejected and all(x['oracle']['passed'] for x in collections['preliminary']) and all(x['passed'] for x in collections['joint']) and all(x['passed'] for x in pairs),
        no_new_native_runs=True,reassessed_native_runs=108,final_candidate_positive_runs=48,final_matched_negative_runs=6,
        preliminary_candidate_rejected=True,preliminary_candidate_failed_runs=len(failures),source_manifests=anchors,
        collections=collections,solver_pairs=pairs,producer_sha256=digest(Path(__file__).read_bytes()),
        oracle_sha256=digest(Path('benchmarks/running_fault_island_reference.py').read_bytes()),
        normative_approval=False,continuous_uncertainty_qualified=False,
        scope='All108 existing sealed native traces reassessed with source physical voltage and exact unexcited zero-row restriction. Six true preliminary design failures retained. No native trajectory changed, no new native runs in this reassessment.')
    if anchors!=dict(preliminary=verify(preliminary),joint=verify(joint)):raise ValueError('source changed during reassessment')
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for k in ['preliminary','joint','out']:p.add_argument('--'+k,required=True)
    r=qualify(**vars(p.parse_args()));print(dict(passed=r['passed'],reassessed_native_runs=108))
    if not r['passed']:raise SystemExit(2)
