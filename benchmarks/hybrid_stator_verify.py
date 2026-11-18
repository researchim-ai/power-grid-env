"""Offline independent consumer of limiter references, owners and comparisons."""
from pathlib import Path
import copy
import numpy as np
from power_grid_env.study_artifacts import read_json,encoded,seal,verify
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_spec import StudySpec
from benchmarks.hybrid_stator_cases import jobs
from benchmarks.hybrid_stator_reference import HybridStatorReference
from benchmarks.hybrid_stator_acceptance import producers,compare,coverage,event_key


def accept(source,out):
    root=Path(source);anchor=verify(root);summary=read_json(root/'summary.json');declared=list(jobs());fp=package_fingerprint()
    if not summary['passed'] or summary['code_sha256']!=fp or summary['producers']!=producers():raise ValueError('producer/runtime mismatch')
    if [r['id'] for r in summary['rows']]!=[j['id'] for j in declared]:raise ValueError('owner inventory mismatch')
    comparisons=replays=limiter_events=held_segments=0;maximum_event_refinement=0.;maximum_power_refinement=0.;max_ref_energy=0.;max_native_energy=0.;energy_checks=0;trips_checked=0;immediate_cascades=0
    for job in declared:
        child=root/job['id'];verify(child)
        refs=[HybridStatorReference(job['study']).run(max_step=.002),HybridStatorReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
        for name,ref in zip(['reference','refined-reference'],refs):
            data=read_json(child/(name+'.json'))
            if encoded(data['initialization'])!=encoded(ref.initial) or encoded(data['events'])!=encoded(ref.events):raise ValueError('reference init/events differ')
            coverage(ref.events,job['controls'])
            for point in data['regular_values']:
                if encoded(point['channels'])!=encoded(ref.sample(point['time_s'])):raise ValueError('reference sample differs')
            for point in data['one_sided_values']:
                if encoded(point['channels'])!=encoded(ref.sample(point['time_s'],point['side'])):raise ValueError('one-sided reference differs')
            for left,right,sol,c in ref.segments:
                for address,side in c['held'].items():
                    target=ref.entries[address][1 if side=='lower' else 2]
                    if max(abs(sol(t)[address]-target) for t in np.linspace(left,right,7))>1e-12:raise ValueError('held state drift')
                    held_segments+=1
            from benchmarks.energy_integral_gate import reference_error
            max_ref_energy=max(max_ref_energy,reference_error(ref))
            trip=next(e['time_s'] for e in ref.events if e['kind']=='generator_thermal_trip')
            pre,post=[ref.sample(trip,side) for side in ['pre_event','post_event']]
            for key in ['g.delta','g.omega','g.heat.temperature_c','g.stator_thermal_level','m.speed_pu']:
                if abs(pre[key]-post[key])>1e-12:raise ValueError('state jumped in trip cascade')
            for t in np.linspace(trip,ref.w.s.duration_s,21):
                q=ref.sample(float(t),'post_event');expected=ref.replica.trip_level*np.exp(-(t-trip)/ref.replica.time_constant_s)
                if abs(q['g.stator_thermal_level']-expected)>1e-8 or any(q[k]!=0 for k in ['g.current_pu','g.heat.copper_loss_w','g.connected']):raise ValueError('posttrip state/current differs')
            replays+=1
        if [event_key(e) for e in refs[0].events]!=[event_key(e) for e in refs[1].events]:raise ValueError('refinement event identities differ')
        error=max(abs(a['time_s']-b['time_s']) for a,b in zip(refs[0].events,refs[1].events));maximum_event_refinement=max(maximum_event_refinement,error)
        if error>1e-8:raise ValueError('event refinement exceeds gate')
        for point in read_json(child/'reference.json')['regular_values']:
            t=point['time_s']
            if any(abs(t-e['time_s'])<2e-7 for e in refs[1].events):continue
            a,b=[r.sample(t) for r in refs]
            for k in a:
                delta=abs(a[k]-b[k]);limit=1e-4 if k.endswith(('_w','_var','_j','.measurement','.current_a')) else 1e-7
                if delta>limit:raise ValueError('reference refinement failed:'+k)
                if k.endswith(('_w','_var')):maximum_power_refinement=max(maximum_power_refinement,delta)
        for method in ['radau','network_dae']:
            for grid in [0,1]:
                leaf=child/(method+'-'+str(grid));verify(leaf);owner=copy.deepcopy(job['study']);owner['coupled'].update(integration_method=method,step_s=.002/2**grid)
                if read_json(leaf/'study.json')!=StudySpec.model_validate(owner).model_dump(mode='json'):raise ValueError('native owner differs')
                report=read_json(leaf/'trace.json');checks=[compare(r,report) for r in refs]
                if not report['completed'] or not report['additional_validation']['ok'] or not all(c['passed'] for c in checks) or encoded(checks)!=encoded(read_json(leaf/'comparison.json')):raise ValueError('comparison/gates differ')
                from benchmarks.energy_integral_gate import native_error
                from scipy.optimize import linear_sum_assignment
                audit=report['operating_point'];joint=job['mode']=='equilibrium'
                for ref in refs:
                    key='full_physical_spectral_abscissa_per_s' if joint else 'connected_machine_spectral_abscissa_per_s'
                    if abs(audit[key]-ref.initial['energy_spectral_abscissa_per_s'])>2e-6:raise ValueError('initial spectral abscissa differs')
                    if joint:
                        a=np.array(audit['physical_eigenvalues_per_s']);b=np.array(ref.initial['energy_eigenvalues_per_s'])
                        if a.shape!=b.shape:raise ValueError('physical spectrum dimension differs')
                        cost=abs((a[:,0]+1j*a[:,1])[:,None]-(b[:,0]+1j*b[:,1])[None,:]);ri,ci=linear_sum_assignment(cost)
                        if np.max(cost[ri,ci])>5e-6:raise ValueError('physical spectrum differs')
                channels=report['channels']
                if max(abs(v) for v in channels['g.stator.power_balance_w'])>1e-7 or max(abs(a-b) for a,b in zip(channels['g.stator.copper_electrical_loss_w'],channels['g.heat.copper_loss_w']))>1e-7:raise ValueError('copper partition differs')
                max_native_energy=max(max_native_energy,native_error(report));energy_checks+=1
                c=coverage(report['event_log'],job['controls'])
                if c['immediate_SEXS_release']!=(job['lead']==1.) or c['SEXS']['releases']!=(2 if job['lead']==1. else 1):raise ValueError('cascade policy differs')
                trips_checked+=1;immediate_cascades+=int(c['immediate_SEXS_release'])
                trip=next(e for e in report['event_log'] if e['kind']=='generator_thermal_trip')
                if trip['threshold']!=job['study']['coupled']['generator_thermal'][0]['trip_level'] or abs(trip['level']-trip['threshold'])>2e-7:raise ValueError('trip threshold differs')
                limiter_events+=sum(e['kind'].startswith('controller_limit_') for e in report['event_log']);comparisons+=2
    # A reversed source list of coincident commands must preserve all saved axes,
    # event order, one-sided values and waveforms, including both heat policies.
    for mode in ['passport','equilibrium']:
        for method in ['radau','network_dae']:
            for grid in [0,1]:
                rows=[read_json(root/(mode+'-lead1.0'+suffix)/(method+'-'+str(grid))/'trace.json') for suffix in ['', '-reversed']]
                if any(rows[0][k]!=rows[1][k] for k in ['time_s','sample_side','event_log','channels']):raise ValueError('command order changed result')
    if replays!=12 or comparisons!=48 or limiter_events!=184 or trips_checked!=24 or immediate_cascades!=16 or held_segments==0 or verify(root)!=anchor:raise ValueError('coverage/evidence changed')
    target=Path(out);target.mkdir(parents=True,exist_ok=False)
    result=dict(passed=True,code_sha256=fp,source_manifest_sha256=anchor,independent_reference_replays=replays,recomputed_comparisons=comparisons,
      native_integral_energy_checks=energy_checks,maximum_native_integral_error_j=max_native_energy,maximum_reference_integral_error_j=max_ref_energy,full_initial_spectra_checked=12,frozen_initial_spectra_checked=12,native_generator_trips=trips_checked,immediate_trip_release_cascades=immediate_cascades,native_limiter_events=limiter_events,reference_held_segments_checked=held_segments,command_permutation_pairs=8,maximum_event_refinement_error_s=maximum_event_refinement,
      maximum_power_refinement_error_w_var=maximum_power_refinement,independent_host=False,consumer_energy_gate_sha256=__import__('power_grid_env.study_artifacts',fromlist=['digest']).digest(Path('benchmarks/energy_integral_gate.py').read_bytes()))
    (target/'summary.json').write_bytes(encoded(result));seal(target);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',required=True);print(accept(**vars(p.parse_args())))
