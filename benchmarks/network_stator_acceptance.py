"""Sealed, bounded OC/actuator/I2/limiter qualification; no external services."""
import copy
from pathlib import Path

from benchmarks.generator_stator_acceptance import compare as waveform_compare
from benchmarks.network_stator_reference import NetworkStatorReference
from power_grid_env.study_artifacts import encoded, seal, digest


def event_key(e):
    return tuple(e.get(k) for k in ('kind','equipment_id','controller','bound','value',
                                   'outcome','cause','breaker_id','threshold'))


def compare(reference, report):
    if not report.get('completed'):
        return dict(passed=False, incomplete_native=True, solver_message=report.get('solver_message'))
    if [event_key(e) for e in reference.events] != [event_key(e) for e in report['event_log']]:
        return dict(passed=False, event_identities_match=False)
    result = waveform_compare(reference, report)
    metadata = all(abs(a[k]-b.get(k, float('inf'))) <= 2e-7
                   for a,b in zip(reference.events,report['event_log'])
                   for k in ('due_s','level') if k in a)
    result['event_metadata_match'] = metadata
    result['passed'] = result['passed'] and metadata
    return result


def coverage(events, scenario):
    def one(kind):
        rows = [e for e in events if e['kind']==kind]
        if len(rows)!=1: raise ValueError('expected one '+kind)
        return rows[0]
    pickup, trip, request, opening, dropout = [one(k) for k in (
        'network_relay_pickup','network_relay_trip','breaker_open_requested',
        'breaker_open','network_relay_dropout')]
    if any(e['equipment_id']!='primary-oc' for e in (pickup,trip,dropout)):
        raise ValueError('relay identity differs')
    if trip['breaker_id']!='primary-cb' or request['cause']!='relay:primary-oc' or opening['cause']!=request['cause']:
        raise ValueError('actuator causality differs')
    if abs(trip['time_s']-pickup['time_s']-.005)>2e-7 or trip['time_s']!=request['time_s']:
        raise ValueError('definite clock delay differs')
    delay = .08 if scenario=='thermal_first' else .005
    if abs(request['due_s']-request['time_s']-delay)>1e-12 or abs(opening['time_s']-request['due_s'])>2e-10:
        raise ValueError('actuator deadline differs')
    thermal = [e for e in events if e['kind']=='generator_thermal_trip']
    failures = [e for e in events if e['kind']=='breaker_failure_confirmed']
    expected = dict(relay_first='applied',thermal_first='already_in_requested_state',failed_open='failed_to_operate')[scenario]
    if opening['outcome']!=expected or len(thermal)!=(scenario!='relay_first') or len(failures)!=(scenario=='failed_open'):
        raise ValueError('race outcome differs')
    if thermal and (thermal[0]['threshold']!=1.8 or abs(thermal[0]['level']-1.8)>2e-7):
        raise ValueError('I2 threshold differs')
    disconnected = opening['time_s'] if scenario=='relay_first' else thermal[0]['time_s']
    if dropout['time_s']!=disconnected: raise ValueError('dropout did not follow opening')
    if scenario=='thermal_first' and not trip['time_s']<disconnected<opening['time_s']:
        raise ValueError('stale command ordering differs')
    if scenario=='failed_open' and not opening['time_s']<failures[0]['time_s']<disconnected:
        raise ValueError('failed actuator ordering differs')
    if failures and abs(failures[0]['time_s']-opening['time_s']-.02)>2e-10:
        raise ValueError('breaker failure timer differs')
    for group in ('TGOV1','SEXS'):
        if not any(e.get('controller')==group and e['kind']=='controller_limit_enter' for e in events):
            raise ValueError('missing controller saturation')
    return dict(disconnected_s=disconnected,outcome=expected,thermal_trips=len(thermal),
                failure_confirmations=len(failures),open_attempts=int(scenario!='thermal_first'))


def producers():
    from benchmarks.hybrid_stator_acceptance import producers as inherited
    result = inherited()
    for p in ('benchmarks/network_stator_cases.py','benchmarks/network_stator_reference.py',
              'benchmarks/network_stator_acceptance.py'):
        result[p] = digest(Path(p).read_bytes())
    return result


def reference_packet(r):
    times = [i*.001 for i in range(501) if all(abs(i*.001-e['time_s'])>2e-7 for e in r.events)]
    return dict(initialization=r.initial,events=r.events,
        regular_values=[dict(time_s=t,channels=r.sample(t)) for t in times],
        one_sided_values=[dict(time_s=t,side=side,channels=r.sample(t,side))
            for t in sorted({e['time_s'] for e in r.events}) for side in ('pre_event','post_event')])


def execute(job, root):
    from power_grid_env.study_spec import StudySpec, run_study
    root.mkdir(parents=True,exist_ok=False)
    refs = [NetworkStatorReference(job['study']).run(max_step=.002),
            NetworkStatorReference(job['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
    for r in refs: coverage(r.events,job['scenario'])
    rows = []
    for method in ('radau','network_dae'):
        for grid in (0,1):
            s=copy.deepcopy(job['study']);s['coupled'].update(integration_method=method,step_s=.002/2**grid)
            owner=StudySpec.model_validate(s);report=run_study(owner)
            if not report.get('completed'):
                (root/('incomplete-'+method+'-'+str(grid)+'.json')).write_bytes(encoded(report))
                raise ValueError('native integration failed: '+str(report.get('solver_message',report.get('reason'))))
            checks=[compare(r,report) for r in refs]
            effects=coverage(report['event_log'],job['scenario'])
            leaf=root/(method+'-'+str(grid));leaf.mkdir()
            for name,value in [('study',owner.model_dump(mode='json')),('trace',report),('comparison',checks)]:
                (leaf/(name+'.json')).write_bytes(encoded(value))
            seal(leaf)
            rows.append(dict(method=method,grid=grid,passed=report['completed'] and report['additional_validation']['ok'] and all(c['passed'] for c in checks),coverage=effects))
    for name,r in zip(('reference','refined-reference'),refs):
        (root/(name+'.json')).write_bytes(encoded(reference_packet(r)))
    result=dict(id=job['id'],passed=all(r['passed'] for r in rows),rows=rows)
    (root/'summary.json').write_bytes(encoded(result));seal(root)
    print(job['id'],result['passed'],flush=True)
    return result


def qualify(out):
    from benchmarks.network_stator_cases import jobs
    from power_grid_env.evidence import package_fingerprint
    from concurrent.futures import ProcessPoolExecutor
    root=Path(out);root.mkdir(parents=True,exist_ok=False);fp=package_fingerprint();hashes=producers()
    declared=list(jobs())
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(execute,j,root/j['id']) for j in declared]
        rows=[f.result() for f in futures]
    result=dict(passed=len(rows)==8 and all(r['passed'] for r in rows),physical_cases=8,
        native_runs=32,independent_trajectories=16,comparisons=64,code_sha256=fp,producers=hashes,rows=rows,
        scope='Authored balanced radial generator-port definite OC, delayed opening, I2 race and failed-open confirmation; stator R(T), physical heat/energy and TGOV1/SEXS limits. Source/line connected; no backups, islands, reclose, UV, OEM or arbitrary simultaneous roots.')
    if fp!=package_fingerprint() or hashes!=producers(): raise ValueError('producer changed')
    (root/'summary.json').write_bytes(encoded(result));seal(root)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True)
    if not qualify(**vars(p.parse_args()))['passed']: raise SystemExit(2)
