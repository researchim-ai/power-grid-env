"""Independent hybrid controller limiter qualification for authored R(T) studies."""
import copy
from pathlib import Path
from benchmarks.generator_stator_acceptance import compare as waveform_compare


def event_key(e):
    return (e['kind'],e['equipment_id'],e.get('controller'),e.get('bound'),e.get('value'),e.get('outcome'))


def compare(reference,report):
    if [event_key(e) for e in reference.events]!=[event_key(e) for e in report['event_log']]:
        return dict(passed=False,event_identities_match=False,expected_events=reference.events,actual_events=report['event_log'])
    return waveform_compare(reference,report)


def coverage(events,controls):
    trips=[e for e in events if e['kind']=='generator_thermal_trip']
    if len(trips)!=1 or trips[0]['outcome']!='latched_electrical_open':raise ValueError('expected one latched generator trip')
    if [e['kind'] for e in events if e['kind'].startswith('fault_')]!=['fault_apply','fault_clear']:raise ValueError('fault sequence differs')
    return dict(trip_time_s=trips[0]['time_s'])


def producers():
    from power_grid_env.study_artifacts import digest
    paths=['benchmarks/stator_trip_cases.py','benchmarks/stator_trip_reference.py','benchmarks/stator_trip_acceptance.py','benchmarks/generator_stator_reference.py','benchmarks/generator_stator_cases.py','benchmarks/generator_stator_acceptance.py','benchmarks/generator_energy_reference.py','benchmarks/generator_heat_reference.py','benchmarks/generator_trip_cases.py','benchmarks/generator_trip_reference.py','benchmarks/generator_trip_acceptance.py','benchmarks/generator_replica_cases.py','benchmarks/generator_replica_reference.py','benchmarks/limiter_winding_cases.py','benchmarks/limiter_winding_reference.py','benchmarks/limiter_winding_acceptance.py',
      'benchmarks/controlled_winding_cases.py','benchmarks/controlled_winding_reference.py','benchmarks/mixed_equilibrium_cases.py',
      'benchmarks/mixed_equilibrium_reference.py','benchmarks/mixed_winding_cases.py','benchmarks/mixed_winding_reference.py',
      'benchmarks/two_cage_winding_reference.py','benchmarks/rms_winding_reference.py','benchmarks/motor_only_loaded_reference.py',
      'benchmarks/mixed_winding_acceptance.py','benchmarks/rms_winding_acceptance.py','benchmarks/loaded_protection_winding_acceptance.py']
    return {p:digest(Path(p).read_bytes()) for p in paths}


def execute(job,root):
    from benchmarks.stator_trip_reference import StatorTripReference
    from power_grid_env.study_spec import StudySpec,run_study
    from power_grid_env.study_artifacts import encoded,seal
    root.mkdir(parents=True,exist_ok=False)
    refs=[StatorTripReference(job['study']).run(max_step=.002),StatorTripReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
    for r in refs:coverage(r.events,job['controls'])
    rows=[]
    for method in ['radau','network_dae']:
        for grid in [0,1]:
            s=copy.deepcopy(job['study']);s['coupled'].update(integration_method=method,step_s=.002/2**grid)
            owner=StudySpec.model_validate(s);report=run_study(owner);checks=[compare(r,report) for r in refs]
            effects=coverage(report['event_log'],job['controls'])
            child=root/(method+'-'+str(grid));child.mkdir()
            for name,value in [('study',owner.model_dump(mode='json')),('trace',report),('comparison',checks)]:
                (child/(name+'.json')).write_bytes(encoded(value))
            seal(child);rows.append(dict(method=method,grid=grid,passed=report['completed'] and report['additional_validation']['ok'] and all(c['passed'] for c in checks),coverage=effects))
    for name,r in zip(['reference','refined-reference'],refs):
        times=[i*.001 for i in range(401) if all(abs(i*.001-e['time_s'])>2e-7 for e in r.events)]
        data=dict(initialization=r.initial,events=r.events,regular_values=[dict(time_s=t,channels=r.sample(t)) for t in times],
            one_sided_values=[dict(time_s=t,side=side,channels=r.sample(t,side)) for t in sorted({e['time_s'] for e in r.events}) for side in ['pre_event','post_event']])
        (root/(name+'.json')).write_bytes(encoded(data))
    result=dict(id=job['id'],passed=all(r['passed'] for r in rows),rows=rows)
    (root/'summary.json').write_bytes(encoded(result));seal(root);print(job['id'],result['passed'],flush=True);return result


def qualify(out):
    from benchmarks.stator_trip_cases import jobs
    from power_grid_env.study_artifacts import encoded,seal
    from power_grid_env.evidence import package_fingerprint
    root=Path(out);root.mkdir(parents=True,exist_ok=False);fp=package_fingerprint();hashes=producers();rows=[execute(j,root/j['id']) for j in jobs()]
    result=dict(passed=len(rows)==6 and all(r['passed'] for r in rows),physical_cases=6,native_runs=24,independent_trajectories=12,comparisons=48,code_sha256=fp,producers=hashes,rows=rows,
      scope='Authored radial GENCLS stator R(T),physical generator heat,energy closure,two-cage motor R(T),TGOV1/SEXS/both and one permanent I2 generator trip before/during finite fault. Source remains connected;no reclose/islands/OC/UV/OEM or global hybrid stability qualification.')
    if fp!=package_fingerprint() or hashes!=producers():raise ValueError('producer changed')
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);r=qualify(**vars(p.parse_args()))
    if not r['passed']:raise SystemExit(2)
