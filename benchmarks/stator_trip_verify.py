"""Offline independent consumer of limiter references, owners and comparisons."""
from pathlib import Path
import copy
import numpy as np
from power_grid_env.study_artifacts import read_json,encoded,seal,verify
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_spec import StudySpec
from benchmarks.stator_trip_cases import jobs
from benchmarks.stator_trip_reference import StatorTripReference
from benchmarks.stator_trip_acceptance import producers,compare,coverage,event_key


def accept(source,out):
    root=Path(source);anchor=verify(root);summary=read_json(root/'summary.json');declared=list(jobs());fp=package_fingerprint()
    if not summary['passed'] or summary['code_sha256']!=fp or summary['producers']!=producers():raise ValueError('producer/runtime mismatch')
    if [r['id'] for r in summary['rows']]!=[j['id'] for j in declared]:raise ValueError('owner inventory mismatch')
    comparisons=replays=limiter_events=held_segments=0;maximum_event_refinement=0.;maximum_power_refinement=0.;max_ref_energy=0.;max_native_energy=0.;energy_checks=0
    for job in declared:
        child=root/job['id'];verify(child)
        refs=[StatorTripReference(job['study']).run(max_step=.002),StatorTripReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
        for name,ref in zip(['reference','refined-reference'],refs):
            data=read_json(child/(name+'.json'))
            if encoded(data['initialization'])!=encoded(ref.initial) or encoded(data['events'])!=encoded(ref.events):raise ValueError('reference init/events differ')
            coverage(ref.events,job['controls'])
            for point in data['regular_values']:
                if encoded(point['channels'])!=encoded(ref.sample(point['time_s'])):raise ValueError('reference sample differs')
            for point in data['one_sided_values']:
                if encoded(point['channels'])!=encoded(ref.sample(point['time_s'],point['side'])):raise ValueError('one-sided reference differs')
            trip=next(e['time_s'] for e in ref.events if e['kind']=='generator_thermal_trip')
            for time in np.linspace(trip,ref.w.s.duration_s,21):
                values=ref.sample(float(time),'post_event')
                expected=ref.replica.trip_level*np.exp(-(time-trip)/ref.replica.time_constant_s)
                if abs(values['g.stator_thermal_level']-expected)>1e-8 or any(values[k]!=0. for k in ['g.current_pu','g.p_w','g.q_var','g.connected','g.stator.copper_electrical_loss_w','g.heat.copper_loss_w']):raise ValueError('posttrip current/cooling differs')
            pre,post=[ref.sample(trip,side) for side in ['pre_event','post_event']]
            for k in ['m.speed_pu','m.stator_temperature_c','m.rotor_temperature_c','g.delta','g.omega','g.stator_thermal_level','g.heat.temperature_c','g.energy.kinetic_energy_j','g.energy.thermal_energy_j']:
                if abs(pre[k]-post[k])>1e-12:raise ValueError('differential state jumped at opening')
            from scipy.integrate import quad
            def energy(t):
                q=ref.sample(t);return q['g.energy.kinetic_energy_j']+q['g.energy.thermal_energy_j']
            def power(t):
                q=ref.sample(t);return q['g.energy.shaft_power_w']-q['g.energy.terminal_power_w']-q['g.energy.signed_damping_exchange_w']-q['g.energy.ambient_heat_w']
            cuts=sorted({0.,ref.w.s.duration_s,*[e['time_s'] for e in ref.events]})
            integral=sum(quad(power,left,right,epsabs=1e-5)[0] for left,right in zip(cuts,cuts[1:]))
            err=abs(energy(cuts[-1])-energy(0)-integral);max_ref_energy=max(max_ref_energy,err)
            if err>1e-4:raise ValueError('independent combined energy integral failed')
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
                from scipy.integrate import simpson
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
                channels=report['channels'];times=np.array(report['time_s']);sides=report['sample_side']
                event=next(e for e in report['event_log'] if e['kind']=='generator_thermal_trip');threshold=job['study']['coupled']['generator_thermal'][0]['trip_level']
                if event['threshold']!=threshold or abs(event['level']-threshold)>2e-7:raise ValueError('trip threshold metadata differs')
                if max(abs(v) for v in channels['g.stator.power_balance_w'])>1e-7 or max(abs(a-b) for a,b in zip(channels['g.stator.copper_electrical_loss_w'],channels['g.heat.copper_loss_w']))>1e-7:raise ValueError('copper partition differs')
                power=np.array(channels['g.energy.shaft_power_w'])-channels['g.energy.terminal_power_w']-np.array(channels['g.energy.signed_damping_exchange_w'])-channels['g.energy.ambient_heat_w'];cuts=sorted({0.,times[-1],*[e['time_s'] for e in report['event_log']]});integral=0.
                for left,right in zip(cuts,cuts[1:]):
                    indices=[j for j,t in enumerate(times) if left<t<right or (t==left and sides[j] in ('initial','post_event')) or (t==right and sides[j] in ('regular','pre_event'))]
                    tt=times[indices]
                    if len(tt)<3 or np.any(np.diff(tt)<=0):raise ValueError('invalid energy quadrature axis')
                    integral+=simpson(power[indices],x=tt)
                stored=np.array(channels['g.energy.kinetic_energy_j'])+channels['g.energy.thermal_energy_j'];err=abs(stored[-1]-stored[0]-integral);max_native_energy=max(max_native_energy,err);energy_checks+=1
                if err>.1 or max(abs(v) for v in channels['g.energy.balance_residual_w'])>1e-7:raise ValueError('native combined energy integral failed')
                coverage(report['event_log'],job['controls']);limiter_events+=sum(e['kind']=='generator_thermal_trip' for e in report['event_log']);comparisons+=2
    if replays!=12 or comparisons!=48 or limiter_events!=24 or verify(root)!=anchor:raise ValueError('coverage/evidence changed')
    target=Path(out);target.mkdir(parents=True,exist_ok=False)
    result=dict(passed=True,code_sha256=fp,source_manifest_sha256=anchor,independent_reference_replays=replays,recomputed_comparisons=comparisons,
      full_initial_spectra_checked=12,frozen_initial_spectra_checked=12,native_integral_energy_checks=energy_checks,maximum_native_integral_error_j=max_native_energy,maximum_reference_integral_error_j=max_ref_energy,native_generator_trips=limiter_events,posttrip_cooling_points_checked=252,maximum_event_refinement_error_s=maximum_event_refinement,
      maximum_power_refinement_error_w_var=maximum_power_refinement,independent_host=False)
    (target/'summary.json').write_bytes(encoded(result));seal(target);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',required=True);print(accept(**vars(p.parse_args())))
