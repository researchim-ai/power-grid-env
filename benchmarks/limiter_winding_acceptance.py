"""Independent hybrid controller limiter qualification for authored R(T) studies."""
import copy
from pathlib import Path
from benchmarks.mixed_winding_acceptance import compare as waveform_compare


def event_key(e):
    return (e['kind'],e['equipment_id'],e.get('controller'),e.get('bound'),e.get('value'),e.get('outcome'))


def compare(reference,report):
    if [event_key(e) for e in reference.events]!=[event_key(e) for e in report['event_log']]:
        return dict(passed=False,event_identities_match=False,expected_events=reference.events,actual_events=report['event_log'])
    return waveform_compare(reference,report)


def coverage(events,controls):
    result={}
    for group in (['TGOV1','SEXS'] if controls=='both' else ['TGOV1'] if controls=='governor' else ['SEXS']):
        own=[e for e in events if e.get('controller')==group]
        entries=[e['bound'] for e in own if e['kind']=='controller_limit_enter']
        releases=[e for e in own if e['kind']=='controller_limit_release']
        if entries!=['upper','lower'] or len(releases)!=2:raise ValueError('missing/reordered limiter excursion: '+group)
        result[group]=dict(upper_entries=1,lower_entries=1,releases=2)
    return result


def producers():
    from power_grid_env.study_artifacts import digest
    paths=['benchmarks/limiter_winding_cases.py','benchmarks/limiter_winding_reference.py','benchmarks/limiter_winding_acceptance.py',
      'benchmarks/controlled_winding_cases.py','benchmarks/controlled_winding_reference.py','benchmarks/mixed_equilibrium_cases.py',
      'benchmarks/mixed_equilibrium_reference.py','benchmarks/mixed_winding_cases.py','benchmarks/mixed_winding_reference.py',
      'benchmarks/two_cage_winding_reference.py','benchmarks/rms_winding_reference.py','benchmarks/motor_only_loaded_reference.py',
      'benchmarks/mixed_winding_acceptance.py','benchmarks/rms_winding_acceptance.py','benchmarks/loaded_protection_winding_acceptance.py']
    return {p:digest(Path(p).read_bytes()) for p in paths}


def execute(job,root):
    from benchmarks.limiter_winding_reference import LimiterReference
    from power_grid_env.study_spec import StudySpec,run_study
    from power_grid_env.study_artifacts import encoded,seal
    root.mkdir(parents=True,exist_ok=False)
    refs=[LimiterReference(job['study']).run(max_step=.002),LimiterReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
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
        times=[i*.001 for i in range(501) if all(abs(i*.001-e['time_s'])>2e-7 for e in r.events)]
        data=dict(initialization=r.initial,events=r.events,regular_values=[dict(time_s=t,channels=r.sample(t)) for t in times],
            one_sided_values=[dict(time_s=t,side=side,channels=r.sample(t,side)) for t in sorted({e['time_s'] for e in r.events}) for side in ['pre_event','post_event']])
        (root/(name+'.json')).write_bytes(encoded(data))
    result=dict(id=job['id'],passed=all(r['passed'] for r in rows),rows=rows)
    (root/'summary.json').write_bytes(encoded(result));seal(root);print(job['id'],result['passed'],flush=True);return result


def qualify(out):
    from benchmarks.limiter_winding_cases import jobs
    from power_grid_env.study_artifacts import encoded,seal
    from power_grid_env.evidence import package_fingerprint
    root=Path(out);root.mkdir(parents=True,exist_ok=False);fp=package_fingerprint();hashes=producers();rows=[execute(j,root/j['id']) for j in jobs()]
    result=dict(passed=len(rows)==8 and all(r['passed'] for r in rows),physical_cases=8,native_runs=32,independent_trajectories=16,comparisons=64,code_sha256=fp,producers=hashes,rows=rows,
      scope='Authored one-GENCLS/two-cage-R(T) radial pulse profile.TGOV1/SEXS/both,passport/joint motor heat;two-sided bounds,immediate and delayed releases;coincident command ordering;no OC/UV,topology changes,OEM or global hybrid stability qualification.')
    if fp!=package_fingerprint() or hashes!=producers():raise ValueError('producer changed')
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);r=qualify(**vars(p.parse_args()))
    if not r['passed']:raise SystemExit(2)
