"""Recompute every independent trajectory and gate from sealed local evidence."""
import copy
from pathlib import Path
import numpy as np
from scipy.optimize import linear_sum_assignment

from benchmarks.network_stator_cases import jobs
from benchmarks.network_stator_reference import NetworkStatorReference
from benchmarks.network_stator_acceptance import compare, coverage, producers, reference_packet, event_key
from benchmarks.energy_integral_gate import native_error, reference_error
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_spec import StudySpec
from power_grid_env.model import canonical_hash
from power_grid_env.study_artifacts import read_json, encoded, seal, verify


def physical_gate(sample, effects, end=.5):
    """Opening continuity, separate latch cause and analytical no-current cooling."""
    t=effects['disconnected_s'];a,b=[sample(t,side) for side in ('pre_event','post_event')]
    for k in ('g.delta','g.omega','g.heat.temperature_c','g.stator_thermal_level','m.speed_pu'):
        if abs(a[k]-b[k])>1e-10: raise ValueError('opening changed a continuous state:'+k)
    if a['g.connected']!=1 or any(b[k]!=0 for k in ('g.current_pu','g.heat.copper_loss_w','g.connected')):
        raise ValueError('opening port/current differs')
    if b['g.stator_thermal_latched']!=effects['thermal_trips']:
        raise ValueError('mechanical opening incorrectly latches I2 protection')
    q=sample(end,'regular')
    if abs(q['g.stator_thermal_level']-b['g.stator_thermal_level']*np.exp(-(end-t)/.05))>2e-7:
        raise ValueError('post-opening I2 cooling differs')
    if q['primary-cb.open_attempts']!=effects['open_attempts'] or q['primary-cb.close_attempts']!=0:
        raise ValueError('actuator attempt counts differ')
    if q['primary-oc.progress']!=1 or q['primary-oc.latched']!=1 or q['primary-oc.active']!=0:
        raise ValueError('relay latch/clock differs')


def accept(source, out):
    root=Path(source);anchor=verify(root);s=read_json(root/'summary.json');declared=list(jobs());fp=package_fingerprint()
    if not s['passed'] or s['code_sha256']!=fp or s['producers']!=producers(): raise ValueError('producer/runtime mismatch')
    if [r['id'] for r in s['rows']]!=[j['id'] for j in declared]: raise ValueError('owner inventory differs')
    replays=comparisons=energy_checks=spectra=trips=failures=0
    max_ref_energy=max_native_energy=max_event_refinement=max_signal_refinement=0.
    outcomes={k:0 for k in ('applied','already_in_requested_state','failed_to_operate')}
    for job in declared:
        child=root/job['id'];verify(child)
        refs=[NetworkStatorReference(job['study']).run(max_step=.002),
              NetworkStatorReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
        for name,r in zip(('reference','refined-reference'),refs):
            if encoded(reference_packet(r))!=encoded(read_json(child/(name+'.json'))):
                raise ValueError('independent snapshot replay differs')
            effects=coverage(r.events,job['scenario']);physical_gate(r.sample,effects)
            pickup=next(e['time_s'] for e in r.events if e['kind']=='network_relay_pickup')
            if abs(r.sample(pickup,'pre_event')['primary-oc.measurement']-150.)>1e-7:
                raise ValueError('independent pickup current differs')
            max_ref_energy=max(max_ref_energy,reference_error(r));replays+=1
        if [event_key(e) for e in refs[0].events]!=[event_key(e) for e in refs[1].events]: raise ValueError('refinement event identities differ')
        event_error=max(abs(a['time_s']-b['time_s']) for a,b in zip(refs[0].events,refs[1].events))
        max_event_refinement=max(max_event_refinement,event_error)
        if event_error>1e-8: raise ValueError('event refinement exceeds gate')
        for point in read_json(child/'reference.json')['regular_values']:
            t=point['time_s']
            if any(abs(t-e['time_s'])<2e-7 for e in refs[1].events): continue
            a,b=[r.sample(t) for r in refs]
            for k in a:
                error=abs(a[k]-b[k]);limit=1e-4 if k.endswith(('_w','_var','_j','.measurement','.current_a')) else 1e-7
                if error>limit: raise ValueError('signal refinement exceeds gate:'+k)
                max_signal_refinement=max(max_signal_refinement,error/limit)
        for method in ('radau','network_dae'):
            for grid in (0,1):
                leaf=child/(method+'-'+str(grid));verify(leaf)
                owner=copy.deepcopy(job['study']);owner['coupled'].update(integration_method=method,step_s=.002/2**grid)
                spec=StudySpec.model_validate(owner);report=read_json(leaf/'trace.json')
                if read_json(leaf/'study.json')!=spec.model_dump(mode='json') or report['settings']!=spec.coupled.model_dump(mode='json') or report['network_sha256']!=canonical_hash(spec.network) or report['code_sha256']!=fp:
                    raise ValueError('native owner/runtime mismatch')
                checks=[compare(r,report) for r in refs]
                if not report['completed'] or not report['additional_validation']['ok'] or not all(c['passed'] for c in checks) or encoded(checks)!=encoded(read_json(leaf/'comparison.json')):
                    raise ValueError('native comparison differs')
                effects=coverage(report['event_log'],job['scenario']);outcomes[effects['outcome']]+=1
                trips+=effects['thermal_trips'];failures+=effects['failure_confirmations']
                def sample(t,side):
                    indices=[i for i,(time,s) in enumerate(zip(report['time_s'],report['sample_side'])) if time==t and s==side]
                    if len(indices)!=1: raise ValueError('missing/ambiguous opening sample')
                    return {k:v[indices[0]] for k,v in report['channels'].items()}
                physical_gate(sample,effects)
                max_native_energy=max(max_native_energy,native_error(report));energy_checks+=1
                audit=report['operating_point'];joint=job['mode']=='equilibrium'
                key='full_physical_spectral_abscissa_per_s' if joint else 'connected_machine_spectral_abscissa_per_s'
                for r in refs:
                    if abs(audit[key]-r.initial['energy_spectral_abscissa_per_s'])>2e-6: raise ValueError('initial spectral abscissa differs')
                    if joint:
                        a=np.array(audit['physical_eigenvalues_per_s']);b=np.array(r.initial['energy_eigenvalues_per_s'])
                        if a.shape!=b.shape: raise ValueError('initial spectrum dimension differs')
                        cost=abs((a[:,0]+1j*a[:,1])[:,None]-(b[:,0]+1j*b[:,1])[None,:]);ri,ci=linear_sum_assignment(cost)
                        if np.max(cost[ri,ci])>5e-6: raise ValueError('initial physical spectrum differs')
                spectra+=1;comparisons+=2
    for mode in ('passport','equilibrium'):
        for method in ('radau','network_dae'):
            for grid in (0,1):
                a,b=[read_json(root/(mode+'-thermal_first'+suffix)/(method+'-'+str(grid))/'trace.json') for suffix in ('','-reversed')]
                if any(a[k]!=b[k] for k in ('time_s','sample_side','event_log','channels')): raise ValueError('equipment/event permutation changed result')
    if replays!=16 or comparisons!=64 or energy_checks!=32 or trips!=24 or failures!=8 or outcomes!={'applied':8,'already_in_requested_state':16,'failed_to_operate':8}:
        raise ValueError('coverage inventory differs')
    if verify(root)!=anchor: raise ValueError('source changed')
    target=Path(out);target.mkdir(parents=True,exist_ok=False)
    result=dict(passed=True,code_sha256=fp,source_manifest_sha256=anchor,independent_reference_replays=replays,
        recomputed_comparisons=comparisons,native_integral_energy_checks=energy_checks,
        maximum_native_integral_error_j=max_native_energy,maximum_reference_integral_error_j=max_ref_energy,
        maximum_event_refinement_error_s=max_event_refinement,maximum_signal_refinement_gate_ratio=max_signal_refinement,
        initial_spectra_checked=spectra,native_thermal_trips=trips,native_failure_confirmations=failures,
        native_opening_outcomes=outcomes,permutation_pairs=8,independent_host=False)
    (target/'summary.json').write_bytes(encoded(result));seal(target)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',required=True)
    print(accept(**vars(p.parse_args())))
