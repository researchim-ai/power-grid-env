"""Final owner-anchored gate; keep exploratory failures outside accepted set."""
from pathlib import Path
from power_grid_env.protection_envelope import ProtectionEnvelope
from power_grid_env.study_artifacts import read_json,encoded,verify,seal,digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.running_fault_corner_store import verify_store
from benchmarks.running_fault_joint_boundary import joint_jobs
from benchmarks.running_fault_boundary_reference import execute


def qualify(protocol,previous_protocol,store,previous_store,preliminary,joint,reassessment,out):
    current=ProtectionEnvelope.model_validate(protocol);previous=ProtectionEnvelope.model_validate(previous_protocol)
    store=Path(store);previous_store=Path(previous_store);preliminary=Path(preliminary);joint=Path(joint);reassessment=Path(reassessment)
    # Full recomputation of owner recipes, engineering requirements, clocks,
    # SI algebra, solver pairs and I²t on all612 final studies.
    checked=verify_store(store,expected_protocol=current)
    if not checked['passed']:raise ValueError('final corner matrix not qualified')
    prior_index=verify(previous_store/'index')
    if read_json(previous_store/'index/protocol.json')!=previous.model_dump(mode='json'):
        raise ValueError('preliminary owner protocol differs')
    prior_result=read_json(previous_store/'index/result.json')
    if not prior_result['passed'] or prior_result['native_runs']!=612:raise ValueError('preliminary matrix incomplete')
    prior_entries=read_json(previous_store/'index/entries.json')
    if len(prior_entries)!=17:raise ValueError('preliminary corners incomplete')
    for e in prior_entries:
        if verify(previous_store/'studies'/e['id'])!=e['manifest_sha256']:raise ValueError('preliminary corner changed')
    sources=dict(preliminary=verify(preliminary),joint=verify(joint))
    sidecar=verify(reassessment);r=read_json(reassessment/'summary.json')
    if not r['passed'] or r['source_manifests']!=sources:raise ValueError('boundary reassessment mismatch')
    if r['oracle_sha256']!=digest(Path('benchmarks/running_fault_island_reference.py').read_bytes()):raise ValueError('boundary oracle changed')
    if ProtectionEnvelope.model_validate(read_json(joint/'revised.json')).model_dump(mode='json')!=current.model_dump(mode='json') or ProtectionEnvelope.model_validate(read_json(joint/'previous.json')).model_dump(mode='json')!=previous.model_dump(mode='json'):
        raise ValueError('joint boundary owner protocol differs')
    original=read_json(joint/'original.json');boundary=[]
    for job in joint_jobs(previous,current,original):
        row=execute((job,str(joint/job['id'])))
        if not row['passed']:raise ValueError('joint boundary recomputation failed')
        boundary.append(row)
    if len(boundary)!=54 or sum(x['label']=='candidate' for x in boundary)!=48:raise ValueError('joint boundary coverage differs')
    if r['preliminary_candidate_failed_runs']!=6:raise ValueError('preserved design failure coverage differs')
    if verify(previous_store/'index')!=prior_index or verify(reassessment)!=sidecar or sources!=dict(preliminary=verify(preliminary),joint=verify(joint)):
        raise ValueError('source evidence changed')
    if verify(store/'index')!=checked['index_manifest_sha256']:raise ValueError('final store index changed')
    result=dict(passed=True,code_sha256=package_fingerprint(),qualified_final_corner_runs=612,
        qualified_final_boundary_runs=48,final_matched_negative_controls=6,
        preliminary_corner_runs=612,preliminary_boundary_runs=54,total_native_research_runs=1332,
        final_solver_pairs=306+24,final_fault_duty_runs=306,final_duty_fine_pairs=102,
        final_corner_index_sha256=checked['index_manifest_sha256'],previous_corner_index_sha256=prior_index,
        boundary_manifests=sources,boundary_reassessment_sha256=sidecar,
        final_all16_corners=True,previous_corner_physics_recomputed_here=False,
        final_corner_physics_recomputed_here=True,all_corners_at_all_voltages_temperatures=False,
        continuous_uncertainty_qualified=False,independent_differential_trajectory=False,
        normative_approval=False,reward=None,full_DES04_complete=False,
        producer_sha256=digest(Path(__file__).read_bytes()),
        scope='Primary .009/backup .0155:612 fully reverified hot-nominal corner runs and48 finite boundary positives; six matched negatives retained. Previous .01/.0155 hot matrix612 retained with integrity check, wider-profile rejection not erased; previous54 boundary runs include original .015 and cold/high design failures. No continuous or universal selectivity claim.')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['protocol','previous-protocol','store','previous-store','preliminary','joint','reassessment','out']:p.add_argument('--'+key,required=True)
    a=vars(p.parse_args());a['protocol']=read_json(a['protocol']);a['previous_protocol']=read_json(a['previous_protocol'])
    r=qualify(**a);print(dict(passed=r['passed'],qualified_final_corner_runs=612,qualified_final_boundary_runs=48))
