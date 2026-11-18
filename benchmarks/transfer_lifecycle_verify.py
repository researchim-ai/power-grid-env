"""Recompute every independent trajectory and gate from sealed local evidence."""
import copy
from pathlib import Path
import numpy as np
from scipy.optimize import linear_sum_assignment

from benchmarks.transfer_lifecycle_cases import jobs
from benchmarks.transfer_lifecycle_reference import TransferLifecycleReference
from benchmarks.transfer_lifecycle_acceptance import compare, coverage, producers, reference_packet, event_key,compact_comparisons
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
    for i in range(151):
        q=sample(i*.002,'regular')
        if q['g.connected']+q['source.connected']>1 or q['g.stator_thermal_latched']!=0:raise ValueError('parallel sources or false thermal latch')
        if abs(q['g.stator.power_balance_w'])>1e-7 or abs(q['g.stator.copper_electrical_loss_w']-q['g.heat.copper_loss_w'])>1e-7:raise ValueError('copper partition differs')
    if scenario=='primary_pt_pause':
        q=sample(.066,'regular')
        if q['ats.primary_measurement_valid']!=0 or q['ats.primary_voltage_pu']!=0 or q['ats.active']!=0 or abs(q['ats.progress']-.5*np.exp(-1.1))>2e-8:raise ValueError('PT suspension/reset memory differs')
    if scenario in ('reserve_pt_before_request','reserve_pt_inflight','pt_retry'):
        q=sample(.072 if scenario!='reserve_pt_inflight' else .078,'regular')
        if q['ats.reserve_measurement_valid']!=0 or q['ats.reserve_voltage_pu']!=0 or q['g.terminal_voltage_pu']<.8:raise ValueError('invalid PT changed physical reserve')
    if scenario=='ct_during_transfer':
        q=sample(.09,'regular')
        if q['reserve-oc.measurement_valid']!=0 or q['reserve-oc.measurement']!=0 or q['g.current_pu']<=0 or q['reserve-oc.progress']!=0:raise ValueError('CT failure during transfer differs')
    q=sample(.3,'regular')
    expected={'grid-cb.open_attempts':2 if scenario=='repeat_inhibited' else 1,'grid-cb.close_attempts':2 if scenario in ('retransfer','repeat_inhibited') else 0,
        'reserve-cb.close_attempts':0 if scenario=='reserve_pt_before_request' else 1,
        'reserve-cb.open_attempts':int(scenario in ('ct_during_transfer','retransfer','repeat_inhibited')),
        'reserve-cb.manual_inhibit':int(scenario in ('retransfer','repeat_inhibited'))}
    if any(q[k]!=v for k,v in expected.items()):raise ValueError('actuator counters/operator inhibition differ')
    if q['source.connected']!=int(scenario=='retransfer') or q['g.connected']!=0:raise ValueError('final source configuration differs')
    if scenario in ('retransfer','repeat_inhibited'):
        q=sample(.24,'regular')
        if q['source.connected']!=1 or q['g.connected']!=0 or q['ats.latched']!=0 or q['reserve-cb.manual_inhibit']!=1:raise ValueError('return/reset bypassed operator isolation')
    if scenario=='pt_retry':
        q=sample(.077,'post_event')
        if q['ats.progress']!=0 or q['ats.latched']!=0 or q['ats.active']!=1:raise ValueError('retry reset/rearm differs')


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
    if ref.transfer:
        for left,right,solution,ctx in ref.segments:
            x,y=solution(left),solution(right);a=ref.transfer_address;p=ref.transfer
            expected=x[a] if ctx['ats_latched'] else x[a]+(right-left)/p.detection_delay_s if ctx['ats_active'] else x[a]*np.exp(-(right-left)/p.detection_delay_s)
            error=abs(y[a]-expected);maximum=max(maximum,error);segments+=1
            if error>2e-8:raise ValueError('transfer clock integral/reset differs')
    return segments,maximum


def accept(source, out):
    root=Path(source);anchor=verify(root);s=read_json(root/'summary.json');declared=list(jobs());fp=package_fingerprint()
    if not s['passed'] or s['code_sha256']!=fp or s['producers']!=producers(): raise ValueError('producer/runtime mismatch')
    if [r['id'] for r in s['rows']]!=[j['id'] for j in declared]: raise ValueError('owner inventory differs')
    replays=comparisons=energy_checks=spectra=0
    max_ref_energy=max_native_energy=max_event_refinement=max_signal_refinement=0.
    totals={k:0 for k in ('transfer_starts','early_blocks','reserve_closes','close_blocks','grid_returns','relay_trips','pt_losses')};max_sync_error={};clock_segments=0;max_clock_error=0.
    for job in declared:
        child=root/job['id'];verify(child)
        refs=[TransferLifecycleReference(job['study']).run(max_step=.002),
              TransferLifecycleReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
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
                    if not indices:
                        epsilon=64*np.finfo(float).eps*max(1.,abs(t))
                        indices=[i for i,(time,s) in enumerate(zip(report['time_s'],report['sample_side'])) if abs(time-t)<=epsilon and s==side]
                    if not indices and side=='regular':
                        indices=[i for i,(time,s) in enumerate(zip(report['time_s'],report['sample_side'])) if abs(time-t)<1e-12 and s in ('initial','post_event')]
                    if len(indices)!=1: raise ValueError('missing/ambiguous opening sample')
                    return {k:v[indices[0]] for k,v in report['channels'].items()}
                physical_gate(sample,report['event_log'],job['scenario'])
                max_native_energy=max(max_native_energy,native_error(report));energy_checks+=1
                audit=report['operating_point'];joint=job['mode']=='equilibrium'
                voltage_audit=audit['initial_voltage_constraints']['g']
                if voltage_audit['kind']!='standby_internal_voltage_target' or 'requested_PV_voltage_pu' in voltage_audit or abs(voltage_audit['solved_voltage_pu']-voltage_audit['requested_internal_voltage_pu'])>1e-12 or abs(voltage_audit['solved_bus_voltage_pu']-report['channels']['g.bus_voltage_pu'][0])>1e-12:raise ValueError('standby initial voltage audit differs')
                key='full_physical_spectral_abscissa_per_s' if joint else 'connected_machine_spectral_abscissa_per_s'
                for r in refs:
                    if abs(audit[key]-r.initial['energy_spectral_abscissa_per_s'])>2e-6: raise ValueError('initial spectral abscissa differs')
                    if joint:
                        a=np.array(audit['physical_eigenvalues_per_s']);b=np.array(r.initial['energy_eigenvalues_per_s'])
                        if a.shape!=b.shape: raise ValueError('initial spectrum dimension differs')
                        cost=abs((a[:,0]+1j*a[:,1])[:,None]-(b[:,0]+1j*b[:,1])[None,:]);ri,ci=linear_sum_assignment(cost)
                        if np.max(cost[ri,ci])>5e-6: raise ValueError('initial physical spectrum differs')
                spectra+=1;comparisons+=2
    for scenario,modes in [('pt_retry',('passport','hot')),('retransfer',('passport','hot'))]:
        for mode in modes:
            for method in ('radau','network_dae'):
                for grid in (0,1):
                    a,b=[read_json(root/(mode+'-'+scenario+suffix)/(method+'-'+str(grid))/'trace.json') for suffix in ('','-reversed')]
                    if any(a[k]!=b[k] for k in ('time_s','sample_side','event_log','channels')):raise ValueError('equipment/event permutation changed result')
    if replays!=36 or comparisons!=144 or energy_checks!=72 or totals!={'transfer_starts':96,'early_blocks':24,'reserve_closes':32,'close_blocks':64,'grid_returns':24,'relay_trips':8,'pt_losses':40}:
        raise ValueError('coverage inventory differs')
    if verify(root)!=anchor: raise ValueError('source changed')
    target=Path(out);target.mkdir(parents=True,exist_ok=False)
    result=dict(passed=True,code_sha256=fp,source_manifest_sha256=anchor,independent_reference_replays=replays,
        recomputed_comparisons=comparisons,native_integral_energy_checks=energy_checks,
        maximum_native_integral_error_j=max_native_energy,maximum_reference_integral_error_j=max_ref_energy,
        maximum_event_refinement_error_s=max_event_refinement,maximum_signal_refinement_gate_ratio=max_signal_refinement,
        initial_spectra_checked=spectra,native_protection_actions=totals,reference_clock_segments_checked=clock_segments,maximum_clock_integral_error=max_clock_error,maximum_synchronism_errors=max_sync_error,permutation_pairs=16,independent_host=False)
    (target/'summary.json').write_bytes(encoded(result));seal(target)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',required=True)
    print(accept(**vars(p.parse_args())))
