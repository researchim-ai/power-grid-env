"""Owner-pinned acceptance of the new three-motor train physics and SI oracle."""
import argparse
from pathlib import Path

from benchmarks.three_motor_training import jobs
from benchmarks.network_construction_reference import reference
from power_grid_env.evidence import package_fingerprint
from power_grid_env.network_design import compile_network,verify_network_design
from power_grid_env.study_artifacts import read_json,encoded,verify,seal,digest


def run(source,source_manifest,si,si_manifest,out):
    fp=package_fingerprint()
    if verify(source)!=source_manifest or verify(si)!=si_manifest:raise ValueError('owner physical anchors differ')
    s=read_json(Path(source)/'summary.json');r=read_json(Path(si)/'receipt.json')
    if (not s['passed'] or not r['passed'] or s['code_sha256']!=fp or r['code_sha256']!=fp or
            r['source_manifest_sha256']!=source_manifest or r['reference_trajectories']!=12 or
            r['grid_comparisons']!=36 or r['reference_sha256']!=digest(Path('benchmarks/three_motor_si_reference.py').read_bytes())):
        raise ValueError('complete current independent dynamic reference required')
    if [v['id'] for v in s['rows']]!=[j['id'] for j in jobs()]:raise ValueError('owner inventory differs')
    for j,row in zip(jobs(),s['rows'],strict=True):
        t,p,plan,c=compile_network(j['task'],j['proposal'])
        checked=verify_network_design(Path(source)/j['id'],expected_task=t,expected_proposal=p)
        independent=reference(t.model_dump(mode='json'),p.model_dump(mode='json'),c,plan.model_dump(mode='json'))
        if not checked['numerically_qualified'] or checked['manifest_sha256']!=row['design_manifest'] or independent!=row['reference']:
            raise ValueError('physics or independent SI/OpenDSS replay differs')
        references=[x for x in r['rows'] if x['design']==j['id']]
        if {v['case'] for v in references}!={c.id for c in plan.cases} or len(references)!=6 or not all(len(v['rows'])==3 and all(z['passed'] for z in v['rows']) for v in references):
            raise ValueError('SI dynamic case/grid inventory differs')
    result=dict(passed=True,code_sha256=fp,source_manifest_sha256=source_manifest,
        designs=2,verified_dynamic_runs=36,independent_fault_solutions=6,
        independent_si_reference_manifest_sha256=si_manifest,independent_dynamic_reference=True,
        scope='Bounded authored three-node three-motor fan studies, all three grids and eight SI channels; no OEM qualification.')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    if verify(source)!=source_manifest or verify(si)!=si_manifest:raise ValueError('sources changed')
    (root/'receipt.json').write_bytes(encoded(result));seal(root)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for k in ('source','source-manifest','si','si-manifest','out'):p.add_argument('--'+k,required=True)
    print(run(**vars(p.parse_args())))
