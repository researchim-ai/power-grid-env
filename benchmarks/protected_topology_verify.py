"""Recompute every independent trajectory and gate from sealed local evidence."""
import copy
from pathlib import Path
import numpy as np
from scipy.optimize import linear_sum_assignment

from benchmarks.protected_topology_cases import jobs
from benchmarks.protected_topology_reference import ProtectedTopologyReference
from benchmarks.protected_topology_acceptance import compare, coverage, producers, reference_packet, event_key,compact_comparisons
from benchmarks.energy_integral_gate import native_error, reference_error
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_spec import StudySpec
from power_grid_env.model import canonical_hash
from power_grid_env.study_artifacts import read_json, encoded, seal, verify


def physical_gate(sample,events,scenario):
    for t in sorted({e['time_s'] for e in events}):
        pre,post=[sample(t,side) for side in ('pre_event','post_event')]
        for k in ('g.delta','g.omega','g.heat.temperature_c','g.stator_thermal_level','m.speed_pu','m.e_real_pu','m.e_imag_pu','m.stator_temperature_c','m.rotor_temperature_c'):
            if abs(pre[k]-post[k])>1e-10:raise ValueError('discontinuous physical state:'+k)
    for e in events:
        t=e['time_s']
        if e['kind']=='network_relay_trip':
            for side in ('pre_event','post_event'):
                if abs(sample(t,side)['primary-oc.progress']-1)>2e-7:raise ValueError('relay trip clock differs')
        if e['kind']=='breaker_close' and e['outcome']=='applied':
            q=sample(t,'post_event')
            if q['primary-oc.progress']!=0 or q['primary-oc.latched']!=0 or q['primary-oc.active']!=1:raise ValueError('reclose failed to reset and rearm protection')
    pickup=next(e['time_s'] for e in events if e['kind']=='network_relay_pickup')
    expected=.94 if scenario=='undervoltage' else 150.
    if abs(sample(pickup,'pre_event')['primary-oc.measurement']-expected)>1e-7:raise ValueError('pickup measurement differs')
    cycling=scenario in ('definite_reclose','standard_inverse','very_inverse','extremely_inverse')
    for t in (.2,.4,.5):
        q=sample(t,'regular')
        if q['g.connected']!=1 or q['g.stator_thermal_latched']!=0 or q['g.heat.copper_loss_w']<=0 or q['m.bus_voltage_pu']<=.5:raise ValueError('generator-fed island lost')
        if q['source.connected']!=int(scenario!='failure_backup') or q['backup-cb.closed']!=q['source.connected'] or q['primary-cb.closed']!=int(scenario=='failure_backup') or q['network.zero_reference_components']!=0:raise ValueError('final protection topology differs')
        if abs(q['g.stator.power_balance_w'])>1e-7 or abs(q['g.stator.copper_electrical_loss_w']-q['g.heat.copper_loss_w'])>1e-7:raise ValueError('copper partition differs')
    q=sample(.5,'regular');reclose=scenario not in ('failure_backup','undervoltage')
    expected={'primary-cb.open_attempts':2 if cycling else 1,'primary-cb.close_attempts':int(cycling or scenario=='sync_blocked'),
        'primary-cb.reclose_shots':int(reclose),'primary-cb.manual_inhibit':int(scenario=='manual_inhibit'),
        'backup-cb.open_attempts':int(scenario=='failure_backup'),'backup-cb.close_attempts':0,'backup-cb.reclose_shots':0,'backup-cb.manual_inhibit':0}
    if any(q[k]!=v for k,v in expected.items()):raise ValueError('actuator attempt/shot/inhibit differs')


def clock_gate(ref):
    from scipy.integrate import quad
    maximum=0.;segments=0
    for left,right,solution,ctx in ref.segments:
        x,y=solution(left),solution(right)
        for i,r in enumerate(ref.relays):
            active,latched,available=ctx['relays'][r.id]
            if latched:expected=x[4+i]
            elif not active:expected=x[4+i]*np.exp(-(right-left)/r.reset_s)
            else:
                integral=quad(lambda t:ref.rhs(t,solution(t),ctx)[4+i],left,right,epsabs=1e-9)[0]
                expected=x[4+i]+integral
            error=abs(y[4+i]-expected);maximum=max(maximum,error);segments+=1
            if error>2e-8:raise ValueError('independent clock integral/reset differs')
    return segments,maximum


def accept(source, out):
    root=Path(source);anchor=verify(root);s=read_json(root/'summary.json');declared=list(jobs());fp=package_fingerprint()
    if not s['passed'] or s['code_sha256']!=fp or s['producers']!=producers(): raise ValueError('producer/runtime mismatch')
    if [r['id'] for r in s['rows']]!=[j['id'] for j in declared]: raise ValueError('owner inventory differs')
    replays=comparisons=energy_checks=spectra=0
    max_ref_energy=max_native_energy=max_event_refinement=max_signal_refinement=0.
    totals={k:0 for k in ('relay_trips','primary_applied','primary_failed','reclose_applied','reclose_blocked','backup_applied')};max_sync_error={};clock_segments=0;max_clock_error=0.
    for job in declared:
        child=root/job['id'];verify(child)
        refs=[ProtectedTopologyReference(job['study']).run(max_step=.002),
              ProtectedTopologyReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
        for name,r in zip(('reference','refined-reference'),refs):
            if encoded(reference_packet(r))!=encoded(read_json(child/(name+'.json'))):
                raise ValueError('independent snapshot replay differs')
            coverage(r.events,job['scenario']);physical_gate(r.sample,r.events,job['scenario'])
            count,error=clock_gate(r);clock_segments+=count;max_clock_error=max(max_clock_error,error)
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
                if not report['completed'] or not report['additional_validation']['ok'] or not all(c['passed'] for c in checks) or encoded(compact_comparisons(checks))!=encoded(read_json(leaf/'comparison.json')):
                    raise ValueError('native comparison differs')
                effects=coverage(report['event_log'],job['scenario'])
                for k,v in effects.items():totals[k]+=v
                for check in checks:
                    for k,v in check['synchronism_errors'].items():max_sync_error[k]=max(max_sync_error.get(k,0.),v)
                def sample(t,side):
                    indices=[i for i,(time,s) in enumerate(zip(report['time_s'],report['sample_side'])) if time==t and s==side]
                    if len(indices)!=1: raise ValueError('missing/ambiguous opening sample')
                    return {k:v[indices[0]] for k,v in report['channels'].items()}
                physical_gate(sample,report['event_log'],job['scenario'])
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
                a,b=[read_json(root/(mode+'-very_inverse'+suffix)/(method+'-'+str(grid))/'trace.json') for suffix in ('','-reversed')]
                if any(a[k]!=b[k] for k in ('time_s','sample_side','event_log','channels')): raise ValueError('equipment/event permutation changed result')
    if replays!=36 or comparisons!=144 or energy_checks!=72 or totals!={'relay_trips':112,'primary_applied':104,'primary_failed':8,'reclose_applied':40,'reclose_blocked':16,'backup_applied':8}:
        raise ValueError('coverage inventory differs')
    if verify(root)!=anchor: raise ValueError('source changed')
    target=Path(out);target.mkdir(parents=True,exist_ok=False)
    result=dict(passed=True,code_sha256=fp,source_manifest_sha256=anchor,independent_reference_replays=replays,
        recomputed_comparisons=comparisons,native_integral_energy_checks=energy_checks,
        maximum_native_integral_error_j=max_native_energy,maximum_reference_integral_error_j=max_ref_energy,
        maximum_event_refinement_error_s=max_event_refinement,maximum_signal_refinement_gate_ratio=max_signal_refinement,
        initial_spectra_checked=spectra,native_protection_actions=totals,reference_clock_segments_checked=clock_segments,maximum_clock_integral_error=max_clock_error,maximum_synchronism_errors=max_sync_error,permutation_pairs=8,independent_host=False)
    (target/'summary.json').write_bytes(encoded(result));seal(target)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',required=True)
    print(accept(**vars(p.parse_args())))
