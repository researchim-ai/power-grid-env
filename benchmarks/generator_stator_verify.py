"""Offline consumer: replay independent equations and recompute every comparison."""
from pathlib import Path
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.generator_stator_cases import jobs
from benchmarks.generator_stator_reference import GeneratorStatorReference
from benchmarks.generator_stator_acceptance import compare


def accept(source,out):
    root=Path(source);anchor=verify(root);summary=read_json(root/'summary.json');declared=list(jobs())
    if not summary['passed'] or summary['code_sha256']!=package_fingerprint():raise ValueError('runtime/qualification mismatch')
    if any(digest(Path(p).read_bytes())!=h for p,h in summary['producers'].items()):raise ValueError('producer mismatch')
    if [r['id'] for r in summary['rows']]!=[r['id'] for r in declared]:raise ValueError('owner inventory mismatch')
    comparisons=0;replays=0;max_power_refinement=0.;max_reference_energy_error=0.;max_native_energy_error=0.;energy_checks=0
    for job in declared:
        child=root/job['id'];verify(child)
        refs=[GeneratorStatorReference(job['study']).run(max_step=.002),GeneratorStatorReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
        for name,ref in zip(['reference','refined-reference'],refs):
            stored=read_json(child/(name+'.json'))
            if encoded(stored['events'])!=encoded(ref.events) or encoded(stored['initialization'])!=encoded(ref.initial):raise ValueError('independent event/init replay differs')
            for point in stored['regular_values']:
                if encoded(point['channels'])!=encoded(ref.sample(point['time_s'])):raise ValueError('independent numerical replay differs')
            from scipy.integrate import quad
            def stored_energy(t):
                q=ref.sample(t);return q['g.energy.kinetic_energy_j']+q['g.energy.thermal_energy_j']
            def power(t):
                q=ref.sample(t);return q['g.energy.shaft_power_w']-q['g.energy.terminal_power_w']-q['g.energy.signed_damping_exchange_w']-q['g.energy.ambient_heat_w']
            cuts=sorted({0.,ref.w.s.duration_s,*[e['time_s'] for e in ref.events]})
            integral=sum(quad(power,l,h,epsabs=1e-5)[0] for l,h in zip(cuts,cuts[1:]))
            err=abs(stored_energy(cuts[-1])-stored_energy(0.)-integral);max_reference_energy_error=max(max_reference_energy_error,err)
            if err>1e-4:raise ValueError('independent integral energy balance failed')
            replays+=1
        for point in read_json(child/'reference.json')['regular_values']:
            a,b=[r.sample(point['time_s']) for r in refs]
            for k in a:
                error=abs(a[k]-b[k]);limit=1e-4 if k.endswith(('_w','_var','_j','.current_a','.measurement')) else 1e-7
                if error>limit:raise ValueError('independent refinement failed: '+k)
                if k.endswith(('_w','_var')):max_power_refinement=max(max_power_refinement,error)
        for method in ['radau','network_dae']:
            for grid in [0,1]:
                leaf=child/(method+'-'+str(grid));verify(leaf);study=read_json(leaf/'study.json');owner=job['study'].copy()
                import copy
                owner=copy.deepcopy(owner);owner['coupled'].update(integration_method=method,step_s=.002/2**grid)
                from power_grid_env.study_spec import StudySpec
                if study!=StudySpec.model_validate(owner).model_dump(mode='json'):raise ValueError('native study owner mismatch')
                report=read_json(leaf/'trace.json');checks=[compare(r,report) for r in refs]
                import numpy as np
                from scipy.optimize import linear_sum_assignment
                audit=report['operating_point'];joint=job['study']['coupled']['operating_point']['winding_state']=='equilibrium'
                key='full_physical_spectral_abscissa_per_s' if joint else 'connected_machine_spectral_abscissa_per_s'
                if not report['additional_validation']['ok']:raise ValueError('native controller gates failed')
                for ref in refs:
                    if abs(audit[key]-ref.initial['energy_spectral_abscissa_per_s'])>2e-6:raise ValueError('controlled spectrum abscissa differs')
                    if joint:
                        if audit['maximum_stationary_heat_error_w']>1e-5 or audit['checked_dynamic_residual_per_s']>1e-8:raise ValueError('physical stationary gate failed')
                        eig=np.array(audit['physical_eigenvalues_per_s']);oracle=np.array(ref.initial['energy_eigenvalues_per_s'])
                        dimension=11+ref.control_count
                        if eig.shape!=(dimension,2) or oracle.shape!=(dimension,2):raise ValueError('physical spectrum dimension differs')
                        cost=abs((eig[:,0]+1j*eig[:,1])[:,None]-(oracle[:,0]+1j*oracle[:,1])[None,:]);ri,ci=linear_sum_assignment(cost)
                        if np.max(cost[ri,ci])>5e-6:raise ValueError('physical spectrum differs')
                if joint and not job['fault']:
                    for k in ['m.speed_pu','m.stator_temperature_c','m.rotor_temperature_c','g.omega']:
                        if np.ptp(report['channels'][k])>1e-8:raise ValueError('stationary hold drift')
                for group,parameter,signal,lo,hi in [('TGOV1','governor','valve_state_pu','VMIN','VMAX'),('SEXS','exciter','field_state_pu','EMIN','EMAX')]:
                    p=job['study']['coupled']['machines'][0].get(parameter)
                    if p:
                        values=report['channels']['g.'+group+'.'+signal]
                        if min(values)<=p[lo]+1e-4 or max(values)>=p[hi]-1e-4:raise ValueError('declared interior profile hit a limit')
                from scipy.integrate import simpson
                channels=report['channels']
                if max(abs(v) for v in channels['g.stator.power_balance_w'])>1e-7 or max(abs(a-b) for a,b in zip(channels['g.stator.copper_electrical_loss_w'],channels['g.heat.copper_loss_w']))>1e-7:raise ValueError('electrical copper/heat partition differs')
                times=np.array(report['time_s']);sides=report['sample_side'];cuts=sorted({0.,times[-1],*[e['time_s'] for e in report['event_log']]})
                power=np.array(channels['g.energy.shaft_power_w'])-channels['g.energy.terminal_power_w']-np.array(channels['g.energy.signed_damping_exchange_w'])-channels['g.energy.ambient_heat_w']
                integral=0.
                for left,right in zip(cuts,cuts[1:]):
                    indices=[j for j,t in enumerate(times) if left<t<right or (t==left and sides[j] in ('initial','post_event')) or (t==right and sides[j] in ('regular','pre_event'))]
                    tt=times[indices]
                    if len(tt)<3 or np.any(np.diff(tt)<=0):raise ValueError('invalid energy quadrature axis')
                    integral+=simpson(power[indices],x=tt)
                energy=np.array(channels['g.energy.kinetic_energy_j'])+channels['g.energy.thermal_energy_j'];err=abs(energy[-1]-energy[0]-integral)
                max_native_energy_error=max(max_native_energy_error,err);energy_checks+=1
                if err>.1 or max(abs(v) for v in channels['g.energy.balance_residual_w'])>1e-7:raise ValueError('native integral energy balance failed')
                if not report['completed'] or not all(c['passed'] for c in checks) or encoded(checks)!=encoded(read_json(leaf/'comparison.json')):raise ValueError('comparison differs')
                comparisons+=2
    if comparisons!=32 or replays!=8 or verify(root)!=anchor:raise ValueError('coverage/evidence mismatch')
    from benchmarks.generator_stator_cases import refusals
    from power_grid_env.study_spec import StudySpec,run_study
    import copy
    count=0
    for job in refusals():
        for method in ['radau','network_dae']:
            leaf=root/(job['id']+'-'+method);verify(leaf)
            study=copy.deepcopy(job['study']);study['coupled']['integration_method']=method;owner=StudySpec.model_validate(study)
            if owner.model_dump(mode='json')!=read_json(leaf/'study.json'):raise ValueError('negative owner differs')
            old=read_json(leaf/'trace.json');new=run_study(owner)
            if new['completed'] or any(new.get(k)!=old.get(k) for k in ['status','reason','operating_point']):raise ValueError('refusal differs')
            count+=1
    if count!=6:raise ValueError('negative coverage differs')
    target=Path(out);target.mkdir(parents=True,exist_ok=False)
    result=dict(passed=True,source_manifest_sha256=anchor,code_sha256=package_fingerprint(),native_integral_energy_checks=energy_checks,maximum_native_integral_error_j=max_native_energy_error,maximum_reference_integral_error_j=max_reference_energy_error,full_physical_spectra_checked=8,stationary_hold_runs=4,interior_controlled_runs=16,native_refusal_replays=count,independent_reference_replays=replays,recomputed_comparisons=comparisons,maximum_power_refinement_error_w_var=max_power_refinement,independent_host=False)
    (target/'summary.json').write_bytes(encoded(result));seal(target);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',required=True);print(accept(**vars(p.parse_args())))
