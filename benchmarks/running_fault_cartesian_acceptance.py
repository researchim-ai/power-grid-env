"""Recompute finite Cartesian and deterministic interior acceptance together."""
import argparse
from pathlib import Path
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,verify,seal,digest
from benchmarks.running_fault_cartesian import CartesianProtocol,verify_store as verify_cartesian,producer as cartesian_producer
from benchmarks.running_fault_interior import InteriorProtocol,run_store as interior_store,producer as interior_producer


def metrics(corners):
    worst_grid=None;worst_fault=None;minimum_grid_margin=None;minimum_fault_margin=None
    largest_residual=None;max_i2t_change=0.;faults=0
    for label,corner in corners:
        result=read_json(corner/'result.json')
        for duty in result['duty']:
            max_i2t_change=max(max_i2t_change,max(duty['relative_changes'].values()))
        for row in result['rows']:
            if row['kind']!='fault':continue
            child=corner/row['id'];r=read_json(child/'result.json');q=read_json(child/'requirements.json')
            study=read_json(child/'study.json');start=study['coupled']['faults'][0]['apply_s']
            grid=r['grid_isolation_time_s']-start;fault=r['fault_extinction_time_s']-start
            entry=dict(id=label+'/'+row['id'],grid_delay_s=grid,fault_delay_s=fault)
            if worst_grid is None or grid>worst_grid['grid_delay_s']:worst_grid=entry
            if worst_fault is None or fault>worst_fault['fault_delay_s']:worst_fault=entry
            gm=q['maximum_grid_isolation_s']-grid;fm=q['maximum_fault_extinction_s']-fault
            minimum_grid_margin=gm if minimum_grid_margin is None else min(minimum_grid_margin,gm)
            minimum_fault_margin=fm if minimum_fault_margin is None else min(minimum_fault_margin,fm)
            residual=r['maximum_sampled_fault_current_after_grid_isolation_a']
            if residual is not None and (largest_residual is None or residual>largest_residual['current_a']):
                largest_residual=dict(id=entry['id'],current_a=residual)
            faults+=1
    return dict(fault_grid_studies=faults,worst_grid_isolation=worst_grid,worst_sampled_fault_extinction=worst_fault,
                minimum_grid_time_margin_s=minimum_grid_margin,minimum_fault_time_margin_s=minimum_fault_margin,
                maximum_i2t_fine_pair_relative_change=max_i2t_change,largest_sampled_residual_after_grid_isolation=largest_residual)


def producer():
    return dict(code_sha256=package_fingerprint(),winding_source_sha256=digest(Path('benchmarks/running_motor_winding_reference.py').read_bytes()),cartesian=cartesian_producer(),interior=interior_producer(),
                acceptance_sha256=digest(Path(__file__).read_bytes()))


def qualify(protocol,store,interior_protocol,interior,winding_reference,out,workers=8):
    p=CartesianProtocol.model_validate(protocol);ip=InteriorProtocol.model_validate(interior_protocol)
    if ip.cartesian!=p:raise ValueError('interior and Cartesian owner protocols differ')
    prod=producer();store=Path(store);interior=Path(interior)
    if any(Path(out).resolve().is_relative_to(source.resolve()) for source in [store,interior]):
        raise ValueError('acceptance output must be outside both source stores')
    a=verify_cartesian(store,p,workers);b=interior_store(ip,interior,workers,verify_only=True)
    if not a['passed'] or not b['passed']:raise ValueError('all declared Cartesian and interior studies must pass')
    winding=Path(winding_reference);winding_anchor=verify(winding);wr=read_json(winding/'summary.json')
    if not wr['passed'] or wr['independent_trajectories']!=15 or wr['native_comparisons']!=30:raise ValueError('independent normal winding reference incomplete')
    if wr['source_indices']!=dict(cartesian=a['index_manifest_sha256'],interior=b['index_manifest_sha256']):raise ValueError('winding reference bound to different native sources')
    if wr['producer_sha256']!=digest(Path('benchmarks/running_motor_winding_reference.py').read_bytes()):raise ValueError('winding reference producer changed')
    corners=[]
    for e in read_json(store/'index/entries.json'):
        corners.append((e['condition_id']+'/'+e['corner'],store/'conditions'/e['condition_id']/e['corner']))
    for e in read_json(interior/'index/entries.json'):corners.append((e['id'],interior/'points'/e['id']))
    values=metrics(corners)
    if values['minimum_fault_time_margin_s'] < -1e-8 or values['minimum_grid_time_margin_s'] < -1e-8:raise ValueError('owner deadline violated')
    if verify(store/'index')!=a['index_manifest_sha256'] or verify(interior/'index')!=b['index_manifest_sha256'] or producer()!=prod:
        raise ValueError('producer/source changed during final acceptance')
    if verify(winding)!=winding_anchor:raise ValueError('winding reference changed')
    result=dict(passed=True,independent_normal_trajectories=15,independent_normal_native_comparisons=30,winding_reference_sha256=winding_anchor,producer=prod,cartesian_index_sha256=a['index_manifest_sha256'],interior_index_sha256=b['index_manifest_sha256'],
                cartesian_runs=a['native_runs'],interior_runs=b['native_runs'],total_native_runs=a['native_runs']+b['native_runs'],
                solver_pairs=a['solver_pairs']+b['solver_pairs'],measured_fault_runs=a['measured_fault_runs']+b['measured_fault_runs'],
                duty_fine_pairs=a['duty_fine_pairs']+b['duty_fine_pairs'],metrics=values,
                all_declared_endpoint_combinations_qualified=True,continuous_uncertainty_qualified=False,
                independent_differential_trajectory=False,normative_approval=False,reward=None,full_DES04_complete=False,
                scope='Owner-catalog-bound finite endpoint Cartesian coverage plus center/seeded interior probes, nominal and tolerance relay parameters, all six modes. Conditional SI algebra and shared-physics two-solver agreement. No continuous proof, dynamic thermal feedback, EMT or OEM certification.')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['protocol','store','interior-protocol','interior','winding-reference','out']:p.add_argument('--'+key,required=True)
    p.add_argument('--workers',type=int,default=8);a=vars(p.parse_args())
    a['protocol']=read_json(a['protocol']);a['interior_protocol']=read_json(a['interior_protocol'])
    r=qualify(**a);print(dict(passed=r['passed'],total_native_runs=r['total_native_runs']))
